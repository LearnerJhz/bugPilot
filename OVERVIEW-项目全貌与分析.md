# BugPilot —— 项目全貌与分析

> 本文是这个仓库的"总览 + 深度分析"文档：讲清楚它是什么、怎么设计的、好在哪、
> 差在哪、以及怎么对外介绍它。
>
> 配套文档：
> - `ARCHITECTURE-引擎驱动Agent说明.md` —— 架构设计的完整推演
> - `TODO-引擎驱动Agent改造.md` —— 路线 B 改造的任务清单与拍板结论
> - `跑测试案例.md` —— 手工跑 demo 的操作步骤

---

## 一、一句话定位

**BugPilot 是一个声明式的 AI 修 Bug 流水线引擎。**

它把"修一个 bug"这件事拆成 `intake → analyze → fix → apply → verify` 五个阶段，
用一份 YAML 清单声明流程，引擎按依赖拓扑序自动调度。核心设计是 **引擎驱动 Agent**：
引擎自己从不调用大模型，而是在需要"动脑"的阶段起一个 Claude Code 子进程，让它在目标
仓库里自走读码、改码、跑命令，写完产物后引擎回收、判卷；判卷不过就按配置回滚到上游
阶段、带着失败反馈重跑。

一句话概括它的价值主张：**把不确定的 LLM，包进确定的工程流程里。**

---

## 二、项目基本盘

| 维度 | 数据 |
|---|---|
| 语言 | Python 3（`from __future__ import annotations`，兼容 3.9+） |
| 代码量 | 20 个 `.py` 文件，2841 行（含内联测试与大量中文注释） |
| 第三方依赖 | 只有 **PyYAML** 一个（`core/manifest.py` 解析清单用），其余全标准库 |
| 外部依赖 | `claude` CLI（Claude Code headless），需在 PATH 中 |
| 开发周期 | 2026-07-21 ~ 2026-07-29，13 次提交 |
| 入口 | `python3 -m cli run <task_id>` |
| 工程配套 | 自研 45 行 `testkit.py` + 各模块内联测试 + `scripts/smoke.py` 端到端冒烟 |

单文件行数分布（`core/orchestrator.py` 是绝对核心）：

```
590  core/orchestrator.py     引擎主循环：Engine + Phase
262  core/ports.py            端口定义（全是 Protocol，零实现）
207  core/models.py           数据契约 + 状态机定义
194  cli/main.py              命令行入口
193  executors/agent_executor.py   AI 阶段通用执行器（prompt 编译 + 判卷）
189  adapters/agent_runner.py      起 claude 子进程
168  executors/intake.py
151  core/manifest.py         YAML → 带类型的 WorkflowManifest + 拓扑排序
135  adapters/run_state_store.py
134  adapters/fake_agent_runner.py  自测用假 Agent
128  executors/verify.py
106  executors/apply.py
 97  core/registry.py         执行器注册表
 85  scripts/smoke.py
 80  adapters/promote_store.py
 47  adapters/local_workspace.py
 45  testkit.py
```

---

## 三、它到底解决什么问题

### 3.1 直接问题

给定一句 bug 描述（或一个 issue URL）和一个代码仓库路径，自动完成：
定位根因 → 改代码 → 记录改动 → 跑测试验证 → 验证失败自动重来。

### 3.2 真正想解决的问题

这才是项目的立意所在。当下用 AI 修 bug 的主流做法是：人打开一个 Agent，聊天式地
让它改，改完自己看。这种方式有三个致命问题：

1. **流程不可控** —— Agent 想跳过哪步就跳过哪步，它可能压根没读代码就开始猜着改。
2. **过程不可审计** —— 事后只有一段聊天记录，没有"根因是什么、置信度多少、验证证据是什么"的结构化留痕。
3. **失败没有兜底** —— 改错了就是改错了，没有自动的"打回重来"机制。

BugPilot 的回答是：**把流程的控制权从模型手里拿回来，交给一段确定性的代码。**
模型降级成一个"被调度的执行单元"，每个阶段跑什么、什么算通过、不通过怎么办，
全部由引擎按声明式配置决定。

---

## 四、核心设计决策：路线 A vs 路线 B

这是整个项目最值得说的一次技术选型，`TODO-引擎驱动Agent改造.md` 里有完整记录。

### 路线 A（早期实现，已废弃）

引擎只负责拼 prompt，把它写进文件，同时写一份带"哨兵字符串"的占位产物，然后
**短路退出**，等外层的某个 LLM 来读 prompt、填产物、再重新拉起引擎。

```
引擎 → 写 prompt + 占位符 → 退出 → (外层 LLM 接管) → 人再拉起引擎 → ...
```

问题：控制权在外层。引擎变成了一个被动的"状态记录器"，流程能不能走完全看外面那个
Agent 的心情。而且需要人反复介入。

### 路线 B（当前实现）

引擎用 `subprocess` **亲自起** Agent 进程，阻塞 `wait()` 到它跑完，然后读回产物判卷。

```
引擎 → 编译 prompt → subprocess 起 claude -p → 阻塞等 → 读产物 → 判卷 → 过/回滚
```

控制权完全在引擎。代价是引擎必须处理子进程的一切异常（超时、崩溃、CLI 不存在、
跑完了但没写产物），换来的是全自动、可重试、可审计。

### 一个配套的关键决定：不解析 stdout

引擎**从不解析 Agent 的标准输出**。它在 prompt 里给出产物文件的**绝对路径**，硬性
要求 Agent 写进去；引擎只检查"文件在不在、内容里有没有约定的二级标题"。

```python
# executors/agent_executor.py  —— 注入 prompt 的硬约束
parts.append(f"1. 把本阶段结论写入产物文件（覆盖写）：`{artifact_path}`")
parts.append("2. 该产物**必须包含以下二级标题**（原文，勿翻译/改写，引擎按字符串校验）：")
```

这一手把"解析自然语言"这个不可靠通道，换成了"文件系统状态"这个完全可靠的信号。
`stdout`/`stderr` 只用来记日志和排障。

---

## 五、整体架构

### 5.1 分层图

严格的六边形架构（Ports & Adapters）。`core/` 是纯内核，不 import `subprocess`、
不知道 git 是什么、不碰文件系统（除了读 manifest）。

```
                        ┌─────────────────────────┐
                        │        cli/main.py      │  参数解析 + 打印进度 + 退出码
                        └────────────┬────────────┘
                                     │
        ┌────────────────────────────▼────────────────────────────┐
        │                     core/  （内核，无 IO）                │
        │                                                          │
        │   manifest.py    YAML → WorkflowManifest + 拓扑排序       │
        │   models.py      PhaseSpec / RunState / 三套状态枚举       │
        │   registry.py    executor 名字 → 类（@register 装饰器）    │
        │   ports.py       ★ 全是 Protocol，零实现                  │
        │   orchestrator.py ★ Engine（整条运行）+ Phase（单阶段四步） │
        └───┬──────────────────────────────────────────────┬───────┘
            │ 依赖倒置：内核只认接口                          │
   ┌────────▼─────────┐                          ┌──────────▼──────────┐
   │    executors/    │                          │      adapters/      │
   │  （实现 PhaseExecutor）                      │  （实现各 Port）      │
   │                  │                          │                     │
   │  intake     确定性 │                         │ local_workspace   工作区 │
   │  agent_executor   │                         │ promote_store     产物黑板│
   │    ├─ analyze  AI │                         │ run_state_store   状态JSON│
   │    └─ fix      AI │                         │ agent_runner    起claude │
   │  apply      确定性 │                         │ fake_agent_runner 自测桩 │
   │  verify     确定性 │                         └─────────────────────┘
   └──────────────────┘
```

### 5.2 目录职责速查

| 目录/文件 | 职责 |
|---|---|
| `manifest.yaml` | **唯一的流程声明**。改这里就能改流程，不用动代码 |
| `prompts/*.md` | 各 AI 阶段的提示词模板（`analyze.md`、`fix.md`） |
| `core/ports.py` | 所有接缝的接口定义：`Workspace` / `ArtifactStore` / `StateStore` / `AgentRunner` / `PhaseExecutor` |
| `core/orchestrator.py` | 引擎主循环。`Engine` 管阶段之间的调度，`Phase` 管一个阶段内的四步 |
| `executors/` | 每个阶段"拼什么 prompt / 判什么卷"。**不起进程、不调模型** |
| `adapters/` | 所有脏活：起子进程、读写文件、序列化 JSON |
| `tasks/<task_id>/` | 运行产物落盘处：`01-intake.md` ~ `05-verify.md` + `_prompt_*.md` + `run_state.json` |

---

## 六、一次完整运行发生了什么

以 `python3 -m cli run mytask --description "登录页空密码提交崩溃" --repo /path/to/repo` 为例。

### 6.1 装配阶段

`Engine.__init__` 一次性把"整条运行共享"的东西装好：

```python
self.manifest = load_manifest()          # 读 manifest.yaml
self.order = self.manifest.topo_order()  # Kahn 拓扑排序，得到合法执行序
PromoteStore.configure("tasks")          # 产物黑板基目录
RunStateStore.configure("tasks")         # 状态基目录
self.workspace = LocalWorkspace(repo)    # Agent 干活的目录
self.run_state = self.state_store.load(task_id) or RunState(task_id=task_id)  # 断点续跑的关键
self.agent = agent_runner or build_agent_runner(self.manifest.project)
```

### 6.2 主循环

```
Engine.run_pipeline():
  idx = 0
  while idx < len(拓扑序):
    ├─ 断点续跑检查：已 SUCCEEDED 且产物文件还在 → ⏭️ 跳过，idx++
    ├─ 依赖门：depends_on 里的阶段都 SUCCEEDED 了吗？否则标 SKIPPED，idx++
    ├─ phase = Engine._new_phase(spec)     ← ctx 由 Phase 在 __post_init__ 自己拼
    ├─ outcome = phase.run()               ← 固定四步（见下）
    ├─ 落盘 run_state.json
    ├─ PASSED → 清掉该阶段的重试反馈，idx++
    ├─ BLOCKED 且还有重试预算 → 记反馈 + 清产物 + idx 拨回 retry_on_fail.target
    └─ BLOCKED 且预算耗尽 → 整体 BLOCKED，退出码 20
```

### 6.3 单阶段的固定四步（`Phase.run()`）

无论 AI 阶段还是确定性阶段，都走同一条路径，差别只在第 ②③ 步：

```
① prepare(ctx)     executor 拼 prompt（AI 阶段）或直接算出最终产物（确定性阶段）
                   ↓ 靠 prep.prompt_text 是否为空来区分两类阶段
② 落盘 prompt      写 _prompt_<phase>.md，便于事后排查与复现
③ _produce()       AI 阶段：先删旧产物 → agent.run(prompt) 起 claude → 读回产物
                   确定性阶段：直接把 prep.artifact 写盘
④ _gate()          校验必需章节齐全 + 置信度达标 → PASSED / BLOCKED
```

第 ③ 步有两道健壮性检查，任一不满足直接 BLOCKED，不进判卷：

```python
if not run_res.ok:                                   # 3a 进程本身异常/超时
    return self._blocked(f"Agent 进程异常（exit={run_res.exit_code}）…")
if not self.store.exists(self.task_id, self.output): # 3b 跑完了但没写产物
    return self._blocked(f"Agent 跑完但没写出产物 '{self.output}'…")
```

**"先删旧产物"这个细节很重要**：它保证"跑完后产物存在"这件事严格等价于"这一轮 Agent
真的写了"，杜绝了上一轮的残留产物被误判成本轮成果。

### 6.4 完整时序（正常路径）

```
intake   [确定性]  把 description 规整成 01-intake.md            → gate: 有 ## Summary/## Inputs ✅
analyze  [AI]      起 claude 读码定位根因 → 02-analyze.md        → gate: 章节齐 + Confidence ≥ 0.6 ✅
fix      [AI]      起 claude 改代码 → 03-fix.md                  → gate: 有 ## Changes/## Self Check ✅
apply    [确定性]  git diff --stat 记账 → 04-apply.md            → gate: 有 ## Applied ✅
verify   [确定性]  跑 verify_command → 05-verify.md              → gate: 章节齐 + Verdict=pass ✅
                                                                  → RunStatus.SUCCEEDED，退出码 0
```

---

## 七、关键机制深挖

### 7.1 声明式流程：加一个阶段不用改引擎

`manifest.yaml` 是唯一的流程真源。加一个新阶段只需要两步：

1. 在 YAML 里加一条 phase 条目
2. 写一个类，用 `@register("新名字")` 装饰

`orchestrator` 永远不用改——它只按名字从注册表查 executor，对任何具体阶段一无所知。
这是开闭原则的教科书式落地。

```python
# core/registry.py
_REGISTRY: dict[str, type] = {}

def register(name: str) -> Callable[[type], type]:
    def decorator(cls: type) -> type:
        if name in _REGISTRY:
            raise ValueError(f"executor '{name}' already registered")
        _REGISTRY[name] = cls
        return cls
    return decorator
```

一个漂亮的副作用：`AgentExecutor` 一个类被注册了**两次**（`analyze` 和 `fix`），
两个阶段的全部差异（提示词、必需章节、置信度门、重试策略）都在 manifest 和
`prompts/*.md` 里，**代码零分叉**。

```python
@register("analyze")
@register("fix")
class AgentExecutor:
    ...
```

### 7.2 三层质量闸门

| 层 | 检查什么 | 在哪实现 | 强度 |
|---|---|---|---|
| 结构门 | 产物必须包含 manifest 声明的二级标题 | 各 executor 的 `gate()`，字符串包含匹配 | 弱，但极廉价 |
| 置信度门 | Agent 自评的 `## Confidence` ≥ 阈值 | `Phase._gate()` 读 `spec.confidence_min` | 中，可被"刷分" |
| 事实门 | 在真实工作区跑 `verify_command`，看退出码 | `executors/verify.py` | **强，骗不了** |

设计上的层次感很清楚：前两道是廉价的早期过滤，防止残缺产物往下游污染；真正兜底的
是 verify 那道——它不听 Agent 说什么，直接跑测试看退出码。

```python
# executors/verify.py —— gate 不只看章节，还看 Verdict 是不是 pass
verdict_pass = bool(re.search(r"##\s*Verdict\s*\n+\s*pass\b", artifact, re.IGNORECASE))
passed = (not missing) and verdict_pass
```

### 7.3 回滚重试：带反馈的状态机

配置长这样：

```yaml
- id: verify
  retry_on_fail:
    target: analyze      # verify 挂了，回到 analyze 重新定位根因
    max_retries: 2
```

引擎的动作：

```python
def _rollback(self, target_idx: int, current_idx: int) -> None:
    for i in range(target_idx, current_idx + 1):
        spec = self.order[i]
        self.store.delete(self.task_id, spec.output)            # 清产物
        self.store.delete(self.task_id, f"_prompt_{spec.id}.md")
        pr = self.run_state.phase_results.get(spec.id)
        if pr:
            pr.status = PhaseStatus.PENDING                     # 状态重置
            pr.finished_at = None
            pr.output_path = None
```

**注意这里刻意没有重置 `retry_count`** —— 重试预算必须是累计的，否则回滚会把计数
清零，导致无限循环。这是一个很容易写错、也很能体现是否想过边界的细节。

回滚不是简单重跑，而是**带着失败原因重跑**。引擎把"为什么没过"整理成一段反馈，
注入下一轮 prompt 的高优先级位置：

```python
def _make_feedback(spec, outcome, attempt, max_retries) -> str:
    lines = [
        f"上一轮在阶段 '{spec.id}' 未通过（第 {attempt}/{max_retries} 次重试）。原因：",
        f"- {outcome.message}",
    ]
    if outcome.missing_sections:
        lines.append(f"- 产物缺少必需章节：{outcome.missing_sections}")
    if outcome.confidence is not None and spec.confidence_min is not None:
        lines.append(f"- 置信度 {outcome.confidence} 低于阈值 {spec.confidence_min}，请换思路重新定位根因。")
    lines.append("请针对以上问题调整，不要重复上一轮的错误假设。")
    return "\n".join(lines)
```

在 prompt 里它被放在 `## ⚠️ 上一轮失败反馈（最高优先级，必须规避）` 标题下。

### 7.4 断点续跑

`tasks/<task_id>/run_state.json` 是整次运行的单一事实来源。每推进一个阶段就落盘一次，
所以进程中途被 Ctrl-C 掉，下次跑同一个 task 会自动跳过已完成的阶段：

```python
def _resume_skip(self, spec: PhaseSpec) -> bool:
    prev = self.run_state.phase_results.get(spec.id)
    if prev and prev.status == PhaseStatus.SUCCEEDED and self.store.exists(self.task_id, spec.output):
        self.log(f"⏭️  [{spec.id}] 已完成，跳过（resume）。")
        return True
    return False
```

判据是**双重的**：状态说成功了 **并且** 产物文件确实还在。只信状态会在产物被手工删掉
时出错，只信文件会在状态未落盘时出错。

### 7.5 模型无关

`ClaudeAgentRunner` 只负责起 `claude` 这个 CLI，**它完全不知道 DeepSeek 是什么**。
用哪个模型由环境变量决定：

```bash
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_AUTH_TOKEN=<key>
```

manifest 里只声明"起哪个 CLI、给什么护栏"：

```yaml
agent:
  cmd: claude
  permission_mode: bypassPermissions   # 无头自走，不弹交互确认
  max_turns: 40                        # 单阶段轮次上限，防跑飞烧钱
  timeout_s: 1800                      # 墙钟超时，挂了就 kill
  extra_args: [--model, deepseek-v4-flash]
```

换模型改 env 或改这几行 YAML，适配器一行不用动。

另外两个工程细节：prompt 走 **stdin** 而不是命令行参数（避免超长 prompt 撞 `ARG_MAX`）；
CLI 不存在时返回退出码 127 并给一条人话错误，而不是抛栈崩掉。

### 7.6 FakeAgentRunner：不花钱就能测整个引擎

这是整个项目最值得称道的测试设计。`adapters/fake_agent_runner.py` 实现了和真 Agent
完全相同的 `AgentRunner` 接口，但不起进程、不动脑，只做一件确定性的事：
**从 prompt 里正则抽出产物路径和必需章节，写出一份恰好能过闸的桩产物。**

```python
def _artifact_path(prompt: str) -> Path | None:
    m = re.search(r"覆盖写）：`([^`]+)`", prompt)   # 对应硬约束第 1 条
    return Path(m.group(1)) if m else None

def _required_sections(prompt: str) -> list[str]:
    found = re.findall(r"`(##[^`]+)`", prompt)      # 抓所有反引号里的二级标题
    ...
```

于是引擎的**全部编排逻辑**——依赖门、起 Agent 的时机、判卷、置信度门、回滚重试、
状态机、断点续跑——都能在**不接模型、不花一分钱**的前提下被端到端验证。

`scripts/smoke.py` 里两个场景：

```python
# 场景1：一次通过
a1 = FakeAgentRunner(confidence=0.9)
assert r1.status == "succeeded"
assert a1.calls == ["analyze", "fix"]     # ← 断言只有 AI 阶段起了 Agent

# 场景2：analyze 首轮 0.3 触发回滚，第二轮 0.9 通过
a2 = _RetryThenPassAgent()
assert a2.calls.count("analyze") == 2
assert r2.run_state.phase_results["analyze"].retry_count == 1
```

`a1.calls == ["analyze", "fix"]` 这个断言尤其巧妙——它反向验证了"确定性阶段绝不起
Agent"这条架构约束没有被破坏。

命令行也能用：`python3 -m cli run demo --fake-agent`，肉眼看引擎一个个阶段推进。

### 7.7 命名的严谨性

`core/models.py` 里有一段术语表，把 "Phase" 这个词拆成了七个各司其职的类型：

| 名字 | 是什么 | 在哪 |
|---|---|---|
| `PhaseSpec` | 声明（manifest 里"跑什么"） | `core/models.py` |
| `PhaseResult` | 持久化的单阶段生命周期记录 | `core/models.py` |
| `PhaseStatus` | `PhaseResult` 的状态枚举 | `core/models.py` |
| `PhaseVerdict` | 一次执行的**返回裁决** | `core/models.py` |
| `Phase` | 运行时对象（四步执行） | `core/orchestrator.py` |
| `PhaseOutcome` | `phase.run()` 的返回值 | `core/orchestrator.py` |
| `ExecutionContext` | 交给 executor 的只读上下文 | `core/ports.py` |

三套状态各管一层，且刻意统一了用词——"没过"一律叫 `BLOCKED`，不再用 `FAILED`/`BLOCKED`
两个词表达同一件事（`run_state_store.py` 里还留了旧数据的平滑迁移映射）：

```
RunStatus    整次运行     : idle / running / blocked / succeeded
PhaseStatus  单阶段持久化 : pending / running / succeeded / blocked / skipped
PhaseVerdict 单次执行结论 : passed / blocked / preview
```

---

## 八、设计亮点总结

按"值得拿出来讲"的程度排序：

1. **控制权反转（路线 A → 路线 B）**
   把流程调度权从模型手里夺回给确定性代码。这是 AI 应用工程里一个真实的分歧点，
   项目里有完整的取舍记录。

2. **用文件系统状态代替自然语言解析**
   不解析 stdout，只看"产物文件在不在、有没有约定标题"。把不可靠通道换成可靠信号。

3. **可测试性设计（FakeAgentRunner）**
   AI 应用最难的就是测试。这里用一个 130 行的假 Agent，让整条编排链路可以零成本回归。

4. **三层闸门 + 带反馈的回滚重试**
   用工程手段收敛 LLM 的不确定性，且真正兜底的那层是"跑真测试看退出码"，骗不了。

5. **六边形架构落地彻底**
   `core/ports.py` 262 行全是 `Protocol`，零实现；内核不 import `subprocess`。
   `@runtime_checkable` 让协议还能做运行时鸭子类型检查。

6. **声明式 + 开闭原则**
   加阶段 = 一条 YAML + 一个 `@register`。`AgentExecutor` 一个类服务两个阶段，代码零分叉。

7. **边界情况想得细**
   回滚不清 `retry_count`、先删旧产物再起 Agent、断点续跑的双重判据、prompt 走 stdin、
   CLI 缺失返回 127 而不崩栈。

---

## 九、已知不足与技术债

诚实列出，这些也是最可能被追问的点。

### 9.1 并发：进程级全局单例

`PromoteStore` / `RunStateStore` 被重构成了全局静态工具类，基目录是模块级全局变量：

```python
_base: Path = PROJECT_ROOT / "tasks"

@staticmethod
def configure(base_dir: str | Path) -> None:
    global _base
    ...
```

后果：**同一个进程跑不了两个不同基目录的任务**。当前单进程单任务场景没问题，
但要并发跑多任务就得改回"每实例各持 base"的实例版。这个权衡在
`TODO-引擎驱动Agent改造.md` 的 R3 条目里已明确记录。

### 9.2 闸门本身很脆弱

- **结构门**就是 `section in artifact` 的裸字符串匹配。Agent 只要把 `## Root Cause`
  这几个字写进去就算过，内容是空的也没关系。
- **置信度门**读的是 Agent 给自己打的分。理论上模型可以直接写 `0.99` 蒙混过关，
  这道门对"自信的错误"完全无效。

缓解：真正兜底的是 verify 跑真测试。但如果目标项目没有测试覆盖，整套闸门就只剩形式检查。

### 9.3 没有效果数据

demo 的 `verify_command` 只是跑靶子仓库里的一个 `test_algo.py`。没有跑过 SWE-bench
之类的公开基准，所以**"这东西修 bug 的成功率是多少"目前答不上来**。

### 9.4 工程配套缺失

- **没有 README** —— 仓库根目录只有两个中文名的设计文档
- **没有 CI** —— 内联测试要一个个模块手动跑（`python3 -m core.models`）
- **没有依赖声明** —— 没有 `requirements.txt` / `pyproject.toml`，PyYAML 得靠猜
- **没用 pytest** —— 自研了 45 行 `testkit.py`（学习目的可以理解，但换来的是没有
  fixture、没有参数化、没有覆盖率统计）
- **没有 lint / 类型检查配置** —— 代码里写了类型注解但没有 mypy 配置去校验它

### 9.5 能力边界

- **拓扑排序实现了但用不上** —— 当前五个阶段是一条线性链，没有可并行的分支。
  Kahn 算法在这里属于"为未来预留"，某种程度上是过度设计。
- **不管 git** —— 引擎不切分支、不提交、不发 PR，需要人工先切好分支再跑。
- **intake 不触网** —— 传 issue URL 只会把 URL 原样记下来，不会去抓 issue 正文。
- **没有成本控制** —— 只有 `max_turns` 和 `timeout_s` 两个粗粒度护栏，没有 token
  计数、没有累计花费统计、没有预算熔断。
- **单条流水线** —— 一次只能修一个 bug，没有任务队列。

---

## 十、可能的演进方向

按投入产出比排序：

| 优先级 | 方向 | 说明 |
|---|---|---|
| 高 | 补 README + `requirements.txt` | 最低成本的可用性提升 |
| 高 | 接一个公开基准（SWE-bench Lite） | 从"架构 demo"变成"有数据的项目"，质变 |
| 高 | Token / 成本统计 | 从 Agent 的输出里抽用量，落进 `run_state.json` |
| 中 | store 改回实例版 + 任务队列 | 解锁并发跑多任务 |
| 中 | 闸门增强 | 结构门改成解析 Markdown AST 检查章节非空；置信度做交叉验证 |
| 中 | intake 抓真 issue | 接 GitHub API 拉 issue 正文和评论 |
| 中 | 迁到 pytest + 加 CI | GitHub Actions 跑 smoke |
| 低 | 引擎管 git | 自动建分支、提交、发 PR |
| 低 | 并行阶段 | 让拓扑排序真正派上用场（如同时跑多个 verify） |

---

## 十一、怎么对外介绍这个项目

### 11.1 定位建议

**不要把它介绍成"一个 AI 修 bug 工具"**——那样会立刻被追问"效果怎么样、基准跑多少分"，
而目前没有这个数据。

**应该介绍成"一个 AI Agent 编排引擎的架构实践"**——卖点是软件设计能力：
用一套干净的六边形架构，把不确定的 LLM 包进确定的工程流程里。

### 11.2 30 秒电梯版

> BugPilot 是一个声明式的 AI 修 Bug 流水线引擎。它把"修一个 bug"拆成 intake、
> analyze、fix、apply、verify 五个阶段，用一份 YAML 清单声明流程，引擎按依赖拓扑序
> 调度。关键设计是**引擎驱动 Agent**：引擎自己不调大模型，而是在需要动脑的阶段起一个
> Claude Code 子进程，让它在目标仓库里自走读码改码，写完产物后引擎回收、判卷。判卷不过
> 就按配置回滚到上游阶段，带着失败反馈重跑。整个引擎纯标准库实现，2800 行左右。

### 11.3 展开讲的三个点

**① 控制权反转** —— 讲路线 A 和路线 B 的取舍，为什么选 B（流程可控、可审计、
可自动重试）。配套讲"不解析 stdout、只看文件"这个决定。

**② 用工程收敛不确定性** —— 讲三层闸门的层次感（廉价过滤 → 自评 → 真测试兜底），
讲带反馈的回滚重试，讲 `retry_count` 不清零这个细节。

**③ 可测试性** —— 讲 `FakeAgentRunner`：不接模型、不花钱，就能端到端验证整个编排链路。
如果被问"AI 应用怎么测试"，这就是标准答案。

### 11.4 讲述技巧

- **别按目录结构讲**（"我有 core、adapters、executors 三个包"），
  改成**跟着一次真实运行走**：敲命令 → 加载 manifest 拿到拓扑序 → intake 确定性写卡 →
  analyze 起子进程读码 → 回来置信度只有 0.4 不过闸 → 带反馈重跑……这样对方能跟上，
  每个环节都能自然插入设计决策。
- **准备一张图** —— 第 6.2 节那个主循环 ASCII 图直接誊上去就行。
- **主动认领 1~2 个不足**（比如全局单例的并发限制、没有基准数据），
  比被问出来显得判断清醒。

### 11.5 追问准备速查

| 可能的追问 | 怎么答 |
|---|---|
| 并发能跑吗 | 目前不能，store 是进程级全局单例。这是为了少传参数做的权衡，要并发得改回实例版（TODO 里有记录） |
| 置信度是模型自己打的，可信吗 | 不完全可信，它只是廉价的早期过滤。真正兜底的是 verify 跑真测试看退出码，那个骗不了 |
| 成功率多少 | 目前没跑过公开基准，这一版的目标是把编排框架和可测试性打磨好。如果要接 SWE-bench，我会…… |
| 为什么不用 LangGraph / LangChain | 想搞清楚编排引擎内部是怎么回事，而不是学一个框架的 API。而且这套需求（子进程管理 + 文件产物 + 回滚）用框架反而绕 |
| Agent 跑飞了怎么办 | 三道护栏：`max_turns` 限轮次、`timeout_s` 墙钟超时后 kill、退出码非 0 直接判 BLOCKED 不进判卷 |
| 为什么不解析模型输出 | 自然语言解析不可靠。改成"prompt 里给绝对路径，引擎只看文件在不在"，把不可靠通道换成文件系统状态 |
| 怎么保证 Agent 不乱改代码 | 目前只靠 prompt 约束（"只改与本 bug 相关的代码"）+ apply 阶段用 `git diff --stat` 留台账。这是个真实的弱点，更强的做法是加改动范围白名单 |

---

## 十二、快速上手

```bash
# 1. 装依赖
pip install pyyaml
# 确保 claude CLI 在 PATH 中

# 2. 配模型（以 DeepSeek 为例）
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_AUTH_TOKEN=<你的 key>

# 3. 真跑
python3 -m cli run mytask --description "登录页空密码提交崩溃" --repo /path/to/repo

# 4. 只看某一阶段 / 只看 prompt（调试）
python3 -m cli run mytask --only analyze
python3 -m cli run mytask --only analyze --preview

# 5. 查进度
python3 -m cli status mytask

# 6. 不花钱自测（假 Agent）
python3 -m cli run demo --fake-agent --repo /tmp/anyrepo
python3 scripts/smoke.py          # 带断言的回归冒烟（含回滚重试场景）

# 7. 跑某个模块的内联测试
python3 -m core.orchestrator
python3 -m executors.agent_executor
```

**退出码约定**：`0` 全部成功 / `20` 闸门未过且重试耗尽 / `1` 执行器未注册等可预期错误 / `2` 用法错误。

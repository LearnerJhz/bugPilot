# 引擎驱动 Agent 改造 —— TODO / 设计定稿

> 目标：把 BugPilot 从「路线A：拼 prompt + 写占位符 + 短路退出、依赖外层 LLM」改造成
> **「路线B：引擎自己起 Agent 进程、自走跑完、回收产物 → 判卷 → 失败回滚重试」**。
> 即 `ARCHITECTURE-引擎驱动Agent说明.md` 第十节里 IM 那条工程路线。

## 已定方案（拍板结论）

- **驱动方式**：路线B。引擎用 `subprocess` 起 Agent CLI，`wait()` 到它自走跑完，读回产物再 gate。
- **Agent 实体**：`claude -p`（Claude Code，headless）。
- **模型**：接 DeepSeek，通过 **环境变量**（`ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic` 等）切换。
  - 关键解耦：`AgentRunner` adapter **不认识 DeepSeek**，只起 `claude`，模型由 env 决定。
- **占位符哨兵**：主链路**去掉**（纯路线B）。
- **谁调 Agent**：由 **orchestrator（引擎）** 调，executor 保持薄（只 `prepare` 拼 prompt + `gate` 判卷），仍可纯 fake 测试。
- **产物回收**：prompt 里明确要求 Agent 把结论**写进产物文件的绝对路径**；引擎读文件来 gate（不解析 stdout）。
- **护栏**：起 Agent 带 `--permission-mode bypassPermissions`、`--max-turns N`；子进程 `wait(timeout)`，超时 kill。
- **分支/PR**：引擎不管理 git 分支、不提交 PR。分支由你自己提前切好，引擎只在当前目录（`--repo`，默认 `.`）的当前分支上调度 Agent 完成修复流程；`Workspace` 只提供「工作目录」与「跑确定性命令」两件事。

## 目标主循环形态

代码结构就两个对象（见 `core/orchestrator.py`）：

- **`Engine`**：整条运行的持有者。构造时一次性装好 manifest / 三个适配器 / agent /
  run_state / 分支，主循环 `run_pipeline` 只管「阶段之间」的调度。
- **`Phase`**：一个阶段一次执行的持有者。束住 spec / executor / result，`ctx` 由它
  自己在 `__post_init__` 里拼；对外只暴露固定四步的 `run()`。

```
Engine.run_pipeline():
  for phase in 拓扑序 (带 idx，可回退):
    ├─ 断点续跑（已 SUCCEEDED 且产物在 → 跳过 resume）
    ├─ 依赖门（depends_on 都 SUCCEEDED？否则 SKIP）
    ├─ phase = self._new_phase(spec)        ← 只递 spec/executor/result，ctx 由 Phase 自造
    ├─ outcome = phase.run():               ← 固定四步：
    │     ① executor.prepare(ctx)           准备 prompt / 产物
    │     ② _produce():                     喂给 Agent + 收产物
    │          · AI 阶段 → agent.run(prompt) 起 claude -p 自走完，读回 0X.md
    │          · 确定性阶段 → 直接写出产物
    │     ③（收产物失败 → 直接 BLOCKED）
    │     ④ _gate()                         校验必需章节 + 置信度门
    ├─ 存状态（run_state.json）
    ├─ PASSED → idx++
    ├─ FAILED 且有预算 → 记反馈 + 清理产物 + 回滚到 retry_on_fail.target
    └─ FAILED 无预算 → 整体 BLOCKED，停下等人
```

`run_pipeline` / `run_phase` 只是模块级薄入口：组一个 `Engine` 再让它跑。

## 任务清单（）

- [x] 1. `core/ports.py`：新增 `AgentRunner` port + `AgentRunResult`；改掉「禁止调模型」的思想约束文案。
- [x] 2. `adapters/agent_runner.py`（新建）：`ClaudeAgentRunner`（subprocess 起 `claude -p`，bypass 权限 / max-turns / timeout / env 透传）+ `build_agent_runner(project)` 工厂 + 测试用 fake。
- [x] 3. `core/models.py`：`PhaseResult` 加 `retry_count`；`PhaseSpec` 加 `retry_on_fail` / `confidence_min`；`core/manifest.py::from_dict` 解析新字段；`run_state_store` 序列化 `retry_count`。
- [x] 4. `core/orchestrator.py`：抽出 `_execute_phase`（prepare→起 Agent→读产物→gate）；新增 `run_pipeline`（拓扑循环 + 依赖门 + 置信度门 + 回滚重试 + 断点续跑）；`run_phase` 复用同一逻辑、去掉占位符短路。 <sub>（此散函数结构后由 R1 重构为 `Engine` + `Phase`）</sub>
- [x] 5. `executors/`：新增通用 `AgentExecutor`（注册 `analyze`/`fix`）、`apply`、`verify` 执行器；在 orchestrator 里 import 触发注册。
- [x] 6. `prompts/`（新建）：`analyze.md`、`fix.md` 阶段提示词模板。
- [x] 7. `manifest.yaml`：加 `project.agent` 配置；给 `analyze` 加 `confidence_min:0.6` + retry；给 `fix` 加 retry；给 `verify` 加 `retry_on_fail: {target: analyze}`。
- [x] 8. `cli/main.py`：`run` 默认走 `run_pipeline` 全自动跑；保留 `--only`/`--preview` 单阶段；退出码去掉 AWAITING(10)。
- [x] 9. 各模块内联测试全绿 + `scripts/smoke.py` 端到端冒烟（含置信度门回滚重试）通过 + 无 lint。

## 结构整理（路线B 跑通后的可读性重构）

主链路功能不变，只为「少传变量、职责归位、看着不乱」。改完 smoke 两场景 + 各模块内联测试仍全绿。

- [x] R1. `core/orchestrator.py` 由「一堆 8 参数的散函数（`_execute_phase` / `_gate` /
  `_build_context` / `_finalize_run_state` 互相手传 executor/ctx/result/store/task_id…）」
  重构为 **`Engine`（整条运行）+ `Phase`（单阶段四步）** 两个对象：
  - 「整条运行共享」的东西（manifest / store / state_store / workspace / agent /
    run_state / retry_feedback）挂在 `Engine` 上，主循环只剩「依赖门 → 组 Phase →
    `phase.run()` → 存状态 → 过则前进 / 不过则回滚」。
  - 「一个阶段一次」的四步（准备 → 喂 Agent → 收产物 → 校验）是 `Phase` 的方法，
    料全在 `self` 上，方法间几乎不再传参。
  - 回滚 / 断点续跑 / 收尾等判断都拆成 `Engine` 的小方法。
  - `run_pipeline` / `run_phase` 缩成薄入口（组 `Engine` 让它跑），CLI / smoke 调用口不变。
- [x] R2. `core/ports.py::ExecutionContext` 去掉无类型的 `config` 杂物 dict，提升为**具名字段**
  （`prior_artifacts` / `project` / `retry_feedback`）；删掉两个派生值 `artifact_path`
  （= `store.path_for(...)`，executor 现算）和 `project_root`（= `core.manifest.PROJECT_ROOT`
  常量）。造 ctx 的职责从 `Engine._build_context` 搬进 `Phase._build_context`（料取自 engine）。
  连带改 4 个 executor 的取值处与它们的内联测试。
- [x] R3. `adapters/promote_store.py` / `run_state_store.py` 改为**全局静态工具类**：无实例状态，
  基目录提到模块级全局，`configure(base)` 设定一次；`Engine` 里 `self.store = PromoteStore`
  直接把「类本身」当 store 用（`self.store.write(...)` 等价 `PromoteStore.write(...)`），不再 `new`。
  > 权衡：base 变成进程级全局单例，同一进程只能有一个基目录——当前单进程单任务场景 OK，
  > 若将来要并发跑多个不同 `tasks` 目录，需改回「每实例各持 base」的实例版。

## 如何真跑（接 DeepSeek）

```bash
export ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
export ANTHROPIC_AUTH_TOKEN=<你的 DeepSeek key>
export ANTHROPIC_MODEL=deepseek-v4-pro[1m]
# 确保 `claude` 在 PATH 中
python3 -m cli run mytask --description "登录页空密码提交崩溃" --repo /path/to/target/repo
```

- 不带 `--only`：引擎自走跑完 intake→analyze→fix→apply→verify，AI 阶段由引擎起 `claude -p` 执行。
- `--only <phase>`：只推进单个阶段（调试）；`--preview` 只看将交给 Agent 的 prompt。
- 冒烟（不需真 claude）：`python3 scripts/smoke.py`。

## 不接真 Agent，怎么验证引擎对不对

假 Agent 单独放在 `adapters/fake_agent_runner.py`（**纯自测件**）：它实现和真 Agent 一样的
`AgentRunner` 接口，但不起进程、不动脑，只按 prompt 里声明的"产物路径 + 必需二级标题"
写出一份恰好能过闸的桩产物。这样引擎的编排逻辑（依赖门 / 起 Agent 时机 / gate / 置信度门 /
回滚重试 / 状态机 / 断点续跑）都能被验证，而不碰真模型。

两种自测方式：

1. **像生产一样跑（推荐，肉眼看引擎驱动一个个 phase）**：
   ```bash
   python3 -m cli run demo --description "登录页空密码崩溃" --repo /tmp/anyrepo --fake-agent
   python3 -m cli status demo         # 看每个 phase 的状态
   ```
   会看到 intake→analyze→fix→apply→verify 依次 ✅，AI 阶段打印"↳ 起 Agent 进程自走"。
   看对不对：① 退出码 0；② `status` 全 succeeded；③ `tasks/demo/` 下 01~05 产物 + `_prompt_*.md` 都在。

2. **带断言的回归冒烟（含回滚重试场景）**：
   ```bash
   python3 scripts/smoke.py
   ```
   场景1 一次通过；场景2 analyze 首轮置信度 0.3 → 引擎回滚重试 → 第二轮 0.9 通过（断言 retry_count==1）。

判断引擎"对不对"的几个观察点：
- **调度顺序**：`FakeAgentRunner.calls` 只应含 AI 阶段（analyze/fix），确定性阶段不起 Agent。
- **置信度门/回滚**：低置信度要能触发回滚到 `retry_on_fail.target` 并重跑，超预算才 BLOCKED。
- **断点续跑**：中途 Ctrl-C 后再 `run` 同一 task，已 succeeded 的阶段应打印"⏭️ 跳过(resume)"。
- **verify 失败回滚**：把 manifest 的 `verify_command` 改成会失败的命令（如 `["false"]`），
  应看到 verify 未过 → 回滚到 analyze 重跑，直至重试耗尽才 BLOCKED(退出码 20)。

## 退出码（新）

- `0` 整条流水线成功
- `20` 闸门/置信度未过且重试耗尽 → BLOCKED
- `1` 执行器未注册 / Agent 起不来等错误
- `2` 用法错误（argparse）

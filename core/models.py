"""
在各模块之间传递的数据结构：阶段、状态、结果、补丁。
仅使用标准库，无第三方依赖。纯数据定义，不含业务逻辑。

RunState(整次运行)
├── status: RunStatus            ← 整体状态牌子
├── phase_results:
│   ├── "intake"  → PhaseResult(status=SUCCEEDED, ...)
│   ├── "analyze" → PhaseResult(status=SUCCEEDED, ...)
│   ├── "fix"     → PhaseResult(status=RUNNING, ...)
│   └── ...
├── current_phase: "fix"         ← 现在跑到哪了
└── description, 时间戳...

### 完整 Timeline
T0 — 刚创建,还没开工

RunState.status      = IDLE          ← 整体:没开工
current_phase        = None
phase_results        = {}            ← 空的,一张卡都没有
T1 — 引擎启动,建好分支,开始跑

RunState.status      = RUNNING  ▶    ← 整体切到"进行中"
current_phase        = "intake"
T2 — 跑 intake 阶段

current_phase        = "intake"
phase_results = {
  "intake": PhaseResult(status = RUNNING ▶, started_at=...)  ← 新建的卡
}
工人(intake executor)产出 01-intake.md → 引擎检查必需章节 ## Summary/## Inputs 都在 → 判成功:

phase_results = {
  "intake": PhaseResult(status = SUCCEEDED ✓,
                        output_path = "01-intake.md",
                        finished_at=...)
}
T3~T5 — analyze / fix / apply 依次同理

每个阶段都是这个循环:PENDING → RUNNING → SUCCEEDED,并把产物路径记下来。黑板逐渐填满:

phase_results = {
  "intake":  SUCCEEDED ✓  01-intake.md
  "analyze": SUCCEEDED ✓  02-analyze.md
  "fix":     SUCCEEDED ✓  03-fix.md
  "apply":   SUCCEEDED ✓  04-apply.md
}
T6 — verify 阶段(这个没 AI,是跑测试命令)

"verify": RUNNING ▶
跑 verify_command,返回码 0 → PASS:

"verify": SUCCEEDED ✓  05-verify.md
T7 — 所有阶段都成了,整体收尾

RunState.status  = SUCCEEDED ✓   ← 整体成功
current_phase    = None
CLI 看到 status is SUCCEEDED → 退出码 0。

整条状态线(正常):


Run:    IDLE ──> RUNNING ─────────────────────────────> SUCCEEDED
                   │                                        ▲
Phase: intake     PENDING→RUNNING→SUCCEEDED                 │
       analyze           PENDING→RUNNING→SUCCEEDED          │
       fix                     PENDING→RUNNING→SUCCEEDED    │
       apply                         PENDING→RUNNING→SUCCEEDED
       verify                              PENDING→RUNNING→SUCCEEDED
"""

import json
from dataclasses import asdict, dataclass, field    # dataclass：生成样板代码；field：字段默认值；asdict：转为字典
from typing import Any, Optional                    # Any：任意类型；Optional[X]：X 或 None
from enum import Enum
from datetime import datetime, timezone

# 带时区的时间戳
def utc_now_iso() -> str:
    """在各运行状态文件中通用的紧凑 UTC 时间戳。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

# --------------------------------------------------------------------------
# Manifest 侧契约（声明式地描述"流程是什么"）
# --------------------------------------------------------------------------
@dataclass
class PhaseSpec:
    """在 manifest 中声明的一个阶段：由哪个执行器驱动、输出什么文件，
    以及（可选）该输出必须包含的标题。引擎负责调度这些阶段；执行器只负责
    填充产物。新增一个阶段 = 一条 manifest 条目加一个已注册的执行器，
    永远不需要改动编排器。"""

    # 唯一的阶段 id，用于依赖引用、调度查找以及展示（例如 "intake"）
    id: str

    # 驱动该阶段的执行器名称；引擎据此解析具体实现
    executor: str

    # 该阶段产生的输出文件；下一个阶段会消费它
    output: str

    # 前置阶段 id；拓扑排序据此确定执行顺序。
    # 可变默认值（list/dict/set）必须使用 default_factory，否则会在多个实例间共享。
    depends_on: list[str] = field(default_factory=list)

    # 输出必须包含的标题；Gate Check 用它校验 Agent 写回产物的完整性
    required_sections: list[str] = field(default_factory=list)

    # 该阶段 prompt 模板的路径（相对项目根）；引擎据它编译 `_prompt_<phase>.md`，
    # 再通过 AgentRunner 起一个 Agent 进程去自走执行。确定性阶段（intake/apply/verify）
    # 无需 prompt，为 None。注意：引擎自己从不调用大模型，只负责把模板拼成 prompt。
    prompt: Optional[str] = None

    # 置信度门（可选，主要给 analyze）：Agent 写回产物里 `## Confidence` 抽出的分数
    # 若低于此阈值，即便必需章节齐全，引擎也判该阶段未过，触发回滚重试。None = 不设门。
    confidence_min: Optional[float] = None

    # 失败回滚策略（可选）：{"target": <阶段id>, "max_retries": <次数>}。
    # 该阶段未过闸时，引擎清空 target..当前 的产物、把执行指针拨回 target 重跑，
    # 最多 max_retries 次。None = 不重试，未过即整体 BLOCKED。
    # 典型：verify 失败回滚到 analyze；analyze 置信度低就地重试（target=analyze）。
    retry_on_fail: Optional[dict] = None

# --------------------------------------------------------------------------
# 术语表："Phase" 这个词在本项目里分成几个各司其职的类型，别混淆：
#
#   PhaseSpec        —— 声明（manifest 里"跑什么"）             见本文件
#   PhaseResult      —— 持久化的单阶段生命周期记录（写进 run_state.json） 见本文件
#   PhaseStatus      —— PhaseResult 的持久化状态牌子（下面这个 enum）      见本文件
#   PhaseVerdict     —— phase.run() 一次执行的**返回结论**（下面这个 enum） 见本文件
#   Phase            —— 运行时对象（一次执行的四步：准备→喂 Agent→收→校验） core/orchestrator.py
#   PhaseOutcome     —— phase.run() 的返回值（带 PhaseVerdict + 路径等）    core/orchestrator.py
#   ExecutionContext —— 交给 executor 的只读上下文                          core/ports.py
#
# 三套"状态"各管一层，但"没过"一律叫 BLOCKED（不再用 FAILED/BLOCKED 两个词表达同一件事）：
#   RunStatus    —— 整次运行     : idle / running / blocked / succeeded
#   PhaseStatus  —— 单阶段持久化 : pending / running / succeeded / blocked / skipped
#   PhaseVerdict —— 单次执行结论 : passed / blocked / preview
# --------------------------------------------------------------------------


# 整个过程的状态
class RunStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"

# 单阶段持久化生命周期状态（写进 run_state.json，供断点续跑/审计）
class PhaseStatus(str, Enum):
    PENDING = "pending"        # 也用于"已写占位产物、正等 Agent 干活"这一态
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"        # 未过闸（缺章节 / 置信度低 / Agent 异常）——历史上叫 FAILED
    SKIPPED = "skipped"


# phase.run() 一次执行的返回结论（瞬时值，不落盘）。与 PhaseStatus 各管一层：
# PhaseStatus 记"这个阶段现在处于什么持久化状态"，PhaseVerdict 记"这一轮跑完给出什么裁决"。
class PhaseVerdict(str, Enum):
    PASSED = "passed"          # 本阶段过闸，可进入下一阶段
    BLOCKED = "blocked"        # 未过闸（缺章节 / 置信度低 / Agent 异常）
    PREVIEW = "preview"        # 只预览 prompt，不落盘、不起 Agent


# --------------------------------------------------------------------------
# 占位产物哨兵（路线A 遗留；路线B 主链路已不用）
# --------------------------------------------------------------------------
# 历史上（路线A）引擎为 AI 阶段写占位产物、含这句哨兵表示"Agent 还没写回"，据此
# 短路退出等外层 LLM。改成路线B后，引擎自己起 Agent 进程并阻塞等它写回产物，不再
# 需要短路，因此主链路不再产生/检测哨兵。此常量保留仅为兼容旧产物与个别测试。
AGENT_PENDING_SENTINEL = "[Awaiting agent execution"


# --------------------------------------------------------------------------
# 运行时状态契约（记录"实际发生了什么"）
# --------------------------------------------------------------------------
@dataclass
class PhaseResult:
    phase_id: str
    status: PhaseStatus = PhaseStatus.PENDING
    output_path: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    retry_count: int = 0                # 本阶段已重试的次数（回滚重试时递增，用于卡住 max_retries）

@dataclass
class RunState:
    """整次运行的状态，可序列化为单个 json 文件（黑板）。

    这是整条流水线的"单一事实来源"：引擎每推进一个阶段就把它读出来、
    改一改、再写回磁盘（tasks/<task_id>/run_state.json）。因此哪怕进程中途
    退出，下次也能凭它断点续跑——引擎据此知道"跑到哪了、哪些阶段成了"。

    """

    task_id: str                                                    # 任务唯一标识，也是产物目录名
    status: RunStatus = RunStatus.IDLE                              # 整次运行的总状态（IDLE/RUNNING/BLOCKED/SUCCEEDED）
    description: str = ""                                           # 本次任务的自然语言描述（如 bug 描述），供各阶段 prompt 复用
    current_phase: Optional[str] = None                             # 当前推进到的阶段 id；全部完成后回归 None
    phase_results: dict[str, PhaseResult] = field(default_factory=dict)  # 黑板主体——阶段 id → 该阶段的 PhaseResult（状态/产物/时间戳）
    started_at: str = field(default_factory=utc_now_iso)            # 本次运行创建时间（UTC ISO）
    updated_at: str = field(default_factory=utc_now_iso)            # 最近一次状态写回时间（UTC ISO），每次 save 时刷新

# 说明：本引擎刻意不定义"补丁提案（PatchProposal）"之类的结构化改动契约。
# 在 Agent 驱动模型里，代码改动由外部 Agent 直接在你当前所在的工作区目录里落盘；
# 分支由你自己提前切好，引擎不切/建分支、不切目录、不提交，也不解析或应用任何补丁。
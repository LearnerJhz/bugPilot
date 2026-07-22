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
└── branch, description, 时间戳...

### 完整 Timeline
T0 — 刚创建,还没开工

RunState.status      = IDLE          ← 整体:没开工
current_phase        = None
phase_results        = {}            ← 空的,一张卡都没有
T1 — 引擎启动,建好分支,开始跑

RunState.status      = RUNNING  ▶    ← 整体切到"进行中"
branch               = "bugpilot/xxx"
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

    # 该阶段 prompt 模板的路径（相对项目根）；引擎据它编译 `_prompt_<phase>.md`
    # 交给外部 Agent。确定性阶段（intake/apply/verify）无需 prompt，为 None。
    # 注意：引擎自己从不调用大模型，只负责把模板拼成 prompt 文件。
    prompt: Optional[str] = None

# 整个过程的状态
class RunStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"

# 断点续跑，引擎用
class PhaseStatus(str, Enum):
    PENDING = "pending"        # 也用于"已写占位产物、正等 Agent 干活"这一态
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


# --------------------------------------------------------------------------
# Agent 驱动契约：占位产物哨兵
# --------------------------------------------------------------------------
# 引擎为 AI 阶段写占位产物时会嵌入这句。只要产物里还含它，就说明 Agent 还没
# 把真正的结论写回来 —— 引擎据此原地退出（不做 Gate Check、不重试），等下次被
# Agent 重新调用。这是整个"Agent 驱动"模型里判断"到底干没干活"的唯一开关。
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

@dataclass
class RunState:
    """整次运行的状态，可序列化为单个 json 文件（黑板）。"""

    task_id: str
    status: RunStatus = RunStatus.IDLE
    description: str = ""
    branch: str = ""
    current_phase: Optional[str] = None
    phase_results: dict[str, PhaseResult] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

# 说明：本引擎刻意不定义"补丁提案（PatchProposal）"之类的结构化改动契约。
# 在 Agent 驱动模型里，代码改动由外部 Agent 在自己的工作区（专用分支 / worktree）
# 里直接落盘；引擎既不代 Agent 动源码，也不解析、不应用任何 LLM 产出的补丁。
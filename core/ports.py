"""端口（Ports）：外部世界插入内核的接缝。

这里只有接口，没有任何实现。编排器（orchestrator）只与这些 Protocol 对话，
绝不直接依赖具体类（git、文件系统……）。这层间接正是关键所在：
更换适配器，保持内核不变。这就是依赖倒置（dependency inversion）的实践。

Agent 驱动模型的关键约束：**这里没有任何"调大模型"的端口**。引擎从不调用
LLM，它只做两件确定性的事——为 AI 阶段编译 prompt + 放占位产物（prepare），
以及对 Agent 写回的产物看门（gate）。真正的"智能"完全由外部 Agent 完成。

本模块只向内依赖 ``core.models``，自身不做任何真实的 IO。
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from core.models import PhaseSpec, RunState


# --------------------------------------------------------------------------
# 跨接缝传递的值对象（value objects）
# --------------------------------------------------------------------------

# 接口 Workspace.run(command)的返回
@dataclass
class CommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    # 计算属性，一个小func
    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class ExecutionContext:
    """交给某个阶段执行器（phase executor）的全部内容。注意它拿到的是*端口*
    （workspace、store），而不是具体的适配器——因此执行器可以用假实现（fake）
    来测试，且自身永远不会直接触碰外部世界。

    这里**没有 llm 端口、也没有预编译好的 prompt_text**：executor 不调模型，
    prompt 是它在 ``prepare`` 里自己编译出来的产物，而不是别人喂进来的输入。"""

    task_id: str
    spec: PhaseSpec
    description: str
    workspace: "Workspace"
    store: "ArtifactStore"
    branch: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class PhasePrep:
    """两段式执行器"第一段"（``prepare``）的产出。

    - **AI 阶段**（analyze/fix）：``prompt_text`` 是编译好的 ``_prompt_<phase>.md``
      全文（工具约束 + 阶段提示词 + 领域知识 + 编码规则 + 前置产物 + 环境变量 +
      输出约定）；``artifact`` 是含哨兵 ``AGENT_PENDING_SENTINEL`` 的占位产物。
      引擎写下这两者后标记 PENDING、退出，等外部 Agent 读 prompt、写回产物。
    - **确定性阶段**（intake/apply/verify）：无需 Agent，``prompt_text`` 留空，
      ``artifact`` 直接是最终产物内容（不含哨兵）；引擎写下后立即进入 Gate Check。

    真正把 ``artifact`` / ``prompt_text`` / ``extra_artifacts`` 落盘的是编排器
    （而非执行器），以便集中管理产物写入。"""

    artifact: str
    prompt_text: str = ""
    extra_artifacts: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class GateResult:
    """两段式执行器"第二段"（``gate``）的产出：对 Agent 写回的产物看门。
    ``passed`` 为真才放行进入下一阶段；``confidence`` 供置信度阈值判断，
    ``missing_sections`` 记录缺失的必需章节，便于反馈给 Agent 重做。"""

    passed: bool
    confidence: float = 0.0
    missing_sections: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 端口（接口）
# --------------------------------------------------------------------------

# Agent 干活的 git 工作区（一条专用分支 / 独立 worktree）
# 装饰器的作用:让这些协议可以用 isinstance(obj, Workspace) 在运行时检查。
# 默认的 Protocol 只能用于静态类型检查(mypy 之类),加了它才能运行时判断。
@runtime_checkable
class Workspace(Protocol):
    """外部 Agent 改代码的地方：一条专用分支 / 独立 worktree。

    职责边界很关键——引擎只负责把工作区**准备好**（切/建分支、给出根目录路径）
    和在里面跑**确定性命令**（如 verify）。它**既不读也不写工作区里的源码**：
    读代码、改代码都由外部 Agent 在这个目录里直接完成（引擎不代 Agent 动源码，
    也没有 ``read_file`` / ``write_file`` / ``apply_patch`` 这类口子）。

    通过这一层间接，内核永远不会 import ``subprocess``，也不需要知道 git 是什么。"""

    # Agent 应当在其中读写源码的工作区根目录（引擎据此建 worktree、跑 verify、报给 Agent）
    def root_path(self) -> str: ...

    # 确保切到某条专用分支（Agent 在这条分支上改，不碰主干）；分支不存在就创建
    def ensure_branch(self, name: str) -> None: ...

    # 返回当前所在的 git 分支名
    def current_branch(self) -> str: ...

    # 在工作区里跑一条确定性命令（比如 verify 的 pytest，看修没修好），返回退出码 + 输出
    def run(self, command: list[str]) -> CommandResult: ...


# phase输出持久化，阶段之间传递结果
@runtime_checkable
class ArtifactStore(Protocol):
    """黑板（blackboard）：各阶段的输出以文件形式存放于此，按任务（task）归类。"""

    # 把某个产物写进黑板（按 task_id + name 归类），返回它的存放路径
    def write(self, task_id: str, name: str, content: str) -> str: ...

    # 读回某个已写入的产物的内容
    def read(self, task_id: str, name: str) -> str: ...

    # 判断某个产物在不在（用于“断点续跑”：已存在的阶段可跳过）
    def exists(self, task_id: str, name: str) -> bool: ...

    # 拿到某个产物的路径（只给路径，不读内容）
    def path_for(self, task_id: str, name: str) -> str: ...


# 持久化整个任务的运行状态 RunState
@runtime_checkable
class StateStore(Protocol):
    """持久化的运行状态（用于恢复 + 审计）。"""

    def load(self, task_id: str) -> Optional[RunState]: ...

    def save(self, state: RunState) -> None: ...

# 暂时只定义，接线作用，测试时的fake时钟
@runtime_checkable
class Clock(Protocol):
    def now_iso(self) -> str: ...

# 抽象协议，每个executor具体实现
@runtime_checkable
class PhaseExecutor(Protocol):
    """一个阶段的行为，两段式。按名称注册；编排器从清单（manifest）的
    ``executor`` 字段查找它，绝不硬编码任何具体阶段。

    引擎每次被 Agent 调用时对某阶段的处理：

    1. ``prepare(ctx)`` —— 编译 prompt + 给出（占位或最终）产物。
       引擎写下产物后，若其中含哨兵则标记 PENDING、退出，等 Agent 干活；
       否则（确定性阶段）直接进入第 2 步。
    2. ``gate(ctx, artifact)`` —— Agent 写回产物后，对产物看门
       （校验必需章节 + 置信度），``passed`` 为真才放行进入下一阶段。

    这两步之间引擎绝不调用任何大模型——中间那段"智能"由外部 Agent 完成。"""

    def prepare(self, ctx: ExecutionContext) -> PhasePrep: ...

    def gate(self, ctx: ExecutionContext, artifact: str) -> GateResult: ...


# --------------------------------------------------------------------------
# 内联测试（运行方式：python3 -m core.ports）
# --------------------------------------------------------------------------
def test_command_result_ok():
    assert CommandResult(0).ok
    assert not CommandResult(1).ok


def test_runtime_checkable_ports_accept_duck_types():
    class _Exec:
        def prepare(self, ctx):
            return PhasePrep(artifact="x")

        def gate(self, ctx, artifact):
            return GateResult(passed=True)

    assert isinstance(_Exec(), PhaseExecutor)

    # 只实现一半（缺 gate）的类会被拒绝——两段式都得齐。
    class _Half:
        def prepare(self, ctx):
            return PhasePrep(artifact="x")

    assert not isinstance(_Half(), PhaseExecutor)
    assert not isinstance(object(), PhaseExecutor)


def test_phase_prep_and_gate_defaults():
    prep = PhasePrep(artifact="hello")
    assert prep.prompt_text == ""
    assert prep.extra_artifacts == {}
    assert prep.notes == []

    gate = GateResult(passed=False)
    assert gate.confidence == 0.0
    assert gate.missing_sections == []


if __name__ == "__main__":
    import sys

    from testkit import run_module_tests

    exit_code = run_module_tests(globals())
    sys.exit(exit_code)

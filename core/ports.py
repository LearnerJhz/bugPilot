"""端口（Ports）：外部世界插入内核的接缝。

这里只有接口，没有任何实现。编排器（orchestrator）只与这些 Protocol 对话，
绝不直接依赖具体类（git、文件系统……）。这层间接正是关键所在：
更换适配器，保持内核不变。这就是依赖倒置（dependency inversion）的实践。

引擎驱动模型（路线B）的关键接缝：``AgentRunner``。引擎**自己起一个真 Agent 进程**
（如 ``claude -p``）让它在 workspace 里自走干完一个阶段，然后回收产物、判卷。引擎
本身仍不含任何"智能"——它只负责：为阶段编译 prompt（executor.prepare）、通过
``AgentRunner`` 把这份 prompt 交给一个自带工具的 Agent 去自走执行、再对 Agent 写回
的产物看门（executor.gate）。"用哪个模型"由 Agent CLI 的环境变量决定，本层不关心。

本模块只有接口、没有实现，也不做任何真实 IO；只向内依赖 ``core.models``。
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
class AgentRunResult:
    """``AgentRunner.run`` 的返回：引擎起的那个 Agent 子进程跑完后的结果。

    引擎不解析 stdout 里的"答案"——真正的产物由 Agent 自己写进产物文件，引擎只用
    这里的 ``exit_code`` / ``timed_out`` 判断"这轮 Agent 到底正常收尾没有"，用
    ``stdout`` / ``stderr`` 做日志与排障。"""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class ExecutionContext:
    """交给某个阶段执行器（phase executor）的全部内容。注意它拿到的是*端口*
    （workspace、store），而不是具体的适配器——因此执行器可以用假实现（fake）
    来测试，且自身永远不会直接触碰外部世界。

    这里**没有 llm 端口、也没有预编译好的 prompt_text**：executor 不调模型，
    prompt 是它在 ``prepare`` 里自己编译出来的产物，而不是别人喂进来的输入。

    历史上这里还挂过一个无类型的 ``config`` 杂物 dict（塞 prior_artifacts / project /
    retry_feedback 等）。现已全部提升为**具名字段**：字段有类型、可被 IDE 补全，也
    不再把"能算出来的派生值"（如产物落盘路径 = ``store.path_for(...)``、项目根 =
    ``core.manifest.PROJECT_ROOT``）冗余地塞进来——那些让 executor 自己现算即可。"""

    task_id: str
    spec: PhaseSpec
    description: str
    workspace: "Workspace"
    store: "ArtifactStore"

    # 前置阶段已存在的产物（拓扑序），作为下游 prompt 的上下文：[(产物名, 内容), ...]
    prior_artifacts: list[tuple[str, str]] = field(default_factory=list)
    # manifest 的 project 配置（verify 阶段据此取 verify_command 等）
    project: dict[str, Any] = field(default_factory=dict)
    # 回滚重试时注入本阶段的失败反馈（当反例喂给 Agent）；无重试则为 None
    retry_feedback: Optional[str] = None


@dataclass
class PhasePrep:
    """两段式执行器"第一段"（``prepare``）的产出。引擎据 ``prompt_text`` 是否为空
    来区分这是 AI 阶段还是确定性阶段：

    - **AI 阶段**（analyze/fix）：``prompt_text`` 是编译好的 ``_prompt_<phase>.md``
      全文（工具约束 + 阶段提示词 + 前置产物 + 环境 + 产物落盘路径 + 输出约定）。
      引擎把它交给 ``AgentRunner`` 起一个 Agent 进程去自走执行，**产物由 Agent 自己
      写进产物文件**——所以 AI 阶段的 ``artifact`` 通常留空，不预写占位内容。
    - **确定性阶段**（intake/apply/verify）：``prompt_text`` 留空，``artifact`` 直接
      是最终产物内容；引擎写下后立即进入 Gate Check，不起 Agent。

    真正把 ``artifact`` / ``prompt_text`` / ``extra_artifacts`` 落盘、以及（AI 阶段）
    调用 ``AgentRunner`` 的都是编排器，而非执行器——执行器只负责"拼"和"判"。"""

    artifact: str = ""
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

# Agent 干活的目录（你自己切好分支后直接跑，引擎不碰 git 分支）
# 装饰器的作用:让这些协议可以用 isinstance(obj, Workspace) 在运行时检查。
# 默认的 Protocol 只能用于静态类型检查(mypy 之类),加了它才能运行时判断。
@runtime_checkable
class Workspace(Protocol):
    """外部 Agent 改代码的地方：一个本地目录（分支由你自己提前切好，引擎不管理）。

    职责边界很关键——引擎只负责给出**根目录路径**（Agent 在这里读/改代码、也是起
    Agent 进程的 cwd）和在里面跑**确定性命令**（如 verify 的测试）。它**既不读也不写
    工作区里的源码**（读/改代码都由外部 Agent 完成），也**不切/建 git 分支、不提交**。

    通过这一层间接，内核永远不会 import ``subprocess``，也不需要知道 git 是什么。"""

    # Agent 应当在其中读写源码的工作区根目录（也是起 Agent 进程的 cwd、跑 verify 的地方）
    def root_path(self) -> str: ...

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

# 引擎驱动 Agent 的核心接缝：起一个真 Agent 进程干完一个阶段
@runtime_checkable
class AgentRunner(Protocol):
    """引擎驱动的执行单元：给定一份编译好的 prompt，起一个**自带工具的 Agent 进程**
    （如 ``claude -p``），让它在 ``workspace`` 里自走完成一个阶段（读码/改码/跑命令/
    把结论写进产物文件），引擎阻塞等它跑完并回收退出信息。

    这正是"引擎驱动 Agent（路线B）"区别于"占位符短路 + 外层 LLM（路线A）"的地方：
    模型进程的生命周期由**引擎**掌控，而不是甩给外层编排者。具体起哪个 CLI、用哪个
    模型（DeepSeek / Claude / …）由适配器与其环境变量决定，本接口一律不关心。"""

    def run(
        self,
        *,
        prompt: str,
        workspace: "Workspace",
        phase_id: str,
        timeout_s: Optional[int] = None,
    ) -> AgentRunResult: ...


# 暂时只定义，接线作用，测试时的fake时钟
@runtime_checkable
class Clock(Protocol):
    def now_iso(self) -> str: ...

# 抽象协议，每个executor具体实现
@runtime_checkable
class PhaseExecutor(Protocol):
    """一个阶段的行为，两段式。按名称注册；编排器从清单（manifest）的
    ``executor`` 字段查找它，绝不硬编码任何具体阶段。

    引擎处理某阶段的流程：

    1. ``prepare(ctx)`` —— 编译 prompt（AI 阶段）或直接产出最终产物（确定性阶段）。
    2. 【仅 AI 阶段】引擎通过 ``AgentRunner`` 起一个 Agent 进程，让它读这份 prompt、
       在工作区里自走干完、把结论写进产物文件；引擎阻塞等它跑完。
    3. ``gate(ctx, artifact)`` —— 对已到位的产物看门（校验必需章节 + 置信度），
       ``passed`` 为真才放行进入下一阶段。

    executor 自身绝不起进程、也不调模型：起 Agent 这件事由编排器持有 ``AgentRunner``
    统一来做，executor 只管"拼 prompt"和"判产物"，因此可用纯 fake 测试。"""

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

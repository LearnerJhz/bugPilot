"""端口（Ports）：外部世界插入内核的接缝。

这里只有接口，没有任何实现。编排器（orchestrator）只与这些 Protocol 对话，
绝不直接依赖具体类（openai、git、文件系统……）。这层间接正是关键所在：
更换适配器，保持内核不变。这就是依赖倒置（dependency inversion）的实践。

本模块只向内依赖 ``core.models``，自身不做任何真实的 IO。
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from core.models import PatchProposal, PhaseSpec, PhaseStatus, RunState


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
    （llm、workspace、store），而不是具体的适配器——因此执行器可以用假实现
    （fake）来测试，且自身永远不会直接触碰外部世界。"""

    task_id: str
    spec: PhaseSpec
    description: str
    workspace: "Workspace"
    llm: "LLMClient"
    store: "ArtifactStore"
    branch: str = ""
    prompt_text: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionOutput:
    """一个阶段返回的内容。``content`` 会成为该阶段的产物（artifact）；真正负责
    持久化它以及 ``extra_artifacts`` 的是编排器（而非执行器）——这样就把产物写入
    集中管理起来。"""

    content: str
    status: PhaseStatus = PhaseStatus.SUCCEEDED
    proposal: Optional[PatchProposal] = None
    extra_artifacts: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 端口（接口）
# --------------------------------------------------------------------------

# 装饰器的作用:让这个协议可以用 isinstance(obj, PhaseExecutor) 在运行时检查。
# 默认的 Protocol 只能用于静态类型检查(mypy 之类),加了它才能运行时判断
@runtime_checkable
class LLMClient(Protocol):
    """“双手”：无状态，文本进、文本出。可以是 Fake / OpenAI / Claude。"""

    def complete(self, prompt: str) -> str: ...


# 待修复的代码工作区，跟它交互的所有手段
@runtime_checkable
class Workspace(Protocol):
    """待修复的代码。所有文件 / 命令 / git 访问都经由此处，
    因此内核永远不会 import ``subprocess``，也不需要知道 git 是什么。"""

    # 读一个文件的内容（比如把有 bug 的源码读出来给 LLM 看）
    def read_file(self, path: str) -> str: ...

    # 把新内容写进某个文件（覆盖式写入）
    def write_file(self, path: str, content: str) -> None: ...

    # 把一份“补丁提案”应用到代码上（批量改多个文件），返回被改动的文件列表
    def apply_patch(self, proposal: PatchProposal) -> list[str]: ...

    # 在这个仓库里跑一条命令（比如 pytest 跑测试，看修没修好），返回退出码 + 输出
    def run(self, command: list[str]) -> CommandResult: ...

    # 确保切到某个 git 分支（在专用分支上改，不碰主干）；分支不存在就创建
    def ensure_branch(self, name: str) -> None: ...

    # 返回当前所在的 git 分支名
    def current_branch(self) -> str: ...


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
    """一个阶段的行为。按名称注册；编排器从清单（manifest）的 ``executor`` 字段
    查找它，绝不硬编码任何具体阶段。"""

    def run(self, ctx: ExecutionContext) -> ExecutionOutput: ...


# --------------------------------------------------------------------------
# 内联测试（运行方式：python3 -m core.ports）
# --------------------------------------------------------------------------
def test_command_result_ok():
    assert CommandResult(0).ok
    assert not CommandResult(1).ok


def test_runtime_checkable_ports_accept_duck_types():
    class _LLM:
        def complete(self, prompt: str) -> str:
            return "ok"

    class _Exec:
        def run(self, ctx):
            return ExecutionOutput(content="x")

    assert isinstance(_LLM(), LLMClient)
    assert isinstance(_Exec(), PhaseExecutor)
    # 缺少对应方法的类会被拒绝。
    assert not isinstance(object(), LLMClient)


def test_execution_output_defaults():
    out = ExecutionOutput(content="hello")
    assert out.status is PhaseStatus.SUCCEEDED
    assert out.proposal is None
    assert out.extra_artifacts == {}


if __name__ == "__main__":
    import sys

    from testkit import run_module_tests

    exit_code = run_module_tests(globals())
    sys.exit(exit_code)

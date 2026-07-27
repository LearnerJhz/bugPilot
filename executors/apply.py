"""apply 执行器：**确定性**阶段，把 fix 阶段 Agent 已落在工作区的改动固化成产物记录。

在引擎驱动模型里，代码改动是上一阶段（fix）那个 Agent 直接在工作区里写好的；apply
不再"应用补丁"，只做一件确定性的事：抓取当前改动概览（``git diff --stat``）写进
``04-apply.md``，作为"改了什么"的台账。不起 Agent、不调模型。
"""

from core.models import utc_now_iso
from core.ports import ExecutionContext, GateResult, PhasePrep
from core.registry import register


@register("apply")
class ApplyExecutor:
    def prepare(self, ctx: ExecutionContext) -> PhasePrep:
        diff_stat = _git_diff_stat(ctx)
        lines = [
            f"# Apply — {ctx.task_id}",
            "",
            "## Applied",
            f"- Applied at: {utc_now_iso()}",
            "",
            "### Changed files (git diff --stat)",
            "```",
            diff_stat or "(无改动或非 git 工作区)",
            "```",
            "",
        ]
        return PhasePrep(artifact="\n".join(lines), prompt_text="", notes=["apply: deterministic"])

    def gate(self, ctx: ExecutionContext, artifact: str) -> GateResult:
        missing = [s for s in ctx.spec.required_sections if s not in artifact]
        passed = not missing
        return GateResult(
            passed=passed,
            confidence=1.0 if passed else 0.0,
            missing_sections=missing,
        )


def _git_diff_stat(ctx: ExecutionContext) -> str:
    try:
        res = ctx.workspace.run(["git", "diff", "--stat"])
        return (res.stdout or "").strip()
    except Exception:  # pragma: no cover
        return ""


# --------------------------------------------------------------------------
# 内联测试（python3 -m executors.apply）
# --------------------------------------------------------------------------
def _make_ctx() -> ExecutionContext:
    from core.models import PhaseSpec

    class _WS:
        def root_path(self):
            return "/tmp/repo"

        def run(self, command):
            from core.ports import CommandResult

            return CommandResult(0, stdout=" a.py | 2 +-\n 1 file changed")

    class _Store:
        def write(self, t, n, c):
            return n

        def read(self, t, n):
            return ""

        def exists(self, t, n):
            return False

        def path_for(self, t, n):
            return n

    spec = PhaseSpec(
        id="apply", executor="apply", output="04-apply.md",
        required_sections=["## Applied"],
    )
    return ExecutionContext(
        task_id="T-1", spec=spec, description="x",
        workspace=_WS(), store=_Store(),
    )


def test_apply_prepare_has_section_and_diff():
    prep = ApplyExecutor().prepare(_make_ctx())
    assert "## Applied" in prep.artifact
    assert "1 file changed" in prep.artifact
    assert prep.prompt_text == ""  # 确定性阶段：不起 Agent


def test_apply_gate():
    ctx = _make_ctx()
    prep = ApplyExecutor().prepare(ctx)
    assert ApplyExecutor().gate(ctx, prep.artifact).passed
    assert not ApplyExecutor().gate(ctx, "no section").passed


if __name__ == "__main__":
    import sys

    from testkit import run_module_tests

    sys.exit(run_module_tests(globals()))

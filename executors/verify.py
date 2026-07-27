"""verify 执行器：**确定性**阶段，在工作区里跑验证命令，判定修复是否成立。

不起 Agent、不调模型：引擎直接执行 manifest 里 ``project.verify_command`` 声明的
确定性命令（如跑测试），据退出码写出 ``05-verify.md`` 的 Verdict/Evidence。

关键：``gate`` 不只看章节齐不齐，还看 **Verdict 是不是 pass**——verdict=fail 会让本
阶段未过闸，从而触发 manifest 里 ``retry_on_fail`` 配置的回滚（通常回到 analyze 重来）。
"""

import re

from core.models import utc_now_iso
from core.ports import ExecutionContext, GateResult, PhasePrep
from core.registry import register


@register("verify")
class VerifyExecutor:
    def prepare(self, ctx: ExecutionContext) -> PhasePrep:
        command = list(ctx.project.get("verify_command", []) or [])

        if not command:
            verdict, evidence = "fail", "manifest 未配置 project.verify_command，无法验证。"
        else:
            exit_code, output = _run(ctx, command)
            verdict = "pass" if exit_code == 0 else "fail"
            evidence = f"$ {' '.join(command)}\n(exit={exit_code})\n{output}".strip()

        lines = [
            f"# Verify — {ctx.task_id}",
            "",
            "## Verdict",
            verdict,
            "",
            "## Evidence",
            "```",
            evidence,
            "```",
            "",
            f"_verified at {utc_now_iso()}_",
            "",
        ]
        return PhasePrep(artifact="\n".join(lines), prompt_text="", notes=[f"verify: {verdict}"])

    def gate(self, ctx: ExecutionContext, artifact: str) -> GateResult:
        missing = [s for s in ctx.spec.required_sections if s not in artifact]
        verdict_pass = bool(re.search(r"##\s*Verdict\s*\n+\s*pass\b", artifact, re.IGNORECASE))
        passed = (not missing) and verdict_pass
        notes = []
        if missing:
            notes.append(f"缺少必需章节: {missing}")
        if not verdict_pass:
            notes.append("Verdict 非 pass（验证未通过）")
        return GateResult(
            passed=passed,
            confidence=1.0 if passed else 0.0,
            missing_sections=missing,
            notes=notes,
        )


def _run(ctx: ExecutionContext, command: list[str]) -> tuple[int, str]:
    try:
        res = ctx.workspace.run(command)
        return res.exit_code, (res.stdout or "") + (res.stderr or "")
    except Exception as exc:  # pragma: no cover
        return 1, f"运行验证命令异常: {exc}"


# --------------------------------------------------------------------------
# 内联测试（python3 -m executors.verify）
# --------------------------------------------------------------------------
def _make_ctx(exit_code: int) -> ExecutionContext:
    from core.models import PhaseSpec
    from core.ports import CommandResult

    class _WS:
        def root_path(self):
            return "/tmp/repo"

        def run(self, command):
            return CommandResult(exit_code, stdout="ran tests")

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
        id="verify", executor="verify", output="05-verify.md",
        required_sections=["## Verdict", "## Evidence"],
    )
    return ExecutionContext(
        task_id="T-1", spec=spec, description="x",
        workspace=_WS(), store=_Store(),
        project={"verify_command": ["true"]},
    )


def test_verify_pass_when_command_succeeds():
    ctx = _make_ctx(0)
    prep = VerifyExecutor().prepare(ctx)
    assert "## Verdict\npass" in prep.artifact
    assert VerifyExecutor().gate(ctx, prep.artifact).passed


def test_verify_fail_blocks_gate():
    ctx = _make_ctx(1)
    prep = VerifyExecutor().prepare(ctx)
    assert "## Verdict\nfail" in prep.artifact
    gate = VerifyExecutor().gate(ctx, prep.artifact)
    assert not gate.passed  # 章节齐全但 verdict=fail → 未过闸 → 触发回滚


if __name__ == "__main__":
    import sys

    from testkit import run_module_tests

    sys.exit(run_module_tests(globals()))

"""intake 执行器：流水线的第一个、**确定性**阶段（不调 Agent、不调模型）。

职责：把用户丢进来的原始请求（一个 issue URL 或一段自由文本）规整成结构化的
任务简报 ``01-intake.md``，作为后续所有阶段的上下文源头。

这是「确定性阶段」的样板，和 AI 阶段（analyze/fix）的关键区别：

- ``prepare()`` 直接产出**最终产物**——不含 ``AGENT_PENDING_SENTINEL`` 哨兵、
  也不编译 ``prompt_text``。因此引擎写盘后不会短路等待，而是立刻进入 ``gate``。
- ``gate()`` 只做**合规校验**：manifest 里声明的 ``required_sections`` 是否齐全，
  不判断内容「对不对」（那不是引擎的职责）。

对照 AI 阶段：AI 阶段的 ``prepare`` 会返回带哨兵的占位产物 + 一份 ``prompt_text``，
引擎写盘后检测到哨兵 → 标记 PENDING → 退出，等外部 Agent 读 prompt、写回产物。
"""

from core.models import utc_now_iso
from core.ports import ExecutionContext, GateResult, PhasePrep
from core.registry import register


@register("intake")
class IntakeExecutor:
    """确定性执行器：接收原始 issue，产出规整后的 ``01-intake.md``。"""

    # ---- 第一段：准备产物（确定性阶段直接给最终产物，无 prompt、无哨兵）----
    def prepare(self, ctx: ExecutionContext) -> PhasePrep:
        raw = (ctx.description or "").strip()

        # 轻量判别：看起来像 URL 就当成 issue 链接记录，否则当自由文本描述。
        # （抓取远端 issue 正文属于后续增强，这里只做确定性规整，不触网。）
        is_url = raw.startswith("http://") or raw.startswith("https://")
        summary = raw.splitlines()[0] if raw else "(no description provided)"

        # 工作区根目录写进简报，方便 Agent 后续阶段知道去哪儿改码。
        try:
            root = ctx.workspace.root_path()
        except Exception:
            root = "(unknown)"

        input_line = f"- Issue URL: {raw}" if is_url else f"- Request: {raw or '(empty)'}"

        lines = [
            f"# Intake — {ctx.task_id}",
            "",
            "## Summary",
            summary,
            "",
            "## Inputs",
            f"- Task ID: {ctx.task_id}",
            input_line,
            f"- Workspace: {root}",
            f"- Intake at: {utc_now_iso()}",
            "",
        ]
        artifact = "\n".join(lines)

        # prompt_text 留空 = 确定性阶段，不劳驾 Agent；artifact 不含哨兵，引擎直接 gate。
        return PhasePrep(
            artifact=artifact,
            prompt_text="",
            notes=["intake: deterministic phase, no agent involved"],
        )

    # ---- 第二段：看门（校验必需章节是否齐全）----
    def gate(self, ctx: ExecutionContext, artifact: str) -> GateResult:
        required = ctx.spec.required_sections
        # 用字符串包含匹配（所以 manifest 里的标题要固定、别翻译）。
        missing = [section for section in required if section not in artifact]
        passed = not missing
        return GateResult(
            passed=passed,
            confidence=1.0 if passed else 0.0,
            missing_sections=missing,
            notes=[] if passed else [f"缺少必需章节: {missing}"],
        )


# --------------------------------------------------------------------------
# 内联测试（运行方式：python3 -m phases.intake）
#
# 用最小 fake 的 Workspace/ArtifactStore 拼出 ExecutionContext，
# 只验证 intake 自己的行为，不依赖真实文件系统或 git。
# --------------------------------------------------------------------------
def _make_ctx(description: str) -> ExecutionContext:
    from core.models import PhaseSpec

    class _FakeWorkspace:
        def root_path(self) -> str:
            return "/tmp/ws"

        def run(self, command):  # pragma: no cover - 未在 intake 中使用
            ...

    class _FakeStore:
        def write(self, task_id, name, content):
            return name

        def read(self, task_id, name):
            return ""

        def exists(self, task_id, name):
            return False

        def path_for(self, task_id, name):
            return name

    spec = PhaseSpec(
        id="intake",
        executor="intake",
        output="01-intake.md",
        required_sections=["## Summary", "## Inputs"],
    )
    return ExecutionContext(
        task_id="T-1",
        spec=spec,
        description=description,
        workspace=_FakeWorkspace(),
        store=_FakeStore(),
    )


def test_intake_prepare_has_required_sections():
    prep = IntakeExecutor().prepare(_make_ctx("Fix the login crash"))
    assert "## Summary" in prep.artifact
    assert "## Inputs" in prep.artifact


def test_intake_prepare_is_deterministic_no_prompt_no_sentinel():
    from core.models import AGENT_PENDING_SENTINEL

    prep = IntakeExecutor().prepare(_make_ctx("anything"))
    assert prep.prompt_text == ""                       # 确定性阶段：无 prompt
    assert AGENT_PENDING_SENTINEL not in prep.artifact  # 无哨兵：引擎不会等 Agent


def test_intake_detects_url_input():
    prep = IntakeExecutor().prepare(_make_ctx("https://github.com/foo/bar/issues/42"))
    assert "Issue URL: https://github.com/foo/bar/issues/42" in prep.artifact


def test_intake_records_freeform_text_as_request():
    prep = IntakeExecutor().prepare(_make_ctx("app crashes on startup"))
    assert "Request: app crashes on startup" in prep.artifact


def test_intake_gate_passes_on_full_artifact():
    ctx = _make_ctx("something")
    prep = IntakeExecutor().prepare(ctx)
    gate = IntakeExecutor().gate(ctx, prep.artifact)
    assert gate.passed
    assert gate.missing_sections == []
    assert gate.confidence == 1.0


def test_intake_gate_fails_on_missing_section():
    ctx = _make_ctx("x")
    gate = IntakeExecutor().gate(ctx, "## Summary only, no inputs")
    assert not gate.passed
    assert "## Inputs" in gate.missing_sections


if __name__ == "__main__":
    import sys

    from testkit import run_module_tests

    sys.exit(run_module_tests(globals()))

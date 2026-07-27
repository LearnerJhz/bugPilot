"""AgentExecutor：AI 阶段的通用执行器（analyze / fix 共用）。

这是"引擎驱动 Agent"里 executor 的标准形态——它**自己不动脑、也不起进程**，
只干两件确定性的事：

1. ``prepare(ctx)``：把 [任务描述 + 前置产物 + 工作区 + 产物落盘路径 + 必需章节 +
   （重试时的）反馈 + 硬约束] 编译成一份完整 prompt。引擎随后会拿它去起一个 Agent
   进程（``claude -p``）自走执行——**代码改动和产物落盘都由那个 Agent 完成**。
2. ``gate(ctx, artifact)``：Agent 写回产物后，校验必需章节是否齐全。

同一个类按 manifest 的 ``executor`` 名注册成 analyze / fix；两者的差异（提示词、
必需章节、置信度门）全部来自 manifest 与 ``prompts/<phase>.md``，代码零分叉。
"""

from __future__ import annotations

import re

from core.manifest import PROJECT_ROOT
from core.ports import ExecutionContext, GateResult, PhasePrep
from core.registry import register


@register("analyze")
@register("fix")
class AgentExecutor:
    """AI 阶段通用执行器：编译 prompt（供引擎起 Agent 自走）+ 产物看门。"""

    def prepare(self, ctx: ExecutionContext) -> PhasePrep:
        prompt = _compile_prompt(ctx)
        # AI 阶段：只给 prompt，不预写产物——产物由引擎起的 Agent 自己写到 artifact_path。
        return PhasePrep(
            artifact="",
            prompt_text=prompt,
            notes=[f"{ctx.spec.id}: AI phase, engine will spawn an agent to run this prompt"],
        )

    def gate(self, ctx: ExecutionContext, artifact: str) -> GateResult:
        required = ctx.spec.required_sections
        missing = [section for section in required if section not in artifact]
        passed = not missing
        conf = _parse_confidence(artifact)
        return GateResult(
            passed=passed,
            confidence=conf if conf is not None else (1.0 if passed else 0.0),
            missing_sections=missing,
            notes=[] if passed else [f"缺少必需章节: {missing}"],
        )


def _parse_confidence(text: str) -> float | None:
    # 允许 "Confidence" 与数字之间隔着换行/冒号/星号等（如 `## Confidence\n0.9`）。
    m = re.search(r"Confidence[^0-9]{0,20}?([01](?:\.\d+)?|0?\.\d+)", text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _load_template(ctx: ExecutionContext) -> str:
    """读阶段提示词模板（prompts/<phase>.md）；缺失时给一句兜底，不让流程崩。"""
    rel = ctx.spec.prompt
    if not rel:
        return ""
    path = PROJECT_ROOT / rel
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return f"(未找到提示词模板 {rel}，请按下方通用约束完成本阶段。)"


def _compile_prompt(ctx: ExecutionContext) -> str:
    """把模板 + 上下文 + 硬约束拼成交给 Agent 的完整 prompt。"""
    template = _load_template(ctx)
    artifact_path = ctx.store.path_for(ctx.task_id, ctx.spec.output)
    workspace_root = _safe_root(ctx)
    required = ctx.spec.required_sections
    prior = ctx.prior_artifacts
    feedback = ctx.retry_feedback

    parts: list[str] = []
    parts.append(f"# 阶段任务：{ctx.spec.id}")
    parts.append("")
    parts.append("你是一个被引擎调度的自走 Agent。请**独立完成本阶段的全部工作**：读代码、")
    parts.append("必要时改代码、跑命令定位问题，最后把结论写成产物文件。不要向人提问、不要中途停下。")
    parts.append("")

    parts.append("## 本次任务")
    parts.append(ctx.description or "(无描述)")
    parts.append("")

    parts.append("## 工作区（在这里读/改代码）")
    parts.append(f"- 代码根目录: {workspace_root}")
    parts.append("")

    if prior:
        parts.append("## 前置阶段产物（上下文，供你参考）")
        for name, content in prior:
            parts.append(f"### {name}")
            parts.append(content.strip())
            parts.append("")

    if feedback:
        parts.append("## ⚠️ 上一轮失败反馈（最高优先级，必须规避）")
        parts.append(feedback.strip())
        parts.append("")

    parts.append("## 阶段提示词")
    parts.append(template or "(无)")
    parts.append("")

    parts.append("## 硬约束（务必遵守）")
    parts.append(f"1. 把本阶段结论写入产物文件（覆盖写）：`{artifact_path}`")
    parts.append("2. 该产物**必须包含以下二级标题**（原文，勿翻译/改写，引擎按字符串校验）：")
    for section in required:
        parts.append(f"   - `{section}`")
    parts.append("3. 只改与本 bug 相关的代码，改动落在上面的工作区里。")
    parts.append("4. 全程自主完成，不要询问、不要等待确认。")
    parts.append("")
    parts.append(f"完成后请确认产物文件 `{artifact_path}` 已按要求写好。")

    return "\n".join(parts)


def _safe_root(ctx: ExecutionContext) -> str:
    try:
        return ctx.workspace.root_path()
    except Exception:  # pragma: no cover
        return "(unknown)"


# --------------------------------------------------------------------------
# 内联测试（运行方式：python3 -m executors.agent_executor）
# --------------------------------------------------------------------------
def _make_ctx(phase_id: str, required: list[str], *, feedback: str | None = None) -> ExecutionContext:
    from core.models import PhaseSpec

    class _WS:
        def root_path(self) -> str:
            return "/tmp/repo"

        def run(self, command): ...

    class _Store:
        def write(self, t, n, c):
            return n

        def read(self, t, n):
            return ""

        def exists(self, t, n):
            return False

        def path_for(self, t, n):
            return f"/abs/tasks/{t}/{n}"

    spec = PhaseSpec(
        id=phase_id, executor=phase_id, output=f"02-{phase_id}.md",
        required_sections=required, prompt=None,
    )
    return ExecutionContext(
        task_id="T-1", spec=spec, description="登录空密码崩溃",
        workspace=_WS(), store=_Store(),
        prior_artifacts=[("01-intake.md", "## Summary\nx")], retry_feedback=feedback,
    )


def test_prepare_is_ai_phase_with_prompt_and_no_artifact():
    prep = AgentExecutor().prepare(_make_ctx("analyze", ["## Root Cause", "## Plan"]))
    assert prep.prompt_text != ""
    assert prep.artifact == ""
    assert "## Root Cause" in prep.prompt_text
    assert "/abs/tasks/T-1/02-analyze.md" in prep.prompt_text  # 产物落盘绝对路径已注入


def test_prepare_injects_prior_and_feedback():
    prep = AgentExecutor().prepare(
        _make_ctx("analyze", ["## Root Cause"], feedback="上轮根因猜错了")
    )
    assert "01-intake.md" in prep.prompt_text          # 前置产物
    assert "上轮根因猜错了" in prep.prompt_text          # 重试反馈


def test_gate_checks_sections_and_confidence():
    ctx = _make_ctx("analyze", ["## Root Cause", "## Confidence"])
    ok = AgentExecutor().gate(ctx, "## Root Cause\nnull deref\n## Confidence\n0.9\n")
    assert ok.passed and ok.confidence == 0.9
    bad = AgentExecutor().gate(ctx, "## Root Cause only")
    assert not bad.passed and "## Confidence" in bad.missing_sections


if __name__ == "__main__":
    import sys

    from testkit import run_module_tests

    sys.exit(run_module_tests(globals()))

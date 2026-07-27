"""FakeAgentRunner：**自测专用**的假 Agent（不调任何模型）。

用途：在**不接真 Agent**的情况下验证引擎编排本身是否正确——依赖门、起 Agent 的
时机、gate/置信度门、失败回滚重试、状态机与断点续跑。它实现和真 Agent 一样的
``AgentRunner`` 接口，但不起进程、不动脑，只做一件确定性的事：

    按引擎交来的 prompt 里声明的"产物路径 + 必需二级标题"，写出一份**恰好能过闸**
    的桩产物（含高置信度）。

这样 ``python3 -m cli run <task> --fake-agent`` 就能像生产一样启动引擎、看它自动
驱动一个个 phase 跑完，而每个 AI 阶段的"活"由这个桩顶替。它是纯自测件，不参与
真实修 bug；真跑请用 ``ClaudeAgentRunner``。
"""

from __future__ import annotations

import re
from pathlib import Path

from core.ports import AgentRunResult, Workspace


class FakeAgentRunner:
    """假 Agent：解析 prompt → 写一份能过闸的桩产物。仅供自测引擎编排。"""

    def __init__(self, *, confidence: float = 0.9, fail_phases: tuple[str, ...] = ()) -> None:
        # confidence：写进产物的置信度（默认 0.9，够过 analyze 的 0.6 门）。
        # fail_phases：这些阶段故意"跑挂"（返回非 0），用来演示引擎的失败/回滚分支。
        self.confidence = confidence
        self.fail_phases = tuple(fail_phases)
        self.calls: list[str] = []  # 记录每次被起来的阶段，便于断言引擎调度顺序

    def run(
        self,
        *,
        prompt: str,
        workspace: Workspace,
        phase_id: str,
        timeout_s: int | None = None,
    ) -> AgentRunResult:
        self.calls.append(phase_id)

        if phase_id in self.fail_phases:
            return AgentRunResult(
                exit_code=1, stderr=f"[fake-agent] 故意让阶段 '{phase_id}' 失败（自测用）。"
            )

        path = _artifact_path(prompt)
        if path is None:
            return AgentRunResult(
                exit_code=1, stderr="[fake-agent] 无法从 prompt 解析产物路径。"
            )

        sections = _required_sections(prompt)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_stub_artifact(phase_id, sections, self.confidence), encoding="utf-8")
        return AgentRunResult(exit_code=0, stdout=f"[fake-agent] wrote {path}")


def _artifact_path(prompt: str) -> Path | None:
    # 对应 AgentExecutor 里硬约束第 1 条："...写入产物文件（覆盖写）：`<abs path>`"
    m = re.search(r"覆盖写）：`([^`]+)`", prompt)
    return Path(m.group(1)) if m else None


def _required_sections(prompt: str) -> list[str]:
    # 抓 prompt 里所有反引号包着、以 ## 开头的二级标题（模板与硬约束里都会出现），去重保序。
    found = re.findall(r"`(##[^`]+)`", prompt)
    ordered: list[str] = []
    for s in found:
        s = s.strip()
        if s not in ordered:
            ordered.append(s)
    return ordered


def _stub_artifact(phase_id: str, sections: list[str], confidence: float) -> str:
    lines = [f"# {phase_id} — fake-agent stub（自测桩，非真实结论）", ""]
    if not sections:
        lines.append("(prompt 未声明必需章节，写个占位内容)")
        return "\n".join(lines) + "\n"
    for s in sections:
        lines.append(s)
        if s.lower().rstrip().endswith("confidence"):
            lines.append(str(confidence))
        elif s.lower().rstrip().endswith("verdict"):
            lines.append("pass")
        else:
            lines.append(f"(fake stub for {s})")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 内联测试（运行方式：python3 -m adapters.fake_agent_runner）
# --------------------------------------------------------------------------
class _WS:
    def root_path(self) -> str:
        return "/tmp/repo"

    def run(self, command): ...


def test_writes_artifact_with_required_sections(tmp=None):
    import tempfile

    d = Path(tempfile.mkdtemp())
    out = d / "02-analyze.md"
    prompt = (
        f"...写入产物文件（覆盖写）：`{out}`\n"
        "必须包含：\n   - `## Root Cause`\n   - `## Confidence`\n   - `## Plan`\n"
    )
    res = FakeAgentRunner(confidence=0.8).run(
        prompt=prompt, workspace=_WS(), phase_id="analyze"
    )
    assert res.ok
    text = out.read_text(encoding="utf-8")
    assert "## Root Cause" in text and "## Plan" in text
    assert "## Confidence\n0.8" in text


def test_fail_phases_returns_nonzero():
    res = FakeAgentRunner(fail_phases=("fix",)).run(
        prompt="覆盖写）：`/tmp/x.md`", workspace=_WS(), phase_id="fix"
    )
    assert not res.ok


if __name__ == "__main__":
    import sys

    from testkit import run_module_tests

    sys.exit(run_module_tests(globals()))

"""端到端冒烟（自测）：用假 Agent 驱动 run_pipeline，验证引擎主循环 + 置信度门 + 回滚重试。

复用 ``adapters.fake_agent_runner.FakeAgentRunner``（唯一的假 Agent 文件），不接真模型。
也可直接命令行自测：``python3 -m cli run <task> --fake-agent --repo /tmp/xxx``。
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adapters.fake_agent_runner import FakeAgentRunner  # noqa: E402
from core.orchestrator import run_pipeline  # noqa: E402


class _RetryThenPassAgent(FakeAgentRunner):
    """analyze 第一次写低置信度（触发回滚），之后恢复高置信度——用来验证回滚重试。"""

    def run(self, *, prompt, workspace, phase_id, timeout_s=None):
        if phase_id == "analyze" and self.calls.count("analyze") == 0:
            self.confidence = 0.3
        else:
            self.confidence = 0.9
        return super().run(prompt=prompt, workspace=workspace, phase_id=phase_id, timeout_s=timeout_s)


def _run(task_id, agent):
    workdir = tempfile.mkdtemp(prefix="bugpilot-smoke-")
    result = run_pipeline(
        task_id, description="登录页空密码提交崩溃", repo=workdir, agent_runner=agent, log=print
    )
    shutil.rmtree(workdir, ignore_errors=True)
    shutil.rmtree(Path(__file__).resolve().parents[1] / "tasks" / task_id, ignore_errors=True)
    return result


def main():
    print("\n=== 场景1：一次通过 ===")
    a1 = FakeAgentRunner(confidence=0.9)
    r1 = _run("smoke_ok", a1)
    assert r1.status == "succeeded", r1.status
    assert a1.calls == ["analyze", "fix"], a1.calls  # 只有 AI 阶段起了 Agent
    print("场景1 OK：status=succeeded, agent calls =", a1.calls)

    print("\n=== 场景2：analyze 首轮置信度 0.3 触发回滚，第二轮 0.9 通过 ===")
    a2 = _RetryThenPassAgent()
    r2 = _run("smoke_retry", a2)
    assert r2.status == "succeeded", r2.status
    assert a2.calls.count("analyze") == 2, a2.calls
    assert r2.run_state.phase_results["analyze"].retry_count == 1
    print("场景2 OK：analyze 重试 1 次后通过, agent calls =", a2.calls)

    print("\n✅ 冒烟全部通过")


if __name__ == "__main__":
    main()

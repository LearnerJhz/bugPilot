"""AgentRunner 适配器：引擎驱动 Agent（路线B）的"起进程"这一环。

引擎不自己"动脑"，但它**亲自起一个自带工具的 Agent 进程**去动脑——这里就是那个
接缝的具体实现。默认实现 ``ClaudeAgentRunner`` 用 ``subprocess`` 起 ``claude -p``
（Claude Code headless），让它在目标工作区里自走：读代码、改代码、跑命令，并把本
阶段的结论写进指定的产物文件。引擎阻塞等它跑完，回收退出码/输出。

**模型无关**：这里只起 ``claude`` 这个 CLI，"背后用哪个模型"完全由环境变量决定
（例如把 ``ANTHROPIC_BASE_URL`` 指向 DeepSeek 的 Anthropic 兼容端点）。所以想换模型
时改 env 即可，本文件一行都不用动——这正是"引擎/编排"与"模型选择"解耦的意义。

配置来自 manifest 的 ``project.agent``，例如::

    project:
      agent:
        cmd: claude                 # 起哪个 Agent CLI
        permission_mode: bypassPermissions  # 无头自走必须放开权限
        max_turns: 40               # 单阶段轮次上限，防跑飞
        timeout_s: 1800             # 子进程墙钟超时，挂了就 kill
        extra_args: []              # 追加给 CLI 的额外参数
        env: {}                     # 追加/覆盖的环境变量（如指向 DeepSeek）
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Optional

from core.ports import AgentRunResult, Workspace


class ClaudeAgentRunner:
    """用 ``subprocess`` 起 ``claude -p`` 的 AgentRunner 实现（路线B 默认）。"""

    def __init__(
        self,
        *,
        cmd: str = "claude",
        permission_mode: str = "bypassPermissions",
        max_turns: int = 40,
        timeout_s: int = 1800,
        extra_args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        self.cmd = cmd
        self.permission_mode = permission_mode
        self.max_turns = max_turns
        self.timeout_s = timeout_s
        self.extra_args = list(extra_args or [])
        self.env_overrides = dict(env or {})

    def _build_command(self) -> list[str]:
        # prompt 走 stdin（避免超长 prompt 撞 ARG_MAX），命令行只放 flag。
        argv = [self.cmd, "-p", "--permission-mode", self.permission_mode]
        if self.max_turns:
            argv += ["--max-turns", str(self.max_turns)]
        argv += self.extra_args
        return argv

    def _build_env(self) -> dict[str, str]:
        # 继承当前 shell 环境（DeepSeek 的 ANTHROPIC_* 通常就设在这里），再叠加覆盖项。
        env = dict(os.environ)
        env.update(self.env_overrides)
        return env

    def run(
        self,
        *,
        prompt: str,
        workspace: Workspace,
        phase_id: str,
        timeout_s: Optional[int] = None,
    ) -> AgentRunResult:
        argv = self._build_command()
        cwd = workspace.root_path()
        limit = timeout_s if timeout_s is not None else self.timeout_s
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                input=prompt,
                env=self._build_env(),
                capture_output=True,
                text=True,
                timeout=limit,
            )
        except FileNotFoundError:
            # claude 没装 / 不在 PATH：给一条清晰的错误，让引擎判 FAILED 而不是崩栈。
            return AgentRunResult(
                exit_code=127,
                stderr=(
                    f"找不到 Agent CLI '{self.cmd}'。请先安装 Claude Code 并确保在 PATH 中，"
                    f"或在 manifest 的 project.agent.cmd 里改成正确的命令。"
                ),
                duration_s=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired as exc:
            # 超时：subprocess.run 已负责 kill 子进程，这里只如实上报。
            return AgentRunResult(
                exit_code=124,
                stdout=exc.stdout or "",
                stderr=(exc.stderr or "") + f"\n[timeout] Agent 在 {limit}s 内未跑完，已终止。",
                duration_s=time.monotonic() - started,
                timed_out=True,
            )
        return AgentRunResult(
            exit_code=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_s=time.monotonic() - started,
        )


def build_agent_runner(project: dict[str, Any]) -> ClaudeAgentRunner:
    """从 manifest 的 ``project`` 配置装配一个 AgentRunner。

    只认 ``project.agent`` 这一小块；缺省时用一套对无头自走安全的默认值。
    """
    agent_cfg = dict((project or {}).get("agent", {}) or {})
    return ClaudeAgentRunner(
        cmd=agent_cfg.get("cmd", "claude"),
        permission_mode=agent_cfg.get("permission_mode", "bypassPermissions"),
        max_turns=int(agent_cfg.get("max_turns", 40)),
        timeout_s=int(agent_cfg.get("timeout_s", 1800)),
        extra_args=list(agent_cfg.get("extra_args", []) or []),
        env=dict(agent_cfg.get("env", {}) or {}),
    )


# --------------------------------------------------------------------------
# 内联测试（运行方式：python3 -m adapters.agent_runner）
#
# 不真的起 claude —— 用一个假 workspace + 一个绝对存在的命令来验证装配与退出信息。
# --------------------------------------------------------------------------
class _FakeWorkspace:
    def __init__(self, root: str = ".") -> None:
        self._root = root

    def root_path(self) -> str:
        return self._root

    def run(self, command):  # pragma: no cover
        ...


def test_build_agent_runner_defaults():
    runner = build_agent_runner({})
    assert runner.cmd == "claude"
    assert runner.permission_mode == "bypassPermissions"
    assert runner.max_turns == 40


def test_build_agent_runner_reads_config():
    runner = build_agent_runner(
        {"agent": {"cmd": "mycli", "max_turns": 5, "timeout_s": 10, "extra_args": ["--x"]}}
    )
    assert runner.cmd == "mycli"
    assert runner.max_turns == 5
    assert runner.timeout_s == 10
    assert "--x" in runner._build_command()


def test_missing_cli_reports_127():
    runner = ClaudeAgentRunner(cmd="definitely-not-a-real-binary-xyz")
    res = runner.run(prompt="hi", workspace=_FakeWorkspace(), phase_id="analyze")
    assert res.exit_code == 127
    assert not res.ok
    assert "找不到 Agent CLI" in res.stderr


def test_real_command_roundtrip():
    # 用 `cat` 冒充 Agent：它把 stdin（prompt）原样回显到 stdout，退出码 0。
    runner = ClaudeAgentRunner(cmd="cat", permission_mode="x", max_turns=0, extra_args=[])
    # cat 不认识 -p/--permission-mode，这些会被当普通参数；为纯测通路，换成无参 cat：
    runner._build_command = lambda: ["cat"]  # type: ignore[method-assign]
    res = runner.run(prompt="hello-agent", workspace=_FakeWorkspace(), phase_id="analyze")
    assert res.ok
    assert "hello-agent" in res.stdout


if __name__ == "__main__":
    import sys

    from testkit import run_module_tests

    sys.exit(run_module_tests(globals()))

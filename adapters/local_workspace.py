"""LocalWorkspace：``Workspace`` 端口的本地实现。

说白了它只回答两个最基础的问题，一共就这两个方法：

1. ``root_path()`` —— **agent 去哪个目录修 bug？** 引擎起 ``claude`` 进程时得有个
   工作目录（cwd），claude 才知道去哪读代码、改代码。这里就是把那个目录告诉它。
2. ``run(command)`` —— **修完怎么确认修好了？** 在那个目录里跑确定性命令
   （verify 阶段的测试命令、apply 阶段的 ``git diff``），把退出码和输出拿回来。

职责边界（见 ``core.ports.Workspace``）：它**不读也不写源码**（改代码是外部 Agent
的活），也**不碰 git 分支、不切目录、不提交**——分支由你自己提前切好，引擎只在你
当前所在的这个目录里干活。
"""

from __future__ import annotations  # 让 str | Path 之类注解延迟求值，兼容 <3.10

import subprocess
from pathlib import Path

from core.ports import CommandResult


class LocalWorkspace:
    """本地目录 + 子进程命令。实现 ``core.ports.Workspace``。"""

    def __init__(self, root: str | Path) -> None:
        self._root = str(Path(root).resolve())

    # 给 claude 进程当工作目录（cwd）用：agent 在这个目录里读代码、改代码。
    def root_path(self) -> str:
        return self._root

    # 在这个目录里跑一条确定性命令（verify 的测试 / apply 的 git diff），
    # 返回退出码 + 输出，供引擎判断"修好没"和记录"改了啥"。
    def run(self, command: list[str]) -> CommandResult:
        proc = subprocess.run(
            command,
            cwd=self._root,
            check=False,
            capture_output=True,
            text=True,
        )
        return CommandResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

"""PromoteStore：``ArtifactStore`` 端口的文件系统实现（黑板）。

把每个任务的产物存到 ``<base>/<task_id>/<name>`` 下，例如
``tasks/demo1/01-intake.md``、``tasks/demo1/_prompt_analyze.md``。

这是"确定性适配器"：引擎只依赖 ``core.ports.ArtifactStore`` 协议，
换成 S3、数据库等其它实现时，引擎一行都不用改。
"""

from __future__ import annotations  # 让 str | Path 之类注解延迟求值，兼容 <3.10

from pathlib import Path

from core.manifest import PROJECT_ROOT


class PromoteStore:
    """按 task_id 归档产物到本地磁盘。实现 ``core.ports.ArtifactStore``。"""

    def __init__(self, base_dir: str | Path = "tasks") -> None:
        base = Path(base_dir)
        # 相对路径一律锚定到项目根，避免受调用时 cwd 影响。
        self._base = base if base.is_absolute() else (PROJECT_ROOT / base)

    def _path(self, task_id: str, name: str) -> Path:
        return self._base / task_id / name

    def path_for(self, task_id: str, name: str) -> str:
        return str(self._path(task_id, name))

    def write(self, task_id: str, name: str, content: str) -> str:
        path = self._path(task_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    def read(self, task_id: str, name: str) -> str:
        return self._path(task_id, name).read_text(encoding="utf-8")

    def exists(self, task_id: str, name: str) -> bool:
        return self._path(task_id, name).exists()


# --------------------------------------------------------------------------
# 简单自测（运行方式：python3 -m adapters.promote_store）
# 写一段文字进去 → 读出来 → 打印。产物留在项目根的 testTemp/ 下可直接查看。
# --------------------------------------------------------------------------
if __name__ == "__main__":
    store = PromoteStore(PROJECT_ROOT / "testTemp")

    store.write("demo", "note.md", "你好，这是一段测试文字。")
    text = store.read("demo", "note.md")
    print(text)
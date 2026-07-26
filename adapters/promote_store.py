"""PromoteStore：``ArtifactStore`` 端口的文件系统实现（黑板），全局静态工具。

把每个任务的产物存到 ``<base>/<task_id>/<name>`` 下，例如
``tasks/demo1/01-intake.md``、``tasks/demo1/_prompt_analyze.md``。

它没有实例状态，本质上就是"按 (task_id, name) 算出路径并读/写文件"的工具人，
因此做成**全局唯一 + 静态方法**：直接 ``PromoteStore.write(...)`` 调用，不必到处
`new` 和传参。基目录是唯一的全局配置，通过 :func:`PromoteStore.configure` 设定
（默认 ``<项目根>/tasks``）。

它仍满足 ``core.ports.ArtifactStore`` 协议（方法都在类上，作为端口传给 executor
时把 ``PromoteStore`` 类本身当 store 即可），换成 S3、数据库等实现时引擎不用改。
"""

from __future__ import annotations  # 让 str | Path 之类注解延迟求值，兼容 <3.10

from pathlib import Path

from core.manifest import PROJECT_ROOT

# 全局唯一的产物基目录（相对路径一律锚定到项目根，避免受调用时 cwd 影响）。
_base: Path = PROJECT_ROOT / "tasks"


class PromoteStore:
    """按 task_id 归档产物到本地磁盘的全局静态工具。实现 ``core.ports.ArtifactStore``。"""

    @staticmethod
    def configure(base_dir: str | Path) -> None:
        """设定全局产物基目录（相对路径锚定到项目根）。整次运行装配时调一次即可。"""
        global _base
        base = Path(base_dir)
        _base = base if base.is_absolute() else (PROJECT_ROOT / base)

    @staticmethod
    def base_dir() -> Path:
        """当前全局产物基目录。"""
        return _base

    @staticmethod
    def _path(task_id: str, name: str) -> Path:
        return _base / task_id / name

    @staticmethod
    def path_for(task_id: str, name: str) -> str:
        return str(PromoteStore._path(task_id, name))

    @staticmethod
    def write(task_id: str, name: str, content: str) -> str:
        path = PromoteStore._path(task_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)

    @staticmethod
    def read(task_id: str, name: str) -> str:
        return PromoteStore._path(task_id, name).read_text(encoding="utf-8")

    @staticmethod
    def exists(task_id: str, name: str) -> bool:
        return PromoteStore._path(task_id, name).exists()

    @staticmethod
    def delete(task_id: str, name: str) -> None:
        # 回滚重试时清空 target..当前 的产物，让这些阶段下轮从头重跑。
        path = PromoteStore._path(task_id, name)
        if path.exists():
            path.unlink()


# --------------------------------------------------------------------------
# 简单自测（运行方式：python3 -m adapters.promote_store）
# 写一段文字进去 → 读出来 → 打印。产物留在项目根的 testTemp/ 下可直接查看。
# --------------------------------------------------------------------------
if __name__ == "__main__":
    PromoteStore.configure(PROJECT_ROOT / "testTemp")

    PromoteStore.write("demo", "note.md", "你好，这是一段测试文字。")
    text = PromoteStore.read("demo", "note.md")
    print(text)

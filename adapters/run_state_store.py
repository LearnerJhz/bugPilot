"""RunStateStore：``StateStore`` 端口的 JSON 文件实现，全局静态工具。

每个任务一份 ``<base>/<task_id>/run_state.json``，记录整次运行的黑板状态
（``RunState``），支持断点续跑与事后审计。

它没有实例状态，本质上就是"按 task_id 算出路径并读/写 JSON"的工具人，因此做成
**全局唯一 + 静态方法**：直接 ``RunStateStore.load(...)`` / ``RunStateStore.save(...)``
调用，不必到处 `new`。基目录是唯一的全局配置，通过 :func:`RunStateStore.configure`
设定（默认 ``<项目根>/tasks``）。

序列化逻辑放在适配器里，让 ``core.models`` 保持纯数据、不掺 IO。它仍满足
``core.ports.StateStore`` 协议（方法都在类上），作为端口传递时把类本身当 store 即可。
"""

from __future__ import annotations  # 让 str | Path 之类注解延迟求值，兼容 <3.10

import json
from pathlib import Path
from typing import Any, Optional

from core.manifest import PROJECT_ROOT
from core.models import PhaseResult, PhaseStatus, RunState, RunStatus

STATE_FILENAME = "run_state.json"

# 全局唯一的状态基目录（相对路径一律锚定到项目根，避免受调用时 cwd 影响）。
_base: Path = PROJECT_ROOT / "tasks"


def _result_to_dict(r: PhaseResult) -> dict[str, Any]:
    return {
        "phase_id": r.phase_id,
        "status": r.status.value,
        "output_path": r.output_path,
        "notes": list(r.notes),
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "retry_count": r.retry_count,
    }


# 旧产物里单阶段"没过"曾写作 "failed"，现已统一为 "blocked"；读旧文件时平滑迁移。
_LEGACY_PHASE_STATUS = {"failed": "blocked"}


def _result_from_dict(d: dict[str, Any]) -> PhaseResult:
    status_raw = d.get("status", "pending")
    status_raw = _LEGACY_PHASE_STATUS.get(status_raw, status_raw)
    return PhaseResult(
        phase_id=d["phase_id"],
        status=PhaseStatus(status_raw),
        output_path=d.get("output_path"),
        notes=list(d.get("notes", []) or []),
        started_at=d.get("started_at"),
        finished_at=d.get("finished_at"),
        retry_count=int(d.get("retry_count", 0) or 0),
    )


def _state_to_dict(s: RunState) -> dict[str, Any]:
    return {
        "task_id": s.task_id,
        "status": s.status.value,
        "description": s.description,
        "current_phase": s.current_phase,
        "phase_results": {pid: _result_to_dict(r) for pid, r in s.phase_results.items()},
        "started_at": s.started_at,
        "updated_at": s.updated_at,
    }


def _state_from_dict(d: dict[str, Any]) -> RunState:
    return RunState(
        task_id=d["task_id"],
        status=RunStatus(d.get("status", "idle")),
        description=d.get("description", ""),
        current_phase=d.get("current_phase"),
        phase_results={
            pid: _result_from_dict(rd)
            for pid, rd in (d.get("phase_results", {}) or {}).items()
        },
        started_at=d.get("started_at") or "",
        updated_at=d.get("updated_at") or "",
    )


class RunStateStore:
    """把 ``RunState`` 存成 JSON 的全局静态工具。实现 ``core.ports.StateStore``。"""

    @staticmethod
    def configure(base_dir: str | Path) -> None:
        """设定全局状态基目录（相对路径锚定到项目根）。整次运行装配时调一次即可。"""
        global _base
        base = Path(base_dir)
        _base = base if base.is_absolute() else (PROJECT_ROOT / base)

    @staticmethod
    def base_dir() -> Path:
        """当前全局状态基目录。"""
        return _base

    @staticmethod
    def _path(task_id: str) -> Path:
        return _base / task_id / STATE_FILENAME

    @staticmethod
    def load(task_id: str) -> Optional[RunState]:
        path = RunStateStore._path(task_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return _state_from_dict(data)

    @staticmethod
    def save(state: RunState) -> None:
        path = RunStateStore._path(state.task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_state_to_dict(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# --------------------------------------------------------------------------
# 简单自测（运行方式：python3 -m adapters.run_state_store）
# 造一个 RunState → 存起来 → 读回来 → 打印。产物落在项目根的 testTemp/ 下可直接查看。
# --------------------------------------------------------------------------
if __name__ == "__main__":
    RunStateStore.configure(PROJECT_ROOT / "testTemp")

    state = RunState(task_id="demo", description="登录页空密码崩溃", status=RunStatus.RUNNING)
    RunStateStore.save(state)

    loaded = RunStateStore.load("demo")
    print(loaded)

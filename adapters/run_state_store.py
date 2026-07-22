"""RunStateStore：``StateStore`` 端口的 JSON 文件实现。

每个任务一份 ``<base>/<task_id>/run_state.json``，记录整次运行的黑板状态
（``RunState``），支持断点续跑与事后审计。

序列化逻辑放在适配器里，让 ``core.models`` 保持纯数据、不掺 IO。
"""

from __future__ import annotations  # 让 str | Path 之类注解延迟求值，兼容 <3.10

import json
from pathlib import Path
from typing import Any, Optional

from core.manifest import PROJECT_ROOT
from core.models import PhaseResult, PhaseStatus, RunState, RunStatus

STATE_FILENAME = "run_state.json"


def _result_to_dict(r: PhaseResult) -> dict[str, Any]:
    return {
        "phase_id": r.phase_id,
        "status": r.status.value,
        "output_path": r.output_path,
        "notes": list(r.notes),
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }


def _result_from_dict(d: dict[str, Any]) -> PhaseResult:
    return PhaseResult(
        phase_id=d["phase_id"],
        status=PhaseStatus(d.get("status", "pending")),
        output_path=d.get("output_path"),
        notes=list(d.get("notes", []) or []),
        started_at=d.get("started_at"),
        finished_at=d.get("finished_at"),
    )


def _state_to_dict(s: RunState) -> dict[str, Any]:
    return {
        "task_id": s.task_id,
        "status": s.status.value,
        "description": s.description,
        "branch": s.branch,
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
        branch=d.get("branch", ""),
        current_phase=d.get("current_phase"),
        phase_results={
            pid: _result_from_dict(rd)
            for pid, rd in (d.get("phase_results", {}) or {}).items()
        },
        started_at=d.get("started_at") or "",
        updated_at=d.get("updated_at") or "",
    )


class RunStateStore:
    """把 ``RunState`` 存成 JSON。实现 ``core.ports.StateStore``。"""

    def __init__(self, base_dir: str | Path = "tasks") -> None:
        base = Path(base_dir)
        self._base = base if base.is_absolute() else (PROJECT_ROOT / base)

    def _path(self, task_id: str) -> Path:
        return self._base / task_id / STATE_FILENAME

    def load(self, task_id: str) -> Optional[RunState]:
        path = self._path(task_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return _state_from_dict(data)

    def save(self, state: RunState) -> None:
        path = self._path(state.task_id)
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
    store = RunStateStore(PROJECT_ROOT / "testTemp")

    state = RunState(task_id="demo", description="登录页空密码崩溃", status=RunStatus.RUNNING)
    store.save(state)

    loaded = store.load("demo")
    print(loaded)

"""In-memory store for agent runs paused at 'waiting_for_approval', keyed by
run_id, so the approval endpoint can resume the exact graph state."""
from __future__ import annotations

import threading


class ApprovalService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}

    def store_pending(self, run_id: str, state_snapshot: dict) -> None:
        with self._lock:
            self._pending[run_id] = state_snapshot

    def pop_pending(self, run_id: str) -> dict | None:
        with self._lock:
            return self._pending.pop(run_id, None)

    def peek_pending(self, run_id: str) -> dict | None:
        with self._lock:
            return self._pending.get(run_id)


_service = ApprovalService()


def get_approval_service() -> ApprovalService:
    return _service

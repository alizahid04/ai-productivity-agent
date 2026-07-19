"""In-process session memory.

Tracks, per session_id: recent chat turns, the most recent list of tasks
shown to the user (so "mark the second one complete" resolves correctly),
the most recent tool outputs, and lightweight user preferences.

This is intentionally in-memory (dict keyed by session_id) — swap for Redis
in a multi-worker production deployment.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class SessionState:
    messages: list[dict] = field(default_factory=list)
    last_listed_task_ids: list[str] = field(default_factory=list)
    last_tool_outputs: list[dict] = field(default_factory=list)
    preferences: dict = field(default_factory=dict)

    def resolve_task_reference(self, reference: str) -> str | None:
        """Resolve phrases like 'the second one', 'the first task', 'it' against
        the last list of task ids shown to the user in this session."""
        ref = reference.lower().strip()
        ordinals = {
            "first": 0, "1st": 0, "one": 0,
            "second": 1, "2nd": 1, "two": 1,
            "third": 2, "3rd": 2, "three": 2,
            "fourth": 3, "4th": 3, "four": 3,
            "fifth": 4, "5th": 4, "five": 4,
            "last": -1,
        }
        for word, idx in ordinals.items():
            if word in ref:
                if not self.last_listed_task_ids:
                    return None
                try:
                    return self.last_listed_task_ids[idx]
                except IndexError:
                    return None
        # direct id-looking reference
        if len(ref) >= 8 and "-" in ref:
            return reference
        return None


class SessionMemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionState] = {}

    def get(self, session_id: str) -> SessionState:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionState()
            return self._sessions[session_id]

    def add_message(self, session_id: str, role: str, content: str) -> None:
        state = self.get(session_id)
        state.messages.append({"role": role, "content": content})
        # Keep a bounded window to control prompt size.
        if len(state.messages) > 30:
            state.messages = state.messages[-30:]

    def remember_listed_tasks(self, session_id: str, task_ids: list[str]) -> None:
        self.get(session_id).last_listed_task_ids = task_ids

    def remember_tool_output(self, session_id: str, output: dict) -> None:
        state = self.get(session_id)
        state.last_tool_outputs.append(output)
        if len(state.last_tool_outputs) > 10:
            state.last_tool_outputs = state.last_tool_outputs[-10:]


_store = SessionMemoryStore()


def get_session_store() -> SessionMemoryStore:
    return _store

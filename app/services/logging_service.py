"""Persist every agent run to the execution_logs table."""
from __future__ import annotations

from datetime import datetime

from app.agent import llm_settings
from app.config import get_settings
from app.database.db import db_session
from app.database.models import ExecutionLog


def _current_model_label() -> str:
    """The provider/model actually in use right now — read at log time
    since it can change between runs via the Settings page."""
    settings = get_settings()
    state = llm_settings.get_state()
    if state["provider"] == "gemini":
        return f"gemini:{settings.GEMINI_MODEL}"
    return f"huggingface:{state.get('active_hf_model') or settings.HF_MODEL_1}"


def log_run(
    run_id: str,
    request: str,
    tools_used: list[str],
    arguments: dict,
    result: str | None,
    approval_status: str,
    errors: str | None,
    duration: float,
    started_at: datetime,
) -> None:
    with db_session() as db:
        entry = ExecutionLog(
            run_id=run_id,
            request=request,
            model=_current_model_label(),
            tools_used=tools_used,
            arguments=arguments,
            result=result,
            approval_status=approval_status,
            errors=errors,
            duration=duration,
            started_at=started_at,
            finished_at=datetime.utcnow(),
        )
        db.merge(entry)


def get_logs(limit: int = 100) -> list[dict]:
    with db_session() as db:
        rows = (
            db.query(ExecutionLog)
            .order_by(ExecutionLog.started_at.desc())
            .limit(limit)
            .all()
        )
        return [r.to_dict() for r in rows]


def get_log(run_id: str) -> dict | None:
    with db_session() as db:
        row = db.query(ExecutionLog).filter(ExecutionLog.run_id == run_id).first()
        return row.to_dict() if row else None

"""Execution log retrieval + composite workflow endpoints (weekly review)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services.logging_service import get_log, get_logs
from app.tools.workflow_tools import weekly_review as _weekly_review

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs")
def list_logs(limit: int = 100):
    return {"logs": get_logs(limit=limit)}


@router.get("/logs/{run_id}")
def get_single_log(run_id: str):
    log = get_log(run_id)
    if not log:
        raise HTTPException(status_code=404, detail="Log not found.")
    return {"log": log}


@router.get("/workflows/weekly-review")
def weekly_review():
    result = _weekly_review()
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error", "Weekly review failed."))
    return result

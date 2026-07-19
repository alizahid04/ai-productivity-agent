"""Chat endpoint: drives the LangGraph agent, and the approval resume endpoint."""
from __future__ import annotations

import time
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.agent import graph as agent_graph
from app.agent.llm_service import LLMError
from app.models.schemas import ApprovalDecision, ChatRequest, ChatResponse
from app.services.approval_service import get_approval_service
from app.services.logging_service import log_run

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _persist(run_id: str, request_text: str, state: dict, started_at: datetime) -> None:
    tools_used = [c["tool"] for c in state.get("tool_calls_log", [])]
    duration = time.time() - started_at.timestamp()
    log_run(
        run_id=run_id,
        request=request_text,
        tools_used=tools_used,
        arguments={c["tool"]: c["arguments"] for c in state.get("tool_calls_log", [])},
        result=state.get("final_message"),
        approval_status=(
            "approved" if tools_used and state.get("status") == "completed" else
            "pending" if state.get("status") == "waiting_for_approval" else
            "not_required"
        ),
        errors=state.get("error"),
        duration=round(duration, 3),
        started_at=started_at,
    )


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    run_id = str(uuid.uuid4())
    started_at = datetime.utcnow()

    try:
        state = agent_graph.run_agent(req.session_id, run_id, req.message)
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"Local LLM error: {e}") from e

    if state.get("status") == "waiting_for_approval":
        get_approval_service().store_pending(run_id, state)

    _persist(run_id, req.message, state, started_at)

    return ChatResponse(
        run_id=run_id,
        session_id=req.session_id,
        status=state.get("status", "error"),
        message=state.get("final_message") or "",
        tool_calls=state.get("tool_calls_log", []),
        pending_approval=state.get("pending_approval"),
    )


@router.post("/approval", response_model=ChatResponse)
def resolve_approval(decision: ApprovalDecision) -> ChatResponse:
    approval_service = get_approval_service()
    state_snapshot = approval_service.pop_pending(decision.run_id)
    if state_snapshot is None:
        raise HTTPException(status_code=404, detail="No pending approval found for this run_id.")

    pending = state_snapshot.get("pending_approval") or {}
    if pending.get("approval_id") != decision.approval_id:
        # Put it back — mismatched approval id, don't silently proceed.
        approval_service.store_pending(decision.run_id, state_snapshot)
        raise HTTPException(status_code=400, detail="approval_id does not match the pending request.")

    started_at = datetime.utcnow()
    try:
        result_state = agent_graph.resume_after_approval(
            state_snapshot, decision.decision, decision.edited_arguments
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"Local LLM error: {e}") from e

    if result_state.get("status") == "waiting_for_approval":
        approval_service.store_pending(decision.run_id, result_state)

    _persist(decision.run_id, f"[approval:{decision.decision}]", result_state, started_at)

    return ChatResponse(
        run_id=decision.run_id,
        session_id=decision.session_id,
        status=result_state.get("status", "error"),
        message=result_state.get("final_message") or "",
        tool_calls=result_state.get("tool_calls_log", []),
        pending_approval=result_state.get("pending_approval"),
    )

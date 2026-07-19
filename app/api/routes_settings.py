"""Runtime LLM provider/model selection — view current state and switch
between Hugging Face (with up to three configurable models) and Gemini
without restarting the server.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agent import llm_settings
from app.config import get_settings
from app.models.schemas import LLMSettingsUpdateRequest

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _current_view() -> dict:
    settings = get_settings()
    state = llm_settings.get_state()
    return {
        "provider": state["provider"],
        "active_hf_model": state.get("active_hf_model"),
        "hf_models": llm_settings.get_available_hf_models(),
        "gemini_model": settings.GEMINI_MODEL,
        "huggingface_configured": bool(settings.HF_API_KEY),
        "gemini_configured": bool(settings.GEMINI_API_KEY),
    }


@router.get("/llm")
def get_llm_settings():
    return _current_view()


@router.post("/llm")
def update_llm_settings(req: LLMSettingsUpdateRequest):
    if req.provider is None and req.hf_model is None:
        raise HTTPException(status_code=400, detail="Provide 'provider' and/or 'hf_model' to update.")

    try:
        if req.provider is not None:
            llm_settings.set_provider(req.provider)
        if req.hf_model is not None:
            llm_settings.set_active_hf_model(req.hf_model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return _current_view()

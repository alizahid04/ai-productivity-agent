"""Runtime-selectable LLM provider/model configuration.

Unlike the static settings in app.config (loaded once from .env at
startup), this module tracks which provider — Hugging Face or Gemini — and
which of the up to three configured Hugging Face models is currently
active. It's mutable at runtime via the Settings page / `/api/settings/llm`
without restarting the server, and persists the selection to a small JSON
file (data/llm_settings.json) so it survives restarts too.

Switching to a provider whose API key isn't set in .env is still allowed
here (it's just a preference), but the LLM client will raise a clear,
actionable LLMError the moment it's actually used — see
app.agent.llm_service.get_llm_client().
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from app.config import get_settings

_STATE_PATH = Path("./data/llm_settings.json")
_lock = threading.Lock()
_state: dict | None = None

PROVIDERS = ("huggingface", "gemini")


def _defaults() -> dict:
    settings = get_settings()
    return {
        "provider": settings.LLM_PROVIDER if settings.LLM_PROVIDER in PROVIDERS else "huggingface",
        "active_hf_model": settings.HF_MODEL_1,
    }


def _load() -> dict:
    defaults = _defaults()
    if _STATE_PATH.exists():
        try:
            saved = json.loads(_STATE_PATH.read_text())
            if isinstance(saved, dict):
                return {**defaults, **saved}
        except (json.JSONDecodeError, OSError):
            pass
    return defaults


def _save(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state))


def get_available_hf_models() -> list[str]:
    """The up to three Hugging Face models configured in .env, in order,
    skipping any left blank."""
    settings = get_settings()
    return [m for m in (settings.HF_MODEL_1, settings.HF_MODEL_2, settings.HF_MODEL_3) if m and m.strip()]


def get_state() -> dict:
    global _state
    with _lock:
        if _state is None:
            _state = _load()
        return dict(_state)


def set_provider(provider: str) -> dict:
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Must be one of: {', '.join(PROVIDERS)}")
    global _state
    with _lock:
        if _state is None:
            _state = _load()
        _state["provider"] = provider
        _save(_state)
        return dict(_state)


def set_active_hf_model(model: str) -> dict:
    available = get_available_hf_models()
    if model not in available:
        raise ValueError(
            f"'{model}' is not one of the configured Hugging Face models: {', '.join(available)}"
        )
    global _state
    with _lock:
        if _state is None:
            _state = _load()
        _state["active_hf_model"] = model
        _save(_state)
        return dict(_state)

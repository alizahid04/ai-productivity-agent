"""
Centralized environment-based configuration.

Two cloud LLM providers are supported — Hugging Face Inference API and
Google Gemini — selectable at runtime via the Settings page (see
app/agent/llm_settings.py) without restarting the server, as long as both
providers' API keys are present in .env. Up to three Hugging Face models
can be configured (HF_MODEL_1/2/3); which one is "active" is also
runtime-selectable.

get_settings() only loads plain configuration values here. The actual
"is the currently-selected provider actually usable" check requires
looking at runtime-selectable state too, so it lives in
app.agent.llm_service.verify_llm_backend_ready(), run once at application
startup (see app/main.py) — the app must not run with fake/rule-based
behavior, so it fails fast if neither provider is configured.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"

    # --- Provider selection (default; can be changed at runtime via Settings) ---
    LLM_PROVIDER: str = "huggingface"  # "huggingface" or "gemini"

    # --- Hugging Face Inference API — up to three models to choose between ---
    HF_API_KEY: str = ""
    HF_API_BASE: str = "https://router.huggingface.co/v1"
    HF_MODEL_1: str = "Qwen/Qwen2.5-7B-Instruct"
    HF_MODEL_2: str = "meta-llama/Llama-3.1-8B-Instruct"
    HF_MODEL_3: str = "mistralai/Mistral-7B-Instruct-v0.3"

    # --- Google Gemini API ---
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"

    # --- Database ---
    DATABASE_URL: str = "sqlite:///./data/productivity_agent.db"

    # --- Agent execution limits ---
    MAX_AGENT_STEPS: int = 8
    MAX_RETRIES: int = 2
    TOOL_TIMEOUT_SECONDS: int = 30

    SECRET_KEY: str = "change_me_in_production"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    Path("./data").mkdir(parents=True, exist_ok=True)
    return settings

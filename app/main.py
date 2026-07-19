"""FastAPI application entrypoint.

Fails fast and loudly at import/startup time if the currently selected LLM
provider (Hugging Face or Gemini) doesn't have its API key configured —
see app.agent.llm_service.verify_llm_backend_ready(). This is by design:
the app must not run with fake/rule-based behavior.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings

settings = get_settings()

from app.agent.llm_service import verify_llm_backend_ready  # noqa: E402

# Raises RuntimeError immediately if the active provider's API key is
# missing — intentional; see the module docstring above.
verify_llm_backend_ready(settings)

from app.api import routes_chat, routes_logs, routes_notes, routes_settings, routes_tasks  # noqa: E402
from app.database.db import init_db  # noqa: E402

app = FastAPI(title="AI Productivity Agent", version="1.0.0")

init_db()

app.include_router(routes_chat.router)
app.include_router(routes_tasks.router)
app.include_router(routes_notes.router)
app.include_router(routes_logs.router)
app.include_router(routes_settings.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never expose stack traces to the client.
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again."},
    )


@app.get("/")
def index():
    return FileResponse("app/templates/index.html")


@app.get("/api/health")
def health():
    from app.agent import llm_settings

    state = llm_settings.get_state()
    active_model = (
        settings.GEMINI_MODEL if state["provider"] == "gemini" else state.get("active_hf_model")
    )
    return {
        "status": "ok",
        "provider": state["provider"],
        "model": active_model,
        "env": settings.APP_ENV,
    }

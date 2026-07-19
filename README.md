# Aether — AI Productivity & Task Execution Agent

A production-shaped AI productivity agent built for the Week 3 fellowship
brief: FastAPI + LangGraph + Pydantic + SQLite/SQLAlchemy on the backend,
a vanilla HTML/CSS/JS glassmorphic SaaS dashboard on the frontend, and
**Hugging Face Inference API or Google Gemini** as the LLM backend —
selectable at runtime, no regex/keyword intent detection, no fake AI
behavior.

![status](https://img.shields.io/badge/status-fellowship--project-7c5cfc)

## Features

- Chat-driven agent with a visible pipeline: Intent Analysis → Tool Selection
  → Approval Check → Tool Execution → Batch Dispatch → Validation → Response,
  all rendered live in the UI via the "Agent Pulse" indicator and pipeline
  stepper.
- **Two LLM providers, switchable at runtime** — Hugging Face Inference API
  and Google Gemini — from the Settings page, no restart needed as long as
  both providers' API keys are set in `.env`.
- **Up to three Hugging Face models configured at once** (`HF_MODEL_1/2/3`
  in `.env`); pick which one is active from a dropdown in Settings.
- **One message, many actions**: paste in meeting notes, an email, or any
  long text, and the agent extracts *every* explicit task, note, update,
  completion, deletion, and reminder in one pass, then automatically fires
  the right tool call for each — no per-item back-and-forth, and it never
  asks for clarification when the text already says what to do.
- 12 structured tools (create/list/update/complete/delete task, create
  reminder, save/search notes, extract meeting actions, generate work plan,
  detect overdue tasks, draft follow-up email) — every one Pydantic-validated.
- Human-in-the-loop approval for any state-changing/high-stakes action —
  batched into one combined approval card when several come from the same
  extraction, instead of one interruption per item.
- Persistent SQLite storage for tasks, notes, and a full execution log.
- Session memory that resolves references like *"mark the second one
  complete"* against what was just listed.
- Dashboard with Chart.js analytics, dark/light mode, toasts, skeleton
  loaders, and a full execution log viewer.

## Quick start

```bash
git clone https://github.com/alizahid04/ai-productivity-agent.git
cd productivity-agent
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
```

Edit `.env` and set at least one provider's API key:

- **Hugging Face**: get a token at https://huggingface.co/settings/tokens,
  set `HF_API_KEY`. Up to three models can be listed in `HF_MODEL_1`,
  `HF_MODEL_2`, `HF_MODEL_3` — you'll pick which one is active from the
  Settings page in the app.
- **Gemini**: get a key at https://aistudio.google.com/apikey, set
  `GEMINI_API_KEY`.

Set `LLM_PROVIDER=huggingface` or `LLM_PROVIDER=gemini` to choose the
default on startup — you can switch providers afterward from the Settings
page without restarting, provided both keys are set.

```bash
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000.

> **The app will refuse to start if the selected provider's API key is
> missing.** This is intentional — see `docs/ARCHITECTURE.md`.

## Switching providers and models at runtime

Open the **Settings** page in the app:
- Toggle between **Hugging Face** and **Gemini**.
- When Hugging Face is active, pick which of your configured `HF_MODEL_1/2/3`
  is in use from the dropdown.

Both take effect immediately for the next chat message — no restart needed.
Switching to a provider whose key isn't set in `.env` is allowed (it's just
a preference), but the next chat message will show a clear error telling
you which key to add.

## Running tests

```bash
pytest tests/ -v
```

(Tests cover schema validation, the tool registry, task/note CRUD tools,
and the runtime provider-switching logic against a throwaway SQLite file —
they don't require a real API key, since no LLM-powered tools are exercised
in the test suite.)

## Docker

```bash
docker build -t productivity-agent .
docker run -p 8000:8000 \
  -e HF_API_KEY=hf_xxx \
  -e GEMINI_API_KEY=your_gemini_key \
  -e LLM_PROVIDER=huggingface \
  productivity-agent
```

## Deploying to Railway / Render

1. Push this repo to GitHub.
2. Create a new Web Service from the repo (Render) or a new project from the
   repo (Railway).
3. Set the start command to `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
4. Add the environment variables from `.env.example` (at least one of
   `HF_API_KEY`/`GEMINI_API_KEY` is required; everything else has a
   sane default).
5. Since SQLite is a single file, use a persistent volume/disk mounted at
   `./data` if you need data to survive redeploys.

## Project layout

```
productivity-agent/
├── app/
│   ├── agent/         # LangGraph state machine, LLM service (HF + Gemini), runtime
│   │                  # provider/model selection, session memory
│   ├── api/           # FastAPI routers (chat, tasks, notes, logs, settings)
│   ├── tools/         # Tool implementations + the tool registry
│   ├── database/      # SQLAlchemy engine/session + ORM models
│   ├── services/      # Approval queue + execution logging
│   ├── models/        # Pydantic schemas (API + tool args)
│   ├── prompts/        # Agent system prompt construction
│   ├── static/        # CSS/JS for the dashboard
│   └── templates/     # index.html (single-page app shell)
├── docs/ARCHITECTURE.md
├── tests/
├── requirements.txt
├── Dockerfile
└── .env.example
```

## Configuring models

- **Hugging Face**: `HF_MODEL_1/2/3` in `.env` accept any chat model served
  via Hugging Face's Inference Providers router. Not every model on the Hub
  is deployed via Inference Providers — check
  https://huggingface.co/models?inference_provider=all if a model 404s.
- **Gemini**: `GEMINI_MODEL` accepts any Gemini model your API key has
  access to (default `gemini-2.0-flash`).

The LLM backend lives entirely in `app/agent/llm_service.py` — adding a
third provider later means adding one class there plus a branch in
`get_llm_client()`; nothing else in the agent needs to change.

## Notes on scope

This is a fellowship-project-scale build: it implements every required
piece (agent pipeline, approval workflow, session memory, execution
limits/retries/loop-detection, structured logging, persistent storage,
dark/light dashboard) with clean, typed, documented code — but it is a
single-process app with in-memory session/approval state, which is the
right tradeoff for a demo/fellowship deliverable rather than a
multi-instance production deployment. For real multi-worker production use,
move `SessionMemoryStore` and `ApprovalService` to Redis, and the runtime
LLM provider/model selection (currently a small JSON file) to a shared
store as well.

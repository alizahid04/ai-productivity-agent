# Architecture

## LLM backend: two providers, runtime-switchable

`app/agent/llm_service.py` is the only module in the codebase that talks to
an LLM. It supports two providers — **Hugging Face Inference API** and
**Google Gemini** — both implementing the same small interface
(`complete_chat`, `complete_json_with_retry`, `complete_structured_with_retry`)
via a shared `_BaseLLMClient` that holds all the retry/self-correction
logic; each provider class only implements `_chat()`.

Which provider is active, and (for Hugging Face) which of up to three
configured models (`HF_MODEL_1/2/3` in `.env`) is active, is **not** part of
the static settings loaded once at startup — it's runtime-selectable state
managed by `app/agent/llm_settings.py`, changeable from the Settings page
via `/api/settings/llm` without restarting the server, and persisted to
`data/llm_settings.json` so the selection survives restarts too.
`get_llm_client()` builds a fresh client reflecting the current selection on
every call — cheap, since no network I/O happens until a request is
actually made.

## Pipeline

Every chat message runs through a LangGraph state machine (`app/agent/graph.py`)
that mirrors the required pipeline:

```
Intent Analysis (LLM) -> Tool Selection -> Approval Check -> Tool Execution
-> [Batch Dispatch, only after extract_meeting_actions] -> Validation
-> (loop back to Intent Analysis, or) Response Generation
```

- **Intent Analysis**: the currently-selected model (Hugging Face or
  Gemini) receives the full tool catalogue
  (names, descriptions, JSON schemas) and the conversation so far, and must
  reply with strict JSON: either `{"action": "call_tool", ...}` or
  `{"action": "final_answer", ...}`. There is no keyword/regex intent
  classifier anywhere in the codebase.
- **Tool Selection**: the chosen tool's arguments are validated against its
  Pydantic schema. Invalid arguments are fed back to the model as a system
  error message so it can self-correct (up to `MAX_RETRIES`).
- **Approval Check**: if the tool is in `APPROVAL_REQUIRED_TOOLS`
  (`update_task`, `complete_task`, `delete_task`, `create_reminder`,
  `create_multiple_tasks`, `send_email`), the graph halts in
  `waiting_for_approval` and the pending state is stored server-side keyed by
  `run_id`. Nothing is written to the database until `/api/chat/approval`
  resolves it. Pending approvals are always a **list of actions**
  (`pending_approval["actions"]`) — one item for a normal single-tool call,
  several for a batch extraction — so a user approves/rejects a whole group
  in one decision instead of once per item.
- **Tool Execution**: the Python handler runs against SQLite via SQLAlchemy.
  Handlers never raise to the caller — they return `{"success": false, "error": ...}`.
  Shared by every execution path (`app/agent/graph.py::execute_action`).
- **Batch Dispatch** (new): runs only when the executed tool was
  `extract_meeting_actions`. See "Multi-action batch extraction" below.
- **Validation**: on failure, the error is fed back to the model for a bounded
  number of retries; on success, the structured result is fed back so the
  model can decide whether to call another tool or produce a final answer.
- **Response Generation**: folded into the final `final_answer` action — the
  model composes the user-facing reply itself from the tool results already
  in context, so no chain-of-thought is ever exposed to the client.

## Multi-action batch extraction

The agent is not limited to one action per user message. When the user
provides meeting notes, an email, a transcript, or any long text, the
top-level LLM calls `extract_meeting_actions` **once** with the full text.
That tool itself makes one LLM call (`app/tools/workflow_tools.py`) with a
system prompt that explicitly expects multiple items and forbids inventing
or omitting explicit ones, and validates the model's response against a
strict schema (`BatchExtractionResult` in `app/models/schemas.py`):

```json
{
  "tasks": [{"title": "...", "assignee": "...", "due_date": "...", "priority": "..."}],
  "notes": [{"title": "...", "content": "..."}],
  "updates": [{"task_reference": "...", "status": "..."}],
  "deletions": [{"task_reference": "..."}],
  "completions": [{"task_reference": "..."}],
  "reminders": [{"title": "...", "due_date": "..."}]
}
```

`partition_batch_actions()` then splits every item into:
- **auto_actions** — safe to execute immediately, no approval needed: saving
  notes, and creating a task when the text produced exactly one.
- **approval_actions** — needs human sign-off: creating **multiple** tasks at
  once (grouped into a single synthetic `create_multiple_tasks` action),
  and every update, completion, deletion, and reminder (matching the
  existing per-tool approval rules).

`node_batch_dispatch` (in `app/agent/graph.py`) then actually calls
`create_task` / `save_note` / `update_task` / `complete_task` / `delete_task`
/ `create_reminder` — one real tool call per item — via the shared
`execute_action()` helper, immediately for auto_actions, and (after a single
combined approval) for approval_actions. `create_multiple_tasks` is not a
real registry tool; it's expanded back into individual `create_task` calls
at execution time (`resume_after_approval`).

This means one user message can result in many database writes, while still
using only two LLM calls in the common case (top-level tool selection +
the extraction call itself) — no per-item round-trips.

References inside `updates`/`completions`/`deletions` (e.g. "the FastAPI
backend task") are resolved at execution time by `task_tools._resolve_task`:
first against ordinals in session memory ("the second one"), then by a
title-based lookup against the database. This is entity resolution, not
intent classification — the LLM already decided *what* to do; this only
figures out *which row* a reference points to.

## Session memory & reference resolution

`app/agent/memory.py` keeps an in-process, per-`session_id` record of the
last 30 chat turns, the most recently *listed* task IDs, and recent tool
outputs. When the model calls `update_task`/`complete_task`/`delete_task`
with a `task_reference` like `"the second one"`,
`SessionState.resolve_task_reference` maps that phrase against
`last_listed_task_ids` — this is simple ordinal parsing over
*already-fetched, already-identified* data, not intent classification, so it
doesn't violate the "no regex/keyword matching for intent detection" rule
(intent and tool selection are still 100% LLM-driven).

## Execution limits

- `MAX_AGENT_STEPS` (default 8): hard cap on Intent-Analysis iterations per run.
- `MAX_RETRIES` (default 2): bounded retries after a tool failure, after
  malformed JSON output, and after an unrecognized response shape — each
  tracked separately so one kind of hiccup doesn't eat another's budget.
- `TOOL_TIMEOUT_SECONDS` (default 30): timeout for each LLM API call.
- Duplicate-call detection: identical `(tool_name, arguments)` signatures
  within a single run are rejected as a likely loop (except `list_tasks`,
  which is safe to repeat).

## Robustness against malformed or near-miss model output

Small/free-tier models occasionally wrap JSON in markdown fences, add
commentary, or produce a shape that's valid JSON but doesn't quite match
what's expected. Three layers handle this without ever silently guessing at
intent:
1. `app/agent/json_utils.py::extract_json_object` finds the first balanced
   `{...}` block anywhere in the text, tolerating fences and stray prose.
2. `_normalize_action_shape` in `graph.py` tolerates near-miss key names
   (e.g. `"name"` instead of `"tool_name"`) — pure shape normalization, not
   intent classification.
3. If output is still unusable, the model is shown the exact problem and
   asked to correct itself, bounded by `MAX_RETRIES`, before the run fails.
`app/agent/llm_service.py::complete_structured_with_retry` applies the same
pattern to any LLM-powered tool that must return a specific Pydantic shape
(used by `extract_meeting_actions`).

## Why the app refuses to start without a configured API key

`app/agent/llm_service.py::verify_llm_backend_ready()` is called once at
import of `app.main`, before the FastAPI app is even constructed. It checks
that the currently-selected provider (Hugging Face by default, or Gemini —
see `LLM_PROVIDER` / the persisted runtime selection) has its corresponding
API key set. If not, it raises `RuntimeError` naming exactly which env var
to set. There is intentionally no fallback path — the app must not silently
start with no way to actually reach an LLM, so this is enforced at the
earliest possible point rather than deep inside the agent loop.

This check only looks at whether a key is *present*, not whether it's
actually valid or the account has quota — that class of failure (bad key,
model not found, rate limited, quota exhausted) can only be discovered by
making a real request, so it surfaces as a clear `LLMError` message the
first time a chat message actually needs the LLM, rather than at startup.

## Data model

See `app/database/models.py` for the SQLAlchemy models (`Task`, `Note`,
`ExecutionLog`) and `app/models/schemas.py` for every Pydantic schema —
both the REST API request/response models and the tool-argument schemas the
LLM's output is validated against. `Task` includes an `assignee` field,
populated when meeting notes/emails name a specific person for an item.
Reminders are modeled as `Task` rows tagged `"reminder"` rather than a
separate table.


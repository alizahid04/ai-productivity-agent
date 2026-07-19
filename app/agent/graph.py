"""LangGraph agent implementing:

  Intent Analysis (LLM) -> Tool Selection -> Approval Check -> Tool Execution
  -> Validation -> (loop or) Response Generation

with max-step limits, retries, loop/duplicate-call detection, and a hard
stop whenever a tool requires human approval (the graph run ends in a
'waiting_for_approval' state; a separate resume path re-enters execution
once the user approves/rejects/edits).
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, TypedDict

from pydantic import ValidationError

from app.agent.json_utils import JSONExtractionError, extract_json_object
from app.agent.llm_service import LLMError, get_llm_client
from app.agent.memory import SessionState, get_session_store
from app.config import get_settings
from app.prompts.agent_prompts import build_system_prompt
from app.tools.registry import get_tool_spec

try:
    from langgraph.graph import END, StateGraph

    _HAS_LANGGRAPH = True
except Exception:  # noqa: BLE001 - environment without langgraph installed
    _HAS_LANGGRAPH = False


class AgentState(TypedDict, total=False):
    session_id: str
    run_id: str
    status: str
    llm_messages: list[dict]
    step_count: int
    retry_count: int
    format_retry_count: int
    tool_calls_log: list[dict]
    seen_signatures: set
    pending_tool_name: str | None
    pending_tool_args: dict | None
    last_tool_result: dict
    final_message: str | None
    pending_approval: dict | None
    error: str | None


STATUS_THINKING = "thinking"
STATUS_SELECTING_TOOL = "selecting_tool"
STATUS_WAITING_APPROVAL = "waiting_for_approval"
STATUS_EXECUTING = "executing_tool"
STATUS_VALIDATING = "validating_result"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"
STATUS_RETRYING = "retrying_format"


def _call_llm_for_action(state: AgentState) -> dict:
    client = get_llm_client()
    raw = client.complete_chat(state["llm_messages"], temperature=0.1)
    try:
        return extract_json_object(raw)
    except JSONExtractionError as e:
        # Re-raise with the raw text attached so the caller can show the model
        # exactly what it produced when asking it to correct itself.
        err = JSONExtractionError(str(e))
        err.raw_text = raw  # type: ignore[attr-defined]
        raise err from e


def node_intent_analysis(state: AgentState) -> AgentState:
    settings = get_settings()
    state["status"] = STATUS_THINKING
    state["step_count"] = state.get("step_count", 0) + 1

    if state["step_count"] > settings.MAX_AGENT_STEPS:
        state["status"] = STATUS_ERROR
        state["error"] = "Maximum agent steps exceeded. Please rephrase or simplify your request."
        state["final_message"] = state["error"]
        return state

    try:
        action = _call_llm_for_action(state)
    except LLMError as e:
        state["status"] = STATUS_ERROR
        state["error"] = str(e)
        state["final_message"] = f"I couldn't reach the AI model: {e}"
        return state
    except JSONExtractionError as e:
        # The model didn't return valid JSON. Rather than failing the whole
        # run, give it a bounded number of chances to correct itself — small
        # / free-tier models occasionally add stray commentary or truncate.
        format_retries = state.get("format_retry_count", 0) + 1
        state["format_retry_count"] = format_retries
        if format_retries > settings.MAX_RETRIES:
            state["status"] = STATUS_ERROR
            state["error"] = f"Model kept returning invalid JSON after {format_retries} attempts: {e}"
            state["final_message"] = (
                "I'm having trouble getting a clean response from the AI model right now "
                "(it kept returning malformed output). Please try rephrasing your request, "
                "or try again in a moment."
            )
            return state

        raw_text = getattr(e, "raw_text", "")
        state["llm_messages"].append(
            {
                "role": "user",
                "content": (
                    "[SYSTEM ERROR] Your last response could not be parsed as JSON "
                    f"({e}). Your previous output was:\n{raw_text[:800]}\n\n"
                    "Respond again with ONLY a single valid JSON object — no markdown "
                    "code fences, no commentary before or after it, no explanations. "
                    'It must be exactly {"action": "call_tool", ...} or '
                    '{"action": "final_answer", ...} as described in the system prompt.'
                ),
            }
        )
        state["status"] = STATUS_RETRYING
        return state

    action = _normalize_action_shape(action)

    if action.get("action") == "final_answer":
        state["final_message"] = action.get("message", "")
        state["pending_tool_name"] = None
        state["status"] = STATUS_COMPLETED
        state["llm_messages"].append({"role": "assistant", "content": json.dumps(action)})
        return state

    if action.get("action") == "call_tool":
        state["pending_tool_name"] = action.get("tool_name")
        state["pending_tool_args"] = action.get("arguments", {})
        state["status"] = STATUS_SELECTING_TOOL
        state["llm_messages"].append({"role": "assistant", "content": json.dumps(action)})
        return state

    # The JSON parsed fine but didn't match either expected shape (wrong key
    # names, unsupported "action" value, etc). Treat this the same as a
    # malformed-JSON response: ask the model to correct itself rather than
    # failing the whole run outright.
    format_retries = state.get("format_retry_count", 0) + 1
    state["format_retry_count"] = format_retries
    if format_retries > settings.MAX_RETRIES:
        state["status"] = STATUS_ERROR
        state["error"] = (
            f"Model kept producing a response shape the agent doesn't recognize "
            f"after {format_retries} attempts. Last output: {json.dumps(action)[:400]}"
        )
        state["final_message"] = (
            "I'm having trouble getting a usable response from the AI model right now. "
            "Please try rephrasing your request, or try again in a moment."
        )
        return state

    state["llm_messages"].append(
        {
            "role": "user",
            "content": (
                "[SYSTEM ERROR] Your last response was valid JSON but did not match either "
                f"required shape. You sent: {json.dumps(action)[:500]}\n\n"
                'Respond again with ONLY one of these two exact shapes: '
                '{"action": "call_tool", "tool_name": "<name>", "arguments": {...}, '
                '"thought_summary": "<short sentence>"} or '
                '{"action": "final_answer", "message": "<reply>"}. '
                'The "action" field must be exactly "call_tool" or "final_answer".'
            ),
        }
    )
    state["status"] = STATUS_RETRYING
    return state


def _normalize_action_shape(action: dict) -> dict:
    """Best-effort tolerance for near-miss shapes from smaller/free-tier
    models — e.g. a tool call missing the "action" key, or using "name"
    instead of "tool_name". This is shape normalization, not intent
    classification: the model has already decided what to do: we're just
    being lenient about how it expressed that decision as JSON.
    """
    if action.get("action") in ("call_tool", "final_answer"):
        return action

    normalized = dict(action)

    tool_name = normalized.get("tool_name") or normalized.get("name") or normalized.get("tool")
    if tool_name and (normalized.get("arguments") is not None or normalized.get("args") is not None):
        normalized["action"] = "call_tool"
        normalized["tool_name"] = tool_name
        normalized["arguments"] = normalized.get("arguments") or normalized.get("args") or {}
        return normalized

    message = normalized.get("message") or normalized.get("response") or normalized.get("reply")
    if message and not tool_name:
        normalized["action"] = "final_answer"
        normalized["message"] = message
        return normalized

    return action


def node_tool_selection(state: AgentState) -> AgentState:
    if state["status"] == STATUS_ERROR:
        return state

    tool_name = state.get("pending_tool_name")
    spec = get_tool_spec(tool_name) if tool_name else None

    if spec is None:
        state["llm_messages"].append(
            {"role": "user", "content": f"[SYSTEM ERROR] Unknown tool '{tool_name}'. Choose a valid tool or final_answer."}
        )
        state["status"] = STATUS_THINKING  # allow the loop to self-correct
        return state

    # Duplicate / loop detection.
    signature = f"{tool_name}:{json.dumps(state.get('pending_tool_args', {}), sort_keys=True)}"
    seen = state.setdefault("seen_signatures", set())
    if signature in seen and tool_name != "list_tasks":
        state["status"] = STATUS_ERROR
        state["error"] = "Detected a repeated identical tool call (possible loop). Stopping."
        state["final_message"] = (
            "I noticed I was about to repeat the same action, so I stopped to avoid a loop. "
            "Could you clarify what you'd like next?"
        )
        return state
    seen.add(signature)

    # Validate args against the Pydantic schema.
    try:
        validated = spec.args_schema(**(state.get("pending_tool_args") or {}))
    except ValidationError as e:
        state["llm_messages"].append(
            {
                "role": "user",
                "content": f"[SYSTEM ERROR] Invalid arguments for '{tool_name}': {e}. Try again with corrected arguments.",
            }
        )
        state["status"] = STATUS_THINKING
        state["pending_tool_name"] = None
        return state

    state["pending_tool_args"] = validated.model_dump()
    state["status"] = STATUS_SELECTING_TOOL
    return state


def node_approval_check(state: AgentState) -> AgentState:
    if state["status"] == STATUS_ERROR or not state.get("pending_tool_name"):
        return state
    spec = get_tool_spec(state["pending_tool_name"])
    if spec and spec.requires_approval:
        state["status"] = STATUS_WAITING_APPROVAL
        state["pending_approval"] = {
            "approval_id": str(uuid.uuid4()),
            "actions": [
                {
                    "tool_name": spec.name,
                    "arguments": state.get("pending_tool_args", {}),
                    "expected_effect": f"This will run '{spec.name}' with the arguments shown, "
                    "which changes data in the database.",
                }
            ],
        }
    return state


def execute_action(tool_name: str, arguments: dict, session_id: str) -> dict:
    """Validate and run a single tool call, logging it to session memory.
    Shared by the normal one-tool-per-turn path, the batch dispatcher (which
    calls this once per extracted item), and approval resumption (which
    calls this once per approved item, expanding any grouped
    "create_multiple_tasks" action into individual create_task calls).
    """
    spec = get_tool_spec(tool_name)
    session_store = get_session_store()
    start = time.time()

    if spec is None:
        return {
            "tool": tool_name,
            "arguments": arguments,
            "result": {"success": False, "error": f"Unknown tool '{tool_name}'"},
            "duration_seconds": 0.0,
        }

    try:
        args_obj = spec.args_schema(**arguments)
    except ValidationError as e:
        return {
            "tool": tool_name,
            "arguments": arguments,
            "result": {"success": False, "error": f"Invalid arguments: {e}"},
            "duration_seconds": round(time.time() - start, 3),
        }

    session_memory: SessionState = session_store.get(session_id)
    try:
        kwargs: dict[str, Any] = {}
        if spec.needs_session_memory:
            kwargs["session_memory"] = session_memory
        result = spec.handler(args_obj, **kwargs)
    except Exception as e:  # noqa: BLE001
        result = {"success": False, "error": f"Unexpected tool execution error: {e}"}

    duration = time.time() - start
    log_entry = {
        "tool": tool_name,
        "arguments": arguments,
        "result": result,
        "duration_seconds": round(duration, 3),
    }

    if tool_name == "list_tasks" and result.get("success"):
        session_store.remember_listed_tasks(session_id, [t["id"] for t in result.get("tasks", [])])
    session_store.remember_tool_output(session_id, log_entry)

    return log_entry


def node_tool_execution(state: AgentState) -> AgentState:
    if state["status"] in (STATUS_ERROR, STATUS_WAITING_APPROVAL):
        return state

    state["status"] = STATUS_EXECUTING
    log_entry = execute_action(
        state["pending_tool_name"], state.get("pending_tool_args", {}), state["session_id"]
    )
    state.setdefault("tool_calls_log", []).append(log_entry)
    state["last_tool_result"] = log_entry["result"]
    return state


def node_batch_dispatch(state: AgentState) -> AgentState:
    """Runs right after tool_execution when the executed tool was
    "extract_meeting_actions". Takes its categorized result and fires one
    real tool call per extracted item: notes and a single extracted task
    execute immediately; creating multiple tasks at once, and any
    update/completion/deletion/reminder, are queued into ONE combined
    approval request instead of interrupting the user once per item.
    """
    if state["status"] in (STATUS_ERROR, STATUS_WAITING_APPROVAL):
        return state

    result = state.get("last_tool_result", {})
    if not result.get("success"):
        # Extraction itself failed — hand off to normal validation/retry logic.
        return state

    auto_actions = result.get("auto_actions") or []
    approval_actions = result.get("approval_actions") or []
    counts = result.get("counts", {})

    for action in auto_actions:
        log_entry = execute_action(action["tool_name"], action["arguments"], state["session_id"])
        state.setdefault("tool_calls_log", []).append(log_entry)

    if approval_actions:
        state["pending_approval"] = {
            "approval_id": str(uuid.uuid4()),
            "actions": [
                {
                    "tool_name": a["tool_name"],
                    "arguments": a["arguments"],
                    "expected_effect": a.get("description") or f"This will run '{a['tool_name']}'.",
                }
                for a in approval_actions
            ],
        }
        state["status"] = STATUS_WAITING_APPROVAL
        auto_note = f"I already created/saved {len(auto_actions)} item(s) automatically. " if auto_actions else ""
        total = sum(counts.values()) if counts else (len(auto_actions) + len(approval_actions))
        state["final_message"] = (
            f"I found {total} item(s) in that text. {auto_note}"
            f"{len(approval_actions)} item(s) need your approval before I proceed — see the panel on the right."
        )
        return state

    # Nothing needs approval — feed the outcome back to the top LLM so it can
    # compose a natural final_answer summarizing exactly what happened.
    state["llm_messages"].append(
        {
            "role": "user",
            "content": (
                f"[SYSTEM] Extracted and automatically executed {len(auto_actions)} item(s) from the "
                f"provided text (counts by category: {json.dumps(counts)}). "
                f"Details: {json.dumps(auto_actions)[:3000]}. "
                "Summarize this outcome for the user in a final_answer — mention what was created or "
                "saved. If the counts are all zero, say you didn't find any explicit action items."
            ),
        }
    )
    state["status"] = STATUS_THINKING
    return state


def node_validation(state: AgentState) -> AgentState:
    if state["status"] in (STATUS_ERROR, STATUS_WAITING_APPROVAL):
        return state

    settings = get_settings()
    state["status"] = STATUS_VALIDATING
    result = state.get("last_tool_result", {})

    if not result.get("success", False):
        retry_count = state.get("retry_count", 0) + 1
        state["retry_count"] = retry_count
        if retry_count > settings.MAX_RETRIES:
            state["status"] = STATUS_ERROR
            state["error"] = result.get("error", "Tool execution failed.")
            state["final_message"] = (
                f"I tried but couldn't complete that: {state['error']}"
            )
            return state
        # Feed the error back to the model so it can retry / adjust.
        state["llm_messages"].append(
            {
                "role": "user",
                "content": f"[SYSTEM ERROR] Tool '{state['pending_tool_name']}' failed: "
                f"{result.get('error')}. Retry with corrected arguments, choose a "
                "different tool, or use final_answer to explain the issue to the user.",
            }
        )
        state["status"] = STATUS_THINKING
        return state

    # Success — feed the structured result back so the model can either take
    # another step or produce a final_answer.
    state["llm_messages"].append(
        {
            "role": "user",
            "content": f"[SYSTEM] Tool '{state['pending_tool_name']}' result: "
            f"{json.dumps(result)[:4000]}. If this fully answers the user, respond with "
            "final_answer summarizing it naturally. Otherwise call the next needed tool.",
        }
    )
    state["status"] = STATUS_THINKING
    return state


def _route_after_intent(state: AgentState) -> str:
    if state["status"] in (STATUS_ERROR, STATUS_COMPLETED):
        return "end"
    if state["status"] == STATUS_RETRYING:
        return "intent_analysis"
    return "tool_selection"


def _route_after_selection(state: AgentState) -> str:
    if state["status"] == STATUS_ERROR:
        return "end"
    if state["status"] == STATUS_THINKING:
        # validation/self-correction loop sent us back
        return "intent_analysis"
    return "approval_check"


def _route_after_approval(state: AgentState) -> str:
    if state["status"] in (STATUS_WAITING_APPROVAL, STATUS_ERROR):
        return "end"
    return "tool_execution"


def _route_after_tool_execution(state: AgentState) -> str:
    if state.get("pending_tool_name") == "extract_meeting_actions":
        return "batch_dispatch"
    return "validation"


def _route_after_batch_dispatch(state: AgentState) -> str:
    if state["status"] in (STATUS_ERROR, STATUS_WAITING_APPROVAL):
        return "end"
    if state["status"] == STATUS_THINKING:
        return "intent_analysis"
    # Extraction itself failed (last_tool_result.success is False) — hand off
    # to the normal validation/retry logic rather than duplicating it here.
    return "validation"


def _route_after_validation(state: AgentState) -> str:
    if state["status"] == STATUS_ERROR:
        return "end"
    return "intent_analysis"


def build_graph():
    if not _HAS_LANGGRAPH:
        return None

    graph = StateGraph(AgentState)
    graph.add_node("intent_analysis", node_intent_analysis)
    graph.add_node("tool_selection", node_tool_selection)
    graph.add_node("approval_check", node_approval_check)
    graph.add_node("tool_execution", node_tool_execution)
    graph.add_node("batch_dispatch", node_batch_dispatch)
    graph.add_node("validation", node_validation)

    graph.set_entry_point("intent_analysis")
    graph.add_conditional_edges(
        "intent_analysis",
        _route_after_intent,
        {"tool_selection": "tool_selection", "intent_analysis": "intent_analysis", "end": END},
    )
    graph.add_conditional_edges(
        "tool_selection",
        _route_after_selection,
        {"approval_check": "approval_check", "intent_analysis": "intent_analysis", "end": END},
    )
    graph.add_conditional_edges(
        "approval_check",
        _route_after_approval,
        {"tool_execution": "tool_execution", "end": END},
    )
    graph.add_conditional_edges(
        "tool_execution",
        _route_after_tool_execution,
        {"batch_dispatch": "batch_dispatch", "validation": "validation"},
    )
    graph.add_conditional_edges(
        "batch_dispatch",
        _route_after_batch_dispatch,
        {"intent_analysis": "intent_analysis", "validation": "validation", "end": END},
    )
    graph.add_conditional_edges(
        "validation", _route_after_validation, {"intent_analysis": "intent_analysis", "end": END}
    )
    return graph.compile()


def run_fallback_loop(state: AgentState) -> AgentState:
    """Sequential fallback used only if the langgraph package is unavailable
    in the runtime environment. Implements the exact same node pipeline."""
    settings = get_settings()
    for _ in range(settings.MAX_AGENT_STEPS + settings.MAX_RETRIES + 4):
        state = node_intent_analysis(state)
        if state["status"] in (STATUS_ERROR, STATUS_COMPLETED):
            break
        if state["status"] == STATUS_RETRYING:
            continue
        state = node_tool_selection(state)
        if state["status"] == STATUS_ERROR:
            break
        if state["status"] == STATUS_THINKING:
            continue
        state = node_approval_check(state)
        if state["status"] in (STATUS_WAITING_APPROVAL, STATUS_ERROR):
            break
        state = node_tool_execution(state)
        if state.get("pending_tool_name") == "extract_meeting_actions":
            state = node_batch_dispatch(state)
            if state["status"] in (STATUS_ERROR, STATUS_WAITING_APPROVAL):
                break
            if state["status"] == STATUS_THINKING:
                continue
        state = node_validation(state)
        if state["status"] == STATUS_ERROR:
            break
    return state


_compiled_graph = None


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None and _HAS_LANGGRAPH:
        _compiled_graph = build_graph()
    return _compiled_graph


def init_agent_state(session_id: str, run_id: str, user_message: str) -> AgentState:
    session_store = get_session_store()
    session_memory = session_store.get(session_id)
    session_store.add_message(session_id, "user", user_message)

    llm_messages = [{"role": "system", "content": build_system_prompt()}]
    # Include bounded recent history for multi-turn reference resolution.
    for m in session_memory.messages[-10:]:
        llm_messages.append(m)

    return AgentState(
        session_id=session_id,
        run_id=run_id,
        status=STATUS_THINKING,
        llm_messages=llm_messages,
        step_count=0,
        retry_count=0,
        tool_calls_log=[],
        seen_signatures=set(),
        pending_tool_name=None,
        pending_tool_args=None,
        last_tool_result={},
        final_message=None,
        pending_approval=None,
        error=None,
    )


def run_agent(session_id: str, run_id: str, user_message: str) -> AgentState:
    state = init_agent_state(session_id, run_id, user_message)
    compiled = get_compiled_graph()
    if compiled is not None:
        result = compiled.invoke(state, config={"recursion_limit": 50})
    else:
        result = run_fallback_loop(state)

    if result.get("status") == STATUS_COMPLETED and result.get("final_message"):
        get_session_store().add_message(session_id, "assistant", result["final_message"])
    return result


def resume_after_approval(
    state_snapshot: dict, decision: str, edited_arguments: dict | None
) -> AgentState:
    """Resume a graph run that halted at STATUS_WAITING_APPROVAL. Handles
    both the single-action case (a normal update_task/complete_task/etc. the
    top-level LLM called directly) and the multi-action case (a batch
    extraction that produced several pending actions at once) — both are
    stored the same way: pending_approval["actions"] is always a list.
    """
    state: AgentState = dict(state_snapshot)  # type: ignore[assignment]
    pending = state.get("pending_approval") or {}
    actions: list[dict] = pending.get("actions", [])

    if decision == "reject":
        state["status"] = STATUS_COMPLETED
        n = len(actions)
        state["final_message"] = (
            "Okay, I won't make that change. Anything else?"
            if n <= 1
            else f"Okay, I won't make any of those {n} changes. Anything else?"
        )
        state["pending_tool_name"] = None
        state["pending_approval"] = None
        get_session_store().add_message(state["session_id"], "assistant", state["final_message"])
        return state

    if decision == "edit" and edited_arguments is not None and len(actions) == 1:
        actions = [{**actions[0], "arguments": edited_arguments}]

    state["pending_approval"] = None
    executed_logs: list[dict] = []
    for action in actions:
        tool_name = action.get("tool_name")
        arguments = action.get("arguments", {})
        if tool_name == "create_multiple_tasks":
            # Synthetic grouped action from batch dispatch — expand into one
            # create_task call per task.
            for task_args in arguments.get("tasks", []):
                executed_logs.append(execute_action("create_task", task_args, state["session_id"]))
        else:
            executed_logs.append(execute_action(tool_name, arguments, state["session_id"]))

    state["tool_calls_log"] = state.get("tool_calls_log", []) + executed_logs

    failed = [e for e in executed_logs if not e["result"].get("success")]
    succeeded = [e for e in executed_logs if e["result"].get("success")]
    if failed:
        state["llm_messages"].append(
            {
                "role": "user",
                "content": (
                    f"[SYSTEM] The approved action(s) were executed: {len(succeeded)} succeeded, "
                    f"{len(failed)} failed. Failures: {json.dumps(failed)[:1500]}. Successes: "
                    f"{json.dumps(succeeded)[:1500]}. Summarize this outcome for the user in a "
                    "final_answer, mentioning what succeeded and what failed."
                ),
            }
        )
    else:
        state["llm_messages"].append(
            {
                "role": "user",
                "content": (
                    f"[SYSTEM] All {len(succeeded)} approved action(s) executed successfully: "
                    f"{json.dumps(succeeded)[:3000]}. Summarize this outcome for the user in a "
                    "final_answer."
                ),
            }
        )
    state["status"] = STATUS_THINKING

    settings = get_settings()
    remaining_steps = settings.MAX_AGENT_STEPS - state.get("step_count", 0)
    for _ in range(max(remaining_steps, 0) + settings.MAX_RETRIES + 3):
        state = node_intent_analysis(state)
        if state["status"] in (STATUS_ERROR, STATUS_COMPLETED):
            break
        if state["status"] == STATUS_RETRYING:
            continue
        state = node_tool_selection(state)
        if state["status"] == STATUS_ERROR:
            break
        if state["status"] == STATUS_THINKING:
            continue
        state = node_approval_check(state)
        if state["status"] in (STATUS_WAITING_APPROVAL, STATUS_ERROR):
            break
        state = node_tool_execution(state)
        if state.get("pending_tool_name") == "extract_meeting_actions":
            state = node_batch_dispatch(state)
            if state["status"] in (STATUS_ERROR, STATUS_WAITING_APPROVAL):
                break
            if state["status"] == STATUS_THINKING:
                continue
        state = node_validation(state)
        if state["status"] == STATUS_ERROR:
            break

    if state.get("status") == STATUS_COMPLETED and state.get("final_message"):
        get_session_store().add_message(state["session_id"], "assistant", state["final_message"])
    return state

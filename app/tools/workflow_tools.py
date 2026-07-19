"""Higher-level workflow tools. These are LLM-powered sub-tasks: they issue a
focused, structured-output call to the configured LLM provider rather than
using any regex/keyword heuristics, per the project's core requirement.
"""
from __future__ import annotations

import json
from datetime import datetime

from pydantic import ValidationError

from app.agent.json_utils import JSONExtractionError
from app.agent.llm_service import get_llm_client
from app.database.db import db_session
from app.database.models import Task
from app.models.schemas import (
    BatchExtractionResult,
    DetectOverdueTasksArgs,
    DraftFollowUpEmailArgs,
    ExtractMeetingActionsArgs,
    GenerateWorkPlanArgs,
)
from app.tools.task_tools import detect_overdue_tasks as _detect_overdue_tasks

# ---------------------------------------------------------------------------
# Batch extraction: pull every explicit task/note/update/completion/deletion/
# reminder out of a piece of long text in one LLM call.
# ---------------------------------------------------------------------------
BATCH_EXTRACTION_SYSTEM = """You extract EVERY explicit actionable item from meeting notes,
emails, transcripts, or any long text the user provides. This kind of text is EXPECTED to
contain multiple distinct items — extracting only one when several are present is wrong.

Categories to find:
- tasks: new work items to create. Include an assignee if a specific person is named for it,
  a due_date if a deadline is mentioned (ISO-8601, e.g. "2026-07-24T00:00:00"; null if none),
  and a priority ("low"/"medium"/"high"/"urgent") when the text implies urgency — default "medium".
- notes: standalone information worth remembering that is NOT an action (decisions, facts,
  context, technical choices). Give each a short title if a natural one exists.
- updates: explicit requests to change an EXISTING task's title, description, priority,
  status, or due date. task_reference should be how the text refers to that task (its
  name, a fragment of it, or a person/topic that identifies it) — you do not need to know
  its ID.
- completions: explicit statements that an existing task is done / finished / completed.
- deletions: explicit requests to remove/delete/cancel an existing task.
- reminders: explicit requests to be reminded of something at a later point, distinct from a
  task someone needs to do — e.g. "remind me to follow up next week".

Respond with ONLY valid JSON (no markdown fences, no commentary) matching exactly:
{"tasks": [{"title": str, "description": str|null, "assignee": str|null, "due_date": str|null, "priority": "low"|"medium"|"high"|"urgent"}],
 "notes": [{"title": str|null, "content": str, "category": str}],
 "updates": [{"task_reference": str, "title": str|null, "description": str|null, "priority": str|null, "status": str|null, "due_date": str|null}],
 "deletions": [{"task_reference": str}],
 "completions": [{"task_reference": str}],
 "reminders": [{"title": str, "due_date": str|null, "note": str|null}]}

Every array is optional and should be empty ([]) if that category has no explicit items —
but do NOT invent items that aren't explicitly present, and do NOT omit any that are.
Never ask a clarifying question here: if the text contains explicit actionable information,
extract it. Only leave a category empty when the text genuinely contains nothing for it."""


def extract_meeting_actions(args: ExtractMeetingActionsArgs) -> dict:
    """Extract ALL explicit actions from long text in one LLM call, then
    partition them into what can execute immediately vs. what needs human
    approval (per the existing per-tool approval rules). This result is
    consumed by the agent's batch-dispatch step, which actually calls
    create_task/save_note/update_task/complete_task/delete_task/
    create_reminder for every item — no further LLM round-trips needed.
    """
    try:
        client = get_llm_client()
        batch: BatchExtractionResult = client.complete_structured_with_retry(
            system=BATCH_EXTRACTION_SYSTEM,
            user=f"Text:\n\n{args.text}",
            schema=BatchExtractionResult,
        )
    except (JSONExtractionError, ValidationError) as e:
        return {"success": False, "error": f"Model couldn't produce a valid extraction after retries: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to extract actions: {e}"}

    auto_actions, approval_actions = partition_batch_actions(batch)
    counts = {
        "tasks": len(batch.tasks),
        "notes": len(batch.notes),
        "updates": len(batch.updates),
        "deletions": len(batch.deletions),
        "completions": len(batch.completions),
        "reminders": len(batch.reminders),
    }
    return {
        "success": True,
        "counts": counts,
        "auto_actions": auto_actions,
        "approval_actions": approval_actions,
    }


def _derive_note_title(content: str) -> str:
    content = content.strip()
    if len(content) <= 60:
        return content
    truncated = content[:60].rsplit(" ", 1)[0]
    return f"{truncated}…"


def partition_batch_actions(batch: BatchExtractionResult) -> tuple[list[dict], list[dict]]:
    """Split extracted items into:
      - auto_actions: safe to execute immediately, no approval needed
        (creating notes, and creating a single task).
      - approval_actions: require human sign-off before executing — creating
        MULTIPLE tasks at once, and any update/completion/deletion/reminder,
        matching the project's existing per-tool approval rules.
    Grouped as data here; the agent graph executes them.
    """
    auto_actions: list[dict] = []
    approval_actions: list[dict] = []

    for note in batch.notes:
        auto_actions.append(
            {
                "tool_name": "save_note",
                "arguments": {
                    "title": note.title or _derive_note_title(note.content),
                    "content": note.content,
                    "category": note.category,
                },
            }
        )

    if len(batch.tasks) == 1:
        t = batch.tasks[0]
        auto_actions.append(
            {
                "tool_name": "create_task",
                "arguments": {
                    "title": t.title,
                    "description": t.description,
                    "priority": t.priority.value,
                    "due_date": t.due_date,
                    "assignee": t.assignee,
                    "tags": [],
                },
            }
        )
    elif len(batch.tasks) > 1:
        approval_actions.append(
            {
                "tool_name": "create_multiple_tasks",
                "arguments": {
                    "tasks": [
                        {
                            "title": t.title,
                            "description": t.description,
                            "priority": t.priority.value,
                            "due_date": t.due_date,
                            "assignee": t.assignee,
                            "tags": [],
                        }
                        for t in batch.tasks
                    ]
                },
                "description": f"Create {len(batch.tasks)} new tasks: "
                + ", ".join(t.title for t in batch.tasks),
            }
        )

    for u in batch.updates:
        args: dict = {"task_reference": u.task_reference}
        if u.title is not None:
            args["title"] = u.title
        if u.description is not None:
            args["description"] = u.description
        if u.priority is not None:
            args["priority"] = u.priority.value
        if u.status is not None:
            args["status"] = u.status.value
        if u.due_date is not None:
            args["due_date"] = u.due_date
        approval_actions.append(
            {
                "tool_name": "update_task",
                "arguments": args,
                "description": f"Update task '{u.task_reference}'",
            }
        )

    for c in batch.completions:
        approval_actions.append(
            {
                "tool_name": "complete_task",
                "arguments": {"task_reference": c.task_reference},
                "description": f"Mark '{c.task_reference}' as completed",
            }
        )

    for d in batch.deletions:
        approval_actions.append(
            {
                "tool_name": "delete_task",
                "arguments": {"task_reference": d.task_reference},
                "description": f"Delete task '{d.task_reference}'",
            }
        )

    for r in batch.reminders:
        approval_actions.append(
            {
                "tool_name": "create_reminder",
                "arguments": {"title": r.title, "due_date": r.due_date, "note": r.note},
                "description": f"Create reminder '{r.title}'",
            }
        )

    return auto_actions, approval_actions


WORK_PLAN_SYSTEM = """You are a productivity planning assistant. Given a JSON list of the
user's current tasks and a timeframe, produce a realistic schedule and explain your
reasoning briefly. Respond with ONLY valid JSON (no markdown fences) matching:
{"schedule": [{"task_id": str|null, "title": str, "suggested_slot": str, "reasoning": str}],
 "summary": str}"""

FOLLOWUP_EMAIL_SYSTEM = """You draft short, professional follow-up emails.
Respond with ONLY valid JSON (no markdown fences) matching:
{"subject": str, "body": str}"""


def generate_work_plan(args: GenerateWorkPlanArgs) -> dict:
    try:
        with db_session() as db:
            tasks = (
                db.query(Task)
                .filter(Task.status.notin_(["completed", "cancelled"]))
                .order_by(Task.priority.desc(), Task.due_date.asc())
                .limit(50)
                .all()
            )
            task_payload = [t.to_dict() for t in tasks]

        client = get_llm_client()
        user_prompt = (
            f"Timeframe: {args.timeframe}\n"
            f"Focus: {args.focus or 'none specified'}\n"
            f"Current tasks (JSON): {json.dumps(task_payload)}"
        )
        parsed = client.complete_json_with_retry(system=WORK_PLAN_SYSTEM, user=user_prompt)
        return {"success": True, "plan": parsed}
    except JSONExtractionError as e:
        return {"success": False, "error": f"Model returned invalid JSON after retries: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to generate work plan: {e}"}


def detect_overdue_tasks(args: DetectOverdueTasksArgs) -> dict:
    return _detect_overdue_tasks(args.as_of)


def draft_followup_email(args: DraftFollowUpEmailArgs) -> dict:
    try:
        related_task = None
        if args.related_task_id:
            with db_session() as db:
                t = db.query(Task).filter(Task.id == args.related_task_id).first()
                related_task = t.to_dict() if t else None

        client = get_llm_client()
        user_prompt = (
            f"Recipient: {args.recipient_name}\n"
            f"Context: {args.context}\n"
            f"Tone: {args.tone}\n"
            f"Related task: {json.dumps(related_task) if related_task else 'none'}"
        )
        parsed = client.complete_json_with_retry(system=FOLLOWUP_EMAIL_SYSTEM, user=user_prompt)
        return {"success": True, "draft": parsed}
    except JSONExtractionError as e:
        return {"success": False, "error": f"Model returned invalid JSON after retries: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to draft follow-up email: {e}"}


def weekly_review() -> dict:
    """Composite workflow: weekly completion rate + overdue tasks + recommendations."""
    try:
        with db_session() as db:
            all_tasks = db.query(Task).all()
            total = len(all_tasks)
            completed = len([t for t in all_tasks if t.status == "completed"])
        overdue = _detect_overdue_tasks(None)
        completion_rate = round((completed / total) * 100, 1) if total else 0.0

        client = get_llm_client()
        summary_prompt = (
            f"Total tasks: {total}, completed: {completed}, "
            f"completion_rate: {completion_rate}%, "
            f"overdue_count: {overdue.get('count', 0)}. "
            "Write 2-3 short, encouraging, actionable recommendations."
        )
        parsed = client.complete_json_with_retry(
            system='Respond with ONLY valid JSON: {"recommendations": [str, str, ...]}',
            user=summary_prompt,
        )
        return {
            "success": True,
            "total_tasks": total,
            "completed": completed,
            "completion_rate": completion_rate,
            "overdue": overdue.get("overdue_tasks", []),
            "recommendations": parsed.get("recommendations", []),
            "generated_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to generate weekly review: {e}"}

"""Central tool registry.

This is the single source of truth the LLM's tool-selection is validated
against: names, JSON schemas (derived from Pydantic), the Python callable,
and whether human approval is required before execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.models.schemas import (
    APPROVAL_REQUIRED_TOOLS,
    TOOL_ARG_SCHEMAS,
    CompleteTaskArgs,
    CreateReminderArgs,
    CreateTaskArgs,
    DeleteTaskArgs,
    DetectOverdueTasksArgs,
    DraftFollowUpEmailArgs,
    ExtractMeetingActionsArgs,
    GenerateWorkPlanArgs,
    ListTasksArgs,
    SaveNoteArgs,
    SearchNotesArgs,
    UpdateTaskArgs,
)
from app.tools import note_tools, task_tools, workflow_tools


@dataclass
class ToolSpec:
    name: str
    description: str
    args_schema: type
    handler: Callable[..., dict]
    requires_approval: bool
    needs_session_memory: bool = False


def _create_task_handler(args: CreateTaskArgs, **_kw) -> dict:
    return task_tools.create_task(args)


def _list_tasks_handler(args: ListTasksArgs, **_kw) -> dict:
    return task_tools.list_tasks(args)


def _update_task_handler(args: UpdateTaskArgs, session_memory=None, **_kw) -> dict:
    return task_tools.update_task(args, session_memory=session_memory)


def _complete_task_handler(args: CompleteTaskArgs, session_memory=None, **_kw) -> dict:
    return task_tools.complete_task(args, session_memory=session_memory)


def _delete_task_handler(args: DeleteTaskArgs, session_memory=None, **_kw) -> dict:
    return task_tools.delete_task(args, session_memory=session_memory)


def _create_reminder_handler(args: CreateReminderArgs, **_kw) -> dict:
    return task_tools.create_reminder(args)


def _save_note_handler(args: SaveNoteArgs, **_kw) -> dict:
    return note_tools.save_note(args)


def _search_notes_handler(args: SearchNotesArgs, **_kw) -> dict:
    return note_tools.search_notes(args)


def _extract_meeting_actions_handler(args: ExtractMeetingActionsArgs, **_kw) -> dict:
    return workflow_tools.extract_meeting_actions(args)


def _generate_work_plan_handler(args: GenerateWorkPlanArgs, **_kw) -> dict:
    return workflow_tools.generate_work_plan(args)


def _detect_overdue_tasks_handler(args: DetectOverdueTasksArgs, **_kw) -> dict:
    return workflow_tools.detect_overdue_tasks(args)


def _draft_followup_email_handler(args: DraftFollowUpEmailArgs, **_kw) -> dict:
    return workflow_tools.draft_followup_email(args)


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "create_task": ToolSpec(
        name="create_task",
        description="Create a single new task with title, description, priority, due date, tags.",
        args_schema=CreateTaskArgs,
        handler=_create_task_handler,
        requires_approval=False,
    ),
    "list_tasks": ToolSpec(
        name="list_tasks",
        description="List tasks, optionally filtered by status or priority.",
        args_schema=ListTasksArgs,
        handler=_list_tasks_handler,
        requires_approval=False,
    ),
    "update_task": ToolSpec(
        name="update_task",
        description="Update fields of an existing task (identified by id or a natural reference like 'the second one').",
        args_schema=UpdateTaskArgs,
        handler=_update_task_handler,
        requires_approval=True,
        needs_session_memory=True,
    ),
    "complete_task": ToolSpec(
        name="complete_task",
        description="Mark a task as completed (identified by id or a natural reference).",
        args_schema=CompleteTaskArgs,
        handler=_complete_task_handler,
        requires_approval=True,
        needs_session_memory=True,
    ),
    "delete_task": ToolSpec(
        name="delete_task",
        description="Permanently delete a task (identified by id or a natural reference).",
        args_schema=DeleteTaskArgs,
        handler=_delete_task_handler,
        requires_approval=True,
        needs_session_memory=True,
    ),
    "create_reminder": ToolSpec(
        name="create_reminder",
        description="Create a reminder for a later point in time (distinct from a task someone must do).",
        args_schema=CreateReminderArgs,
        handler=_create_reminder_handler,
        requires_approval=True,
    ),
    "save_note": ToolSpec(
        name="save_note",
        description="Save a new note with title, content, category, tags.",
        args_schema=SaveNoteArgs,
        handler=_save_note_handler,
        requires_approval=False,
    ),
    "search_notes": ToolSpec(
        name="search_notes",
        description="Search notes by keyword across title and content.",
        args_schema=SearchNotesArgs,
        handler=_search_notes_handler,
        requires_approval=False,
    ),
    "extract_meeting_actions": ToolSpec(
        name="extract_meeting_actions",
        description=(
            "Extract EVERY explicit task, note, update, completion, deletion, and reminder from "
            "meeting notes, an email, a transcript, or any long text (LLM-powered). Use this "
            "whenever the user provides a block of text that describes multiple things to do, "
            "rather than trying to handle each item with a separate tool call yourself — this "
            "tool finds all of them at once and the system executes each automatically."
        ),
        args_schema=ExtractMeetingActionsArgs,
        handler=_extract_meeting_actions_handler,
        requires_approval=False,
    ),
    "generate_work_plan": ToolSpec(
        name="generate_work_plan",
        description="Generate a prioritized schedule/work plan for today or this week (LLM-powered).",
        args_schema=GenerateWorkPlanArgs,
        handler=_generate_work_plan_handler,
        requires_approval=False,
    ),
    "detect_overdue_tasks": ToolSpec(
        name="detect_overdue_tasks",
        description="Find tasks past their due date that are not completed/cancelled.",
        args_schema=DetectOverdueTasksArgs,
        handler=_detect_overdue_tasks_handler,
        requires_approval=False,
    ),
    "draft_followup_email": ToolSpec(
        name="draft_followup_email",
        description="Draft a follow-up email for a recipient and context (LLM-powered). Does NOT send it.",
        args_schema=DraftFollowUpEmailArgs,
        handler=_draft_followup_email_handler,
        requires_approval=False,
    ),
}


def get_tool_spec(name: str) -> ToolSpec | None:
    return TOOL_REGISTRY.get(name)


def tools_for_llm_prompt() -> list[dict[str, Any]]:
    """Compact tool catalogue injected into the LLM system prompt."""
    out = []
    for spec in TOOL_REGISTRY.values():
        out.append(
            {
                "name": spec.name,
                "description": spec.description,
                "args_schema": spec.args_schema.model_json_schema(),
                "requires_approval": spec.requires_approval,
            }
        )
    return out


assert set(TOOL_ARG_SCHEMAS.keys()) <= set(TOOL_REGISTRY.keys())
assert APPROVAL_REQUIRED_TOOLS >= {
    n for n, s in TOOL_REGISTRY.items() if s.requires_approval
}

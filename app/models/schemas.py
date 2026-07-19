"""Pydantic schemas: API request/response models AND tool-argument schemas.

Every tool the agent can call has a strict Pydantic input schema. The LLM's
structured JSON output is validated against these before any execution
happens — invalid arguments are rejected and returned to the agent loop as
an error, never silently coerced.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------
class Priority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class TaskStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


# ---------------------------------------------------------------------------
# API: Chat
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    run_id: str
    session_id: str
    status: str
    message: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    pending_approval: dict[str, Any] | None = None


class ApprovalDecision(BaseModel):
    session_id: str
    run_id: str
    approval_id: str
    decision: Literal["approve", "reject", "edit"]
    edited_arguments: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# API: LLM provider/model settings (runtime-selectable)
# ---------------------------------------------------------------------------
class LLMSettingsUpdateRequest(BaseModel):
    provider: Literal["huggingface", "gemini"] | None = None
    hf_model: str | None = None


# ---------------------------------------------------------------------------
# API: Tasks / Notes CRUD (used by REST endpoints, not just the agent)
# ---------------------------------------------------------------------------
class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    priority: Priority = Priority.medium
    due_date: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    assignee: str | None = None
    notes: str | None = None


class TaskUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: Priority | None = None
    status: TaskStatus | None = None
    due_date: datetime | None = None
    tags: list[str] | None = None
    assignee: str | None = None
    notes: str | None = None


class NoteCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    category: str = "general"
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tool argument schemas (validated LLM outputs)
# ---------------------------------------------------------------------------
def _validate_iso_date(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"date must be ISO-8601: {e}") from e
    return v


class CreateTaskArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    priority: Priority = Priority.medium
    due_date: str | None = Field(None, description="ISO-8601 date/time, or null")
    tags: list[str] = Field(default_factory=list)
    assignee: str | None = Field(None, description="Person this task is assigned to, if mentioned")

    @field_validator("due_date")
    @classmethod
    def _validate_due_date(cls, v: str | None) -> str | None:
        return _validate_iso_date(v)


class ListTasksArgs(BaseModel):
    status: TaskStatus | None = None
    priority: Priority | None = None
    limit: int = Field(default=50, ge=1, le=500)


class UpdateTaskArgs(BaseModel):
    task_id: str | None = Field(None, description="Task UUID if known")
    task_reference: str | None = Field(
        None,
        description="Natural reference: an ordinal ('the second one'), or descriptive text "
        "(a task title or fragment of one) resolved against recent context or the database.",
    )
    title: str | None = None
    description: str | None = None
    priority: Priority | None = None
    status: TaskStatus | None = None
    due_date: str | None = None
    tags: list[str] | None = None
    assignee: str | None = None


class CompleteTaskArgs(BaseModel):
    task_id: str | None = None
    task_reference: str | None = None


class DeleteTaskArgs(BaseModel):
    task_id: str | None = None
    task_reference: str | None = None


class CreateReminderArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    due_date: str | None = None
    note: str | None = None

    @field_validator("due_date")
    @classmethod
    def _validate_due_date(cls, v: str | None) -> str | None:
        return _validate_iso_date(v)


class SaveNoteArgs(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    category: str = "general"
    tags: list[str] = Field(default_factory=list)


class SearchNotesArgs(BaseModel):
    query: str = Field(..., min_length=1)
    limit: int = Field(default=10, ge=1, le=100)


class ExtractMeetingActionsArgs(BaseModel):
    text: str = Field(
        ...,
        min_length=1,
        description="Raw meeting notes, an email, a transcript, or any long text that may "
        "contain multiple explicit tasks, notes, updates, completions, deletions, or reminders.",
    )


class GenerateWorkPlanArgs(BaseModel):
    timeframe: Literal["today", "this_week"] = "today"
    focus: str | None = Field(None, description="Optional focus area or goal")


class DetectOverdueTasksArgs(BaseModel):
    as_of: str | None = Field(None, description="ISO date to compare against; defaults to now")


class DraftFollowUpEmailArgs(BaseModel):
    recipient_name: str = Field(..., min_length=1)
    context: str = Field(..., min_length=1, description="What the follow-up is about")
    tone: Literal["formal", "friendly", "concise"] = "friendly"
    related_task_id: str | None = None


# ---------------------------------------------------------------------------
# Batch extraction: one structured object covering every explicit action in
# a piece of long text (meeting notes, emails, transcripts, paragraphs).
# The LLM produces this in one shot; the backend then executes one tool call
# per item.
# ---------------------------------------------------------------------------
class BatchTaskItem(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    assignee: str | None = None
    due_date: str | None = None
    priority: Priority = Priority.medium

    @field_validator("due_date")
    @classmethod
    def _validate_due_date(cls, v: str | None) -> str | None:
        return _validate_iso_date(v)


class BatchNoteItem(BaseModel):
    title: str | None = Field(None, description="Short title; if omitted, one is derived from the content.")
    content: str = Field(..., min_length=1)
    category: str = "general"


class BatchUpdateItem(BaseModel):
    task_reference: str = Field(..., description="Which task this refers to (title, fragment, or ordinal).")
    title: str | None = None
    description: str | None = None
    priority: Priority | None = None
    status: TaskStatus | None = None
    due_date: str | None = None


class BatchCompletionItem(BaseModel):
    task_reference: str = Field(..., description="Which task was completed (title, fragment, or ordinal).")


class BatchDeletionItem(BaseModel):
    task_reference: str = Field(..., description="Which task to delete (title, fragment, or ordinal).")


class BatchReminderItem(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    due_date: str | None = None
    note: str | None = None


class BatchExtractionResult(BaseModel):
    tasks: list[BatchTaskItem] = Field(default_factory=list)
    notes: list[BatchNoteItem] = Field(default_factory=list)
    updates: list[BatchUpdateItem] = Field(default_factory=list)
    deletions: list[BatchDeletionItem] = Field(default_factory=list)
    completions: list[BatchCompletionItem] = Field(default_factory=list)
    reminders: list[BatchReminderItem] = Field(default_factory=list)


TOOL_ARG_SCHEMAS: dict[str, type[BaseModel]] = {
    "create_task": CreateTaskArgs,
    "list_tasks": ListTasksArgs,
    "update_task": UpdateTaskArgs,
    "complete_task": CompleteTaskArgs,
    "delete_task": DeleteTaskArgs,
    "create_reminder": CreateReminderArgs,
    "save_note": SaveNoteArgs,
    "search_notes": SearchNotesArgs,
    "extract_meeting_actions": ExtractMeetingActionsArgs,
    "generate_work_plan": GenerateWorkPlanArgs,
    "detect_overdue_tasks": DetectOverdueTasksArgs,
    "draft_followup_email": DraftFollowUpEmailArgs,
}

# Tools that require explicit human approval before executing. Note:
# "create_multiple_tasks" isn't a standalone registry tool — it's a synthetic
# label the batch orchestrator uses when a single batch would create more
# than one task, so that gets one grouped approval instead of the single
# create_task tool (which doesn't require approval on its own).
APPROVAL_REQUIRED_TOOLS = {
    "update_task",
    "complete_task",
    "delete_task",
    "create_reminder",
    "create_multiple_tasks",
    "send_email",
}

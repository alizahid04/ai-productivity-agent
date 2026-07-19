"""Tools for creating, listing, updating, and completing tasks.

Every tool function:
  * accepts a validated Pydantic args object (never raw dicts),
  * talks to the DB via a scoped session,
  * returns a plain-dict structured result,
  * never raises uncaught exceptions to the caller — errors are returned
    as {"success": False, "error": "..."} so the agent loop can react.
"""
from __future__ import annotations

from datetime import datetime

from app.database.db import db_session
from app.database.models import Task
from app.models.schemas import (
    CompleteTaskArgs,
    CreateReminderArgs,
    CreateTaskArgs,
    DeleteTaskArgs,
    ListTasksArgs,
    UpdateTaskArgs,
)


def _parse_due_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def create_task(args: CreateTaskArgs, source: str = "chat") -> dict:
    try:
        with db_session() as db:
            task = Task(
                title=args.title,
                description=args.description,
                priority=args.priority.value,
                due_date=_parse_due_date(args.due_date),
                tags=args.tags,
                assignee=args.assignee,
                source=source,
            )
            db.add(task)
            db.flush()
            result = task.to_dict()
        return {"success": True, "task": result}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to create task: {e}"}


def list_tasks(args: ListTasksArgs) -> dict:
    try:
        with db_session() as db:
            query = db.query(Task)
            if args.status:
                query = query.filter(Task.status == args.status.value)
            if args.priority:
                query = query.filter(Task.priority == args.priority.value)
            tasks = (
                query.order_by(Task.created_at.desc()).limit(args.limit).all()
            )
            result = [t.to_dict() for t in tasks]
        return {"success": True, "tasks": result, "count": len(result)}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to list tasks: {e}"}


def _resolve_task(db, task_id: str | None, task_reference: str | None, session_memory=None) -> Task | None:
    """Resolve a task by direct id, an ordinal reference ('the second one')
    against what was just listed in this session, or — for references that
    came out of batch extraction and were never explicitly listed, like
    "the FastAPI backend task" — a fallback lookup against task titles
    already in the database. This is entity resolution against data the
    system already holds, not intent classification: which tool to call was
    already decided by the LLM; this only figures out *which row* a
    reference points to.
    """
    if task_id:
        return db.query(Task).filter(Task.id == task_id).first()
    if not task_reference:
        return None

    if session_memory is not None:
        resolved_id = session_memory.resolve_task_reference(task_reference)
        if resolved_id:
            task = db.query(Task).filter(Task.id == resolved_id).first()
            if task:
                return task

    # Fallback: match the reference text against existing task titles.
    ref = task_reference.strip().lower()
    if not ref:
        return None
    candidates = db.query(Task).all()
    for t in candidates:
        if t.title.strip().lower() == ref:
            return t
    for t in candidates:
        title = t.title.strip().lower()
        if ref in title or title in ref:
            return t
    return None


def update_task(args: UpdateTaskArgs, session_memory=None) -> dict:
    try:
        with db_session() as db:
            task = _resolve_task(db, args.task_id, args.task_reference, session_memory)
            if not task:
                return {"success": False, "error": f"Could not resolve task '{args.task_reference or args.task_id}' to update."}

            if args.title is not None:
                task.title = args.title
            if args.description is not None:
                task.description = args.description
            if args.priority is not None:
                task.priority = args.priority.value
            if args.status is not None:
                task.status = args.status.value
            if args.due_date is not None:
                task.due_date = _parse_due_date(args.due_date)
            if args.tags is not None:
                task.tags = args.tags
            if args.assignee is not None:
                task.assignee = args.assignee
            task.updated_at = datetime.utcnow()
            db.flush()
            result = task.to_dict()
        return {"success": True, "task": result}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to update task: {e}"}


def complete_task(args: CompleteTaskArgs, session_memory=None) -> dict:
    try:
        with db_session() as db:
            task = _resolve_task(db, args.task_id, args.task_reference, session_memory)
            if not task:
                return {"success": False, "error": f"Could not resolve task '{args.task_reference or args.task_id}' to complete."}
            task.status = "completed"
            task.updated_at = datetime.utcnow()
            db.flush()
            result = task.to_dict()
        return {"success": True, "task": result}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to complete task: {e}"}


def delete_task(args: DeleteTaskArgs, session_memory=None) -> dict:
    try:
        with db_session() as db:
            task = _resolve_task(db, args.task_id, args.task_reference, session_memory)
            if not task:
                return {"success": False, "error": f"Could not resolve task '{args.task_reference or args.task_id}' to delete."}
            deleted_summary = task.to_dict()
            db.delete(task)
        return {"success": True, "deleted_task": deleted_summary}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to delete task: {e}"}


def create_reminder(args: CreateReminderArgs) -> dict:
    """Reminders are modeled as tasks tagged 'reminder' — same underlying
    entity, distinguished for display and for the human-approval workflow."""
    try:
        with db_session() as db:
            task = Task(
                title=args.title,
                description=args.note,
                priority="medium",
                due_date=_parse_due_date(args.due_date),
                tags=["reminder"],
                source="chat",
            )
            db.add(task)
            db.flush()
            result = task.to_dict()
        return {"success": True, "reminder": result}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to create reminder: {e}"}


def detect_overdue_tasks(as_of: str | None = None) -> dict:
    try:
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else datetime.utcnow()
        with db_session() as db:
            tasks = (
                db.query(Task)
                .filter(Task.due_date.isnot(None))
                .filter(Task.due_date < cutoff)
                .filter(Task.status.notin_(["completed", "cancelled"]))
                .all()
            )
            result = [t.to_dict() for t in tasks]
        return {"success": True, "overdue_tasks": result, "count": len(result)}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to detect overdue tasks: {e}"}

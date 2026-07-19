"""Direct REST CRUD for tasks — used by the Tasks page for non-conversational
actions (e.g. drag-drop status change, manual creation via a form)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.database.db import db_session
from app.database.models import Task
from app.models.schemas import TaskCreateRequest, TaskUpdateRequest

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("")
def list_tasks(status: str | None = None, priority: str | None = None):
    with db_session() as db:
        query = db.query(Task)
        if status:
            query = query.filter(Task.status == status)
        if priority:
            query = query.filter(Task.priority == priority)
        tasks = query.order_by(Task.created_at.desc()).all()
        return {"tasks": [t.to_dict() for t in tasks]}


@router.post("")
def create_task(req: TaskCreateRequest):
    with db_session() as db:
        task = Task(
            title=req.title,
            description=req.description,
            priority=req.priority.value,
            due_date=req.due_date,
            tags=req.tags,
            assignee=req.assignee,
            notes=req.notes,
            source="manual",
        )
        db.add(task)
        db.flush()
        return {"task": task.to_dict()}


@router.patch("/{task_id}")
def update_task(task_id: str, req: TaskUpdateRequest):
    with db_session() as db:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found.")
        data = req.model_dump(exclude_unset=True)
        for field, value in data.items():
            if field == "priority" and value is not None:
                value = value.value if hasattr(value, "value") else value
            if field == "status" and value is not None:
                value = value.value if hasattr(value, "value") else value
            setattr(task, field, value)
        task.updated_at = datetime.utcnow()
        db.flush()
        return {"task": task.to_dict()}


@router.delete("/{task_id}")
def delete_task(task_id: str):
    with db_session() as db:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found.")
        db.delete(task)
        return {"deleted": True, "task_id": task_id}


@router.get("/analytics/summary")
def task_analytics():
    with db_session() as db:
        tasks = db.query(Task).all()
        total = len(tasks)
        by_status: dict[str, int] = {}
        by_priority: dict[str, int] = {}
        for t in tasks:
            by_status[t.status] = by_status.get(t.status, 0) + 1
            by_priority[t.priority] = by_priority.get(t.priority, 0) + 1
        completed = by_status.get("completed", 0)
        return {
            "total": total,
            "by_status": by_status,
            "by_priority": by_priority,
            "completion_rate": round((completed / total) * 100, 1) if total else 0.0,
        }

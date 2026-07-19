"""Direct REST CRUD for notes."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.database.db import db_session
from app.database.models import Note
from app.models.schemas import NoteCreateRequest

router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.get("")
def list_notes(q: str | None = None):
    with db_session() as db:
        query = db.query(Note)
        if q:
            like = f"%{q}%"
            query = query.filter((Note.title.ilike(like)) | (Note.content.ilike(like)))
        notes = query.order_by(Note.updated_at.desc()).all()
        return {"notes": [n.to_dict() for n in notes]}


@router.post("")
def create_note(req: NoteCreateRequest):
    with db_session() as db:
        note = Note(title=req.title, content=req.content, category=req.category, tags=req.tags)
        db.add(note)
        db.flush()
        return {"note": note.to_dict()}


@router.delete("/{note_id}")
def delete_note(note_id: str):
    with db_session() as db:
        note = db.query(Note).filter(Note.id == note_id).first()
        if not note:
            raise HTTPException(status_code=404, detail="Note not found.")
        db.delete(note)
        return {"deleted": True, "note_id": note_id}

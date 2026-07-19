"""Tools for saving and searching notes."""
from __future__ import annotations

from app.database.db import db_session
from app.database.models import Note
from app.models.schemas import SaveNoteArgs, SearchNotesArgs


def save_note(args: SaveNoteArgs) -> dict:
    try:
        with db_session() as db:
            note = Note(
                title=args.title,
                content=args.content,
                category=args.category,
                tags=args.tags,
            )
            db.add(note)
            db.flush()
            result = note.to_dict()
        return {"success": True, "note": result}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to save note: {e}"}


def search_notes(args: SearchNotesArgs) -> dict:
    try:
        like = f"%{args.query}%"
        with db_session() as db:
            notes = (
                db.query(Note)
                .filter((Note.title.ilike(like)) | (Note.content.ilike(like)))
                .order_by(Note.updated_at.desc())
                .limit(args.limit)
                .all()
            )
            result = [n.to_dict() for n in notes]
        return {"success": True, "notes": result, "count": len(result)}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": f"Failed to search notes: {e}"}

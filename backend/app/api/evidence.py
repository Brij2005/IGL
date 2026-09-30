"""Access-controlled retrieval for event-linked evidence files."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

try:
    from app.auth import require_permission
    from app.database import get_db
    from app.models import Event, EventEvidence, User
    from app.services.evidence_engine import evidence_engine
except ImportError:
    from backend.app.auth import require_permission
    from backend.app.database import get_db
    from backend.app.models import Event, EventEvidence, User
    from backend.app.services.evidence_engine import evidence_engine


router = APIRouter()


@router.get("/events/{event_id}/evidence")
def list_event_evidence(
    event_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("events:view")),
):
    if db.query(Event.id).filter(Event.id == event_id).first() is None:
        raise HTTPException(status_code=404, detail="Event not found")
    items = db.query(EventEvidence).filter(EventEvidence.event_id == event_id).order_by(EventEvidence.created_at.desc()).all()
    return [{
        "id": item.id,
        "event_id": item.event_id,
        "evidence_type": item.evidence_type,
        "file_hash": item.file_hash,
        "metadata": item.metadata_json,
        "created_at": item.created_at,
        "status": "AVAILABLE" if Path(item.file_path).is_file() else "EVIDENCE_NOT_AVAILABLE",
    } for item in items]


@router.get("/events/{event_id}/evidence/{evidence_id}/content")
def get_evidence_content(
    event_id: str,
    evidence_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("events:view")),
):
    item = db.query(EventEvidence).filter(
        EventEvidence.id == evidence_id,
        EventEvidence.event_id == event_id,
    ).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Evidence not found")
    try:
        path = evidence_engine.resolve_path(item)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="EVIDENCE_NOT_AVAILABLE") from exc
    return FileResponse(path, media_type="image/jpeg", filename=f"evidence-{item.id}.jpg")
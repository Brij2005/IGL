"""Event listing and validated workflow transitions.

Access is anonymous only when the development setting explicitly permits it.
Every state change is written to the audit log with a NULL actor.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

try:
    from app.access_control import log_audit_event, require_permission, require_role
    from app.database import get_db
    from app.models import Event, User
    from app.schemas_events import EventOut, EventWorkflowTransitionRequest
    from app.services.workflow_engine import InvalidWorkflowTransition, transition_event
except ImportError:
    from backend.app.access_control import log_audit_event, require_permission, require_role
    from backend.app.database import get_db
    from backend.app.models import Event, User
    from backend.app.schemas_events import EventOut, EventWorkflowTransitionRequest
    from backend.app.services.workflow_engine import InvalidWorkflowTransition, transition_event


router = APIRouter()


@router.get("", response_model=list[EventOut])
def list_events(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    return db.query(Event).order_by(Event.started_at.desc()).offset(offset).limit(limit).all()


@router.post("/{event_id}/transitions", response_model=EventOut)
def transition_event_state(
    event_id: str,
    transition: EventWorkflowTransitionRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER")),
):
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    previous_state = event.workflow_state
    try:
        transition_event(db, event, transition.new_state, user_id=actor.id if actor else None, reason=transition.reason)
    except InvalidWorkflowTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="EVENT_WORKFLOW_TRANSITIONED",
        resource_type="EVENT",
        resource_id=event.id,
        details_json={"previous_state": previous_state, "new_state": event.workflow_state},
        ip_address=request.client.host if request.client else None,
    )
    return event

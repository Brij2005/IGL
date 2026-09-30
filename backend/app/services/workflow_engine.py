"""Validated event workflow transitions with durable audit history."""
from __future__ import annotations

from sqlalchemy.orm import Session

try:
    from app.models import Event, EventStateTransition
except ImportError:
    from backend.app.models import Event, EventStateTransition


ALLOWED_TRANSITIONS = {
    "NEW": {"UNACKNOWLEDGED"},
    "UNACKNOWLEDGED": {"ACKNOWLEDGED"},
    "ACKNOWLEDGED": {"ASSIGNED"},
    "ASSIGNED": {"UNDER_INVESTIGATION"},
    "UNDER_INVESTIGATION": {"ACTION_REQUIRED", "RESOLVED"},
    "ACTION_REQUIRED": {"RESOLVED"},
    "RESOLVED": {"CLOSED"},
    "CLOSED": set(),
}


class InvalidWorkflowTransition(ValueError):
    pass


def transition_event(
    db: Session,
    event: Event,
    new_state: str,
    *,
    user_id: str | None,
    reason: str,
) -> Event:
    if not reason or not reason.strip():
        raise InvalidWorkflowTransition("A transition reason is required")
    previous_state = event.workflow_state
    if new_state not in ALLOWED_TRANSITIONS.get(previous_state, set()):
        raise InvalidWorkflowTransition(f"Transition {previous_state} -> {new_state} is not allowed")
    transition = EventStateTransition(
        event_id=event.id,
        user_id=user_id,
        previous_state=previous_state,
        new_state=new_state,
        reason=reason.strip(),
    )
    event.workflow_state = new_state
    db.add(transition)
    db.commit()
    db.refresh(event)
    return event

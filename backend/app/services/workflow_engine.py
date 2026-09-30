"""Validated event workflow transitions with durable audit history.

Event state is only ever changed here, and only to a state the transition map
allows. Acknowledgement and assignment are the two operations that create their
own domain records, and they advance the event workflow in the same transaction
so the acknowledgement log and the event state can never disagree.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

try:
    from app.models import (
        Acknowledgement,
        Assignment,
        Event,
        EventStateTransition,
        User,
    )
except ImportError:
    from backend.app.models import (
        Acknowledgement,
        Assignment,
        Event,
        EventStateTransition,
        User,
    )


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

# The states from which each side-effecting operation may start.
ACKNOWLEDGABLE_STATES = {"NEW", "UNACKNOWLEDGED"}
ASSIGNABLE_STATES = {"ACKNOWLEDGED"}


class InvalidWorkflowTransition(ValueError):
    pass


class WorkflowPreconditionError(ValueError):
    """Raised when an operation's starting state does not permit it."""


def allowed_transitions(current_state: str) -> list[str]:
    """Return the states reachable from ``current_state``."""
    return sorted(ALLOWED_TRANSITIONS.get(current_state, set()))


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


def acknowledge_event(
    db: Session,
    event: Event,
    *,
    user: User,
    notes: str | None = None,
) -> Acknowledgement:
    """Record an operator acknowledgement and advance the event in one commit.

    The event must be awaiting acknowledgement. The transition is written in the
    same transaction as the acknowledgement record, so a failure cannot leave a
    logged acknowledgement against an unacknowledged event.
    """
    if event.workflow_state not in ACKNOWLEDGABLE_STATES:
        raise WorkflowPreconditionError(
            f"An event in state '{event.workflow_state}' cannot be acknowledged; "
            f"expected one of {sorted(ACKNOWLEDGABLE_STATES)}"
        )
    previous_state = event.workflow_state
    acknowledgement = Acknowledgement(
        event_id=event.id,
        user_id=user.id,
        notes=notes,
    )
    db.add(acknowledgement)
    db.add(EventStateTransition(
        event_id=event.id,
        user_id=user.id,
        previous_state=previous_state,
        new_state="ACKNOWLEDGED",
        reason=notes or "Event acknowledged by operator",
    ))
    event.workflow_state = "ACKNOWLEDGED"
    db.commit()
    db.refresh(acknowledgement)
    return acknowledgement


def assign_event(
    db: Session,
    event: Event,
    *,
    assignee: User,
    assigner: User | None,
    due_at=None,
    notes: str | None = None,
) -> Assignment:
    """Assign an acknowledged event to an active user and advance the workflow."""
    if event.workflow_state not in ASSIGNABLE_STATES:
        raise WorkflowPreconditionError(
            f"An event in state '{event.workflow_state}' cannot be assigned; "
            f"expected one of {sorted(ASSIGNABLE_STATES)}"
        )
    if not assignee.is_active:
        raise WorkflowPreconditionError("An event cannot be assigned to an inactive user")
    assignment = Assignment(
        event_id=event.id,
        assigned_to_user_id=assignee.id,
        assigned_by_user_id=assigner.id if assigner else None,
        due_at=due_at,
        notes=notes,
    )
    db.add(assignment)
    db.add(EventStateTransition(
        event_id=event.id,
        user_id=assigner.id if assigner else None,
        previous_state=event.workflow_state,
        new_state="ASSIGNED",
        reason=notes or "Event assigned to operator",
    ))
    event.workflow_state = "ASSIGNED"
    db.commit()
    db.refresh(assignment)
    return assignment

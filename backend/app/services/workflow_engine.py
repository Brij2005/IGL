"""Validated event workflow transitions with durable audit history.

Event state is only ever changed here, and only to a state the transition map
allows. Acknowledgement and assignment are the two operations that create their
own domain records, and they advance the event workflow in the same transaction
so the acknowledgement log and the event state can never disagree.

Two states exist for withdrawing an event from normal handling. ``ESCALATED``
records that the event was raised past normal handling and can still be
acknowledged and worked. ``CANCELLED`` closes the event out as a false or
withdrawn condition: it is terminal, always carries a reason, and stamps the
event's end time so the event cannot be picked up as open again.
"""
from __future__ import annotations

from datetime import datetime, timezone

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
    "NEW": {"UNACKNOWLEDGED", "CANCELLED"},
    "UNACKNOWLEDGED": {"ACKNOWLEDGED", "ESCALATED", "CANCELLED"},
    "ACKNOWLEDGED": {"ASSIGNED", "ESCALATED", "CANCELLED"},
    "ASSIGNED": {"UNDER_INVESTIGATION", "CANCELLED"},
    "UNDER_INVESTIGATION": {"ACTION_REQUIRED", "RESOLVED", "CANCELLED"},
    "ACTION_REQUIRED": {"RESOLVED", "CANCELLED"},
    "RESOLVED": {"CLOSED"},
    # ESCALATED means the event was raised past normal handling. It is not a
    # dead end: from there the event can still be acknowledged and worked, or
    # withdrawn with a reason.
    "ESCALATED": {"ACKNOWLEDGED", "CANCELLED"},
    # CANCELLED is terminal. A cancelled event was closed out as a false or
    # withdrawn condition, so it is never reopened; a new observation produces a
    # new event, which is visible as a separate record rather than a rewrite.
    "CANCELLED": set(),
    "CLOSED": set(),
}

# The states from which each side-effecting operation may start.
ACKNOWLEDGABLE_STATES = {"NEW", "UNACKNOWLEDGED", "ESCALATED"}
ASSIGNABLE_STATES = {"ACKNOWLEDGED"}

# States that close an event out and are never eligible for further handling.
TERMINAL_STATES = frozenset({"CANCELLED", "CLOSED"})


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
    """Move an event to a state the transition map allows, with a reason.

    A cancelled event is closed out here: its end time is stamped so the
    duration of the condition is bounded and it can no longer be picked up as an
    open event. Every other transition only changes the workflow state.
    """
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
    if new_state == "CANCELLED" and event.ended_at is None:
        event.ended_at = datetime.now(timezone.utc)
    db.add(transition)
    db.commit()
    db.refresh(event)
    return event


def acknowledge_event(
    db: Session,
    event: Event,
    *,
    actor_id: str | None = None,
    notes: str | None = None,
) -> Acknowledgement:
    """Record an operator acknowledgement and advance the event in one commit.

    The event must be awaiting acknowledgement. The transition is written in the
    same transaction as the acknowledgement record, so a failure cannot leave a
    logged acknowledgement against an unacknowledged event.

    ``actor_id`` is None whenever the acting identity is unknown, which is the
    normal case now that nothing authenticates a request. The acknowledgement is
    still recorded, because the fact that the event was handled is real even
    when the platform cannot prove who handled it.
    """
    if event.workflow_state not in ACKNOWLEDGABLE_STATES:
        raise WorkflowPreconditionError(
            f"An event in state '{event.workflow_state}' cannot be acknowledged; "
            f"expected one of {sorted(ACKNOWLEDGABLE_STATES)}"
        )
    previous_state = event.workflow_state
    acknowledgement = Acknowledgement(
        event_id=event.id,
        user_id=actor_id,
        notes=notes,
    )
    db.add(acknowledgement)
    db.add(EventStateTransition(
        event_id=event.id,
        user_id=actor_id,
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
    assigner: User | None = None,
    due_at=None,
    notes: str | None = None,
) -> Assignment:
    """Assign an acknowledged event to an active identity and advance the workflow.

    The assignee is a named operator identity, which is meaningful without
    authentication: work is still directed at a person. The assigner is the
    acting identity and is None when it cannot be established.
    """
    if event.workflow_state not in ASSIGNABLE_STATES:
        raise WorkflowPreconditionError(
            f"An event in state '{event.workflow_state}' cannot be assigned; "
            f"expected one of {sorted(ASSIGNABLE_STATES)}"
        )
    if not assignee.is_active:
        raise WorkflowPreconditionError("An event cannot be assigned to an inactive identity")
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

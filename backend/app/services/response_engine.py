"""Explicit, auditable lifecycles for the safety response entities.

Every state change in this module is validated against an explicit transition
map, requires a reason, and writes a durable transition record naming the actor
and both states. There is no path that sets a status directly, so an invalid or
unexplained state cannot reach the database.

This module only moves records that already exist. It never creates an Event,
Incident, or NearMiss, because doing so would require a detector or an operator
decision that has not happened. Incident, near-miss, and corrective-action
creation lives in the API layer and is deliberately gated on a persisted event
and an authenticated operator.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

try:
    from app.models import (
        CorrectiveAction,
        CorrectiveActionStateTransition,
        Incident,
        IncidentStateTransition,
        NearMiss,
        NearMissStateTransition,
    )
except ImportError:
    from backend.app.models import (
        CorrectiveAction,
        CorrectiveActionStateTransition,
        Incident,
        IncidentStateTransition,
        NearMiss,
        NearMissStateTransition,
    )


class InvalidStateTransition(ValueError):
    """Raised when a requested state change is not permitted."""


# An incident is opened from a real observation, investigated, then resolved and
# closed. CLOSED is terminal: a closed incident is reopened by a new incident
# linked to the same event, never by silently rewinding this one.
INCIDENT_TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"INVESTIGATING", "RESOLVED"},
    "INVESTIGATING": {"RESOLVED", "OPEN"},
    "RESOLVED": {"CLOSED", "INVESTIGATING"},
    "CLOSED": set(),
}

NEAR_MISS_TRANSITIONS: dict[str, set[str]] = {
    "REPORTED": {"UNDER_REVIEW", "DISMISSED"},
    "UNDER_REVIEW": {"CONFIRMED", "DISMISSED"},
    "CONFIRMED": {"CLOSED", "UNDER_REVIEW"},
    "DISMISSED": set(),
    "CLOSED": set(),
}

CORRECTIVE_ACTION_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"IN_PROGRESS", "VERIFIED"},
    "IN_PROGRESS": {"VERIFIED"},
    "VERIFIED": {"CLOSED", "IN_PROGRESS"},
    "CLOSED": set(),
}

INCIDENT_STATES = frozenset(INCIDENT_TRANSITIONS)
NEAR_MISS_STATES = frozenset(NEAR_MISS_TRANSITIONS)
CORRECTIVE_ACTION_STATES = frozenset(CORRECTIVE_ACTION_TRANSITIONS)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_reason(reason: str | None) -> str:
    if not reason or not reason.strip():
        raise InvalidStateTransition("A transition reason is required")
    if len(reason.strip()) > 2000:
        raise InvalidStateTransition("A transition reason must be 2000 characters or fewer")
    return reason.strip()


def allowed_transitions(transition_map: dict[str, set[str]], current_state: str) -> list[str]:
    """Return the states reachable from ``current_state``, sorted for stable output."""
    return sorted(transition_map.get(current_state, set()))


def _apply(
    record: Any,
    transition_map: dict[str, set[str]],
    new_state: str,
    *,
    reason: str,
    user_id: str | None,
) -> str:
    cleaned_reason = _require_reason(reason)
    current_state = record.status
    if new_state not in transition_map:
        raise InvalidStateTransition(f"'{new_state}' is not a recognised state")
    if current_state not in transition_map:
        raise InvalidStateTransition(f"Record is in unrecognised state '{current_state}'")
    if new_state == current_state:
        raise InvalidStateTransition(f"Record is already in state '{current_state}'")
    if new_state not in transition_map[current_state]:
        raise InvalidStateTransition(f"Transition {current_state} -> {new_state} is not allowed")
    record.status = new_state
    record.updated_at = _utcnow()
    return cleaned_reason


def transition_incident(
    db: Session,
    incident: Incident,
    new_state: str,
    *,
    user_id: str | None,
    reason: str,
) -> Incident:
    """Move an incident to an allowed state and record the transition."""
    previous_state = incident.status
    cleaned_reason = _apply(incident, INCIDENT_TRANSITIONS, new_state, reason=reason, user_id=user_id)
    db.add(IncidentStateTransition(
        incident_id=incident.id,
        user_id=user_id,
        previous_state=previous_state,
        new_state=new_state,
        reason=cleaned_reason,
    ))
    db.commit()
    db.refresh(incident)
    return incident


def transition_near_miss(
    db: Session,
    near_miss: NearMiss,
    new_state: str,
    *,
    user_id: str | None,
    reason: str,
) -> NearMiss:
    """Move a near-miss to an allowed state and record the transition."""
    previous_state = near_miss.status
    cleaned_reason = _apply(near_miss, NEAR_MISS_TRANSITIONS, new_state, reason=reason, user_id=user_id)
    if new_state == "CLOSED":
        near_miss.closed_at = _utcnow()
    db.add(NearMissStateTransition(
        near_miss_id=near_miss.id,
        user_id=user_id,
        previous_state=previous_state,
        new_state=new_state,
        reason=cleaned_reason,
    ))
    db.commit()
    db.refresh(near_miss)
    return near_miss


def transition_corrective_action(
    db: Session,
    corrective_action: CorrectiveAction,
    new_state: str,
    *,
    user_id: str | None,
    reason: str,
) -> CorrectiveAction:
    """Move a corrective action to an allowed state and record the transition."""
    previous_state = corrective_action.status
    cleaned_reason = _apply(
        corrective_action, CORRECTIVE_ACTION_TRANSITIONS, new_state, reason=reason, user_id=user_id
    )
    if new_state == "VERIFIED":
        corrective_action.completed_at = _utcnow()
    db.add(CorrectiveActionStateTransition(
        corrective_action_id=corrective_action.id,
        user_id=user_id,
        previous_state=previous_state,
        new_state=new_state,
        reason=cleaned_reason,
    ))
    db.commit()
    db.refresh(corrective_action)
    return corrective_action


def transition_history(record: Any) -> list[dict[str, Any]]:
    """Return the recorded transitions for a response record, oldest first."""
    return [
        {
            "previous_state": item.previous_state,
            "new_state": item.new_state,
            "reason": item.reason,
            "user_id": item.user_id,
            "transitioned_at": item.transitioned_at,
        }
        for item in sorted(record.state_transitions, key=lambda entry: entry.transitioned_at)
    ]


def allowed_states(transition_map: dict[str, set[str]]) -> list[str]:
    """Return every known state for a lifecycle, sorted for stable output."""
    return sorted(transition_map)

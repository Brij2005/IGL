"""Escalation evaluation for unhandled safety events.

Escalation is a workflow convenience, not a detection: it re-notifies a role
about an event that already exists in the database. The engine therefore never
creates, re-scores, or re-times an event, and it reports
``ESCALATION_NOT_CONFIGURED`` when no policy matches instead of inventing a
chain.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy.orm import Session

try:
    from app.models import EscalationPolicy, Event, EventEscalation, NotificationPolicy, User
except ImportError:
    from backend.app.models import EscalationPolicy, Event, EventEscalation, NotificationPolicy, User


# Events in these states no longer need escalation: somebody is handling them or
# the event is finished.
HANDLED_WORKFLOW_STATES = {
    "ACKNOWLEDGED",
    "ASSIGNED",
    "UNDER_INVESTIGATION",
    "ACTION_REQUIRED",
    "RESOLVED",
    "CLOSED",
}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def matching_policies(db: Session, event: Event) -> List[EscalationPolicy]:
    """Policies that apply to an event, most specific scope first.

    A policy with a NULL event type or severity applies to every event of that
    class; camera and zone scoping is optional.
    """
    candidates = (
        db.query(EscalationPolicy)
        .filter(EscalationPolicy.is_active.is_(True))
        .all()
    )
    applicable = []
    for policy in candidates:
        if policy.event_type and policy.event_type != event.event_type:
            continue
        if policy.severity and policy.severity != event.severity:
            continue
        if policy.camera_id and policy.camera_id != event.camera_id:
            continue
        if policy.zone_id and policy.zone_id != event.zone_id:
            continue
        applicable.append(policy)
    return sorted(
        applicable,
        key=lambda policy: (
            bool(policy.camera_id),
            bool(policy.zone_id),
            -policy.escalation_level,
        ),
        reverse=True,
    )


def evaluate_event_escalation(db: Session, event: Event, now: datetime | None = None) -> dict:
    """Report which policies have fired for an event, and create what is due.

    Returns a report dictionary rather than raising, so a caller can present
    "nothing is due" and "nothing is configured" as different answers.
    """
    reference = _as_utc(now) or datetime.now(timezone.utc)
    report = {
        "event_id": event.id,
        "evaluated_at": reference,
        "workflow_state": event.workflow_state,
        "handled": event.workflow_state in HANDLED_WORKFLOW_STATES,
        "policies_matched": 0,
        "escalations_created": [],
        "status": "EVALUATED",
    }

    if report["handled"]:
        report["status"] = "EVENT_ALREADY_HANDLED"
        return report

    policies = matching_policies(db, event)
    report["policies_matched"] = len(policies)
    if not policies:
        report["status"] = "ESCALATION_NOT_CONFIGURED"
        return report

    started_at = _as_utc(event.started_at) or reference
    already_applied = {
        escalation.policy_id
        for escalation in db.query(EventEscalation).filter(EventEscalation.event_id == event.id).all()
    }

    created = []
    for policy in policies:
        if policy.id in already_applied:
            continue
        due_at = started_at + timedelta(seconds=policy.escalate_after_seconds)
        if reference < due_at:
            continue
        escalation = EventEscalation(
            event_id=event.id,
            policy_id=policy.id,
            escalation_level=policy.escalation_level,
            from_role=policy.from_role,
            to_role=policy.to_role,
            reason=(
                f"Event {event.event_type} ({event.severity}) was not handled within "
                f"{policy.escalate_after_seconds}s of policy '{policy.name}'"
            ),
            triggered_at=reference,
        )
        db.add(escalation)
        created.append(escalation)

    if created:
        db.commit()
        for escalation in created:
            db.refresh(escalation)
        report["escalations_created"] = [
            {
                "id": escalation.id,
                "policy_id": escalation.policy_id,
                "escalation_level": escalation.escalation_level,
                "to_role": escalation.to_role,
                "triggered_at": escalation.triggered_at,
            }
            for escalation in created
        ]
        report["status"] = "ESCALATED"
    else:
        report["status"] = "NOT_DUE"

    return report


def pending_escalations(db: Session, now: datetime | None = None) -> List[EventEscalation]:
    """Escalations that were raised but never acknowledged."""
    return (
        db.query(EventEscalation)
        .filter(EventEscalation.acknowledged_at.is_(None))
        .order_by(EventEscalation.triggered_at)
        .all()
    )


def acknowledge_escalation(
    db: Session,
    escalation: EventEscalation,
    user: User | None,
    now: datetime | None = None,
) -> EventEscalation:
    """Record that a named user took responsibility for an escalation."""
    if escalation.acknowledged_at is not None:
        return escalation
    escalation.acknowledged_at = _as_utc(now) or datetime.now(timezone.utc)
    escalation.acknowledged_by_user_id = user.id if user else None
    db.commit()
    db.refresh(escalation)
    return escalation


def notification_targets(db: Session, event: Event) -> List[NotificationPolicy]:
    """Enabled notification policies that apply to an event."""
    applicable = []
    for policy in (
        db.query(NotificationPolicy).filter(NotificationPolicy.is_enabled.is_(True)).all()
    ):
        if policy.event_type and policy.event_type != event.event_type:
            continue
        if policy.severity and policy.severity != event.severity:
            continue
        if policy.zone_id and policy.zone_id != event.zone_id:
            continue
        applicable.append(policy)
    return applicable

"""Alarm raising, suppression, acknowledgement and expiry.

An alarm exists only because a real, confirmed safety event exists. This module
never invents an alarm, and it never reports an alarm as raised for a severity
below the configured policy or while a cooldown or repeat window is in force.

The policy is explicit and operator-tunable:

``ALARM_ENABLED``
    Master switch. When false no alarm is raised at all and the health layer
    reports ``ALARM_DISABLED_BY_POLICY``.
``ALARM_MIN_SEVERITY``
    Events below this severity never raise an alarm.
``ALARM_COOLDOWN_SECONDS``
    Minimum spacing between two alarm raises for the same event and rule.
``ALARM_MAX_REPEATS_PER_WINDOW`` / ``ALARM_REPEAT_WINDOW_SECONDS``
    Bounds how often one sustained condition may re-raise inside a window.
``ALARM_AUTO_EXPIRE_SECONDS``
    Unacknowledged alarms expire so the board cannot fill with stale alarms.

Every state change is validated against :data:`ALARM_TRANSITIONS`, requires a
reason, and writes an :class:`AlarmStateTransition` audit row.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.models import Alarm, AlarmStateTransition, Event
    from app.services import physical_alarm
except ImportError:  # pragma: no cover - import shim for direct script use
    from backend.app.config import settings
    from backend.app.models import Alarm, AlarmStateTransition, Event
    from backend.app.services import physical_alarm


logger = logging.getLogger("igl.alarm.engine")

SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

# ACTIVE is the only state an operator can act on. ESCALATED is entered by the
# escalation worker. SUPPRESSED, EXPIRED, CLEARED and ACKNOWLEDGED are terminal
# with respect to further automatic raising.
ALARM_TRANSITIONS: dict[str, set[str]] = {
    "ACTIVE": {"ACKNOWLEDGED", "ESCALATED", "CLEARED", "EXPIRED"},
    "ESCALATED": {"ACKNOWLEDGED", "CLEARED", "EXPIRED"},
    "ACKNOWLEDGED": {"CLEARED", "ESCALATED"},
    "SUPPRESSED": {"ACTIVE"},
    "EXPIRED": set(),
    "CLEARED": set(),
}

ACTIVE_STATES = ("ACTIVE", "ESCALATED")
ALARM_STATES = frozenset(ALARM_TRANSITIONS)

SUPPRESSED_COOLDOWN = "COOLDOWN_ACTIVE"
SUPPRESSED_REPEAT_LIMIT = "REPEAT_LIMIT_REACHED"
SUPPRESSED_BELOW_MIN_SEVERITY = "BELOW_MIN_SEVERITY"
SUPPRESSED_DISABLED = "ALARM_DISABLED_BY_POLICY"


class InvalidAlarmTransition(ValueError):
    """Raised when a requested alarm state change is not permitted."""


class AlarmPreconditionError(ValueError):
    """Raised when an alarm operation's starting state does not permit it."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """Normalise a stored timestamp to an aware UTC value for comparison."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def allowed_transitions(current_state: str) -> list[str]:
    return sorted(ALARM_TRANSITIONS.get(current_state, set()))


def _require_reason(reason: str | None) -> str:
    if not reason or not reason.strip():
        raise InvalidAlarmTransition("A transition reason is required")
    if len(reason.strip()) > 2000:
        raise InvalidAlarmTransition("A transition reason must be 2000 characters or fewer")
    return reason.strip()


def _severity_allows(severity: str) -> bool:
    required = SEVERITY_ORDER.get(settings.ALARM_MIN_SEVERITY, 1)
    return SEVERITY_ORDER.get(severity, 0) >= required


def _alarm_context(alarm: Alarm) -> dict[str, Any]:
    return {
        "alarm_id": alarm.id,
        "event_id": alarm.event_id,
        "incident_id": alarm.incident_id,
        "camera_id": alarm.camera_id,
        "zone_id": alarm.zone_id,
        "severity": alarm.severity,
        "detector_key": alarm.detector_key,
        "confidence": alarm.confidence,
        "model_name": alarm.model_name,
        "model_version": alarm.model_version,
        "raised_at": alarm.raised_at.isoformat() if alarm.raised_at else None,
    }


def suppressions_for_event(event: Event) -> list[str]:
    """Return the policy reasons an alarm for this event would be suppressed."""
    reasons: list[str] = []
    if not settings.ALARM_ENABLED:
        reasons.append(SUPPRESSED_DISABLED)
    severity = event.severity
    if not _severity_allows(severity):
        reasons.append(SUPPRESSED_BELOW_MIN_SEVERITY)
    return reasons


def _open_alarm(db: Session, event_id: str) -> Alarm | None:
    return (
        db.query(Alarm)
        .filter(Alarm.event_id == event_id, Alarm.state.in_(ACTIVE_STATES + ("ACKNOWLEDGED",)))
        .order_by(Alarm.raised_at.desc())
        .first()
    )


def _record_transition(
    db: Session,
    alarm: Alarm,
    previous_state: str | None,
    new_state: str,
    reason: str,
    user_id: str | None,
    commit: bool = True,
) -> None:
    db.add(AlarmStateTransition(
        alarm_id=alarm.id,
        user_id=user_id,
        previous_state=previous_state,
        new_state=new_state,
        reason=reason,
    ))
    alarm.state = new_state
    alarm.updated_at = _utcnow()
    if commit:
        db.commit()


def evaluate_alarm_policy(db: Session, event: Event) -> dict[str, Any]:
    """Decide whether a confirmed event should raise an alarm right now.

    Returns an explicit decision with the reason. ``raise_alarm`` is True only
    when a new alarm row is created; ``suppressed`` states carry the policy
    reason instead of pretending an alarm happened.
    """
    decision: dict[str, Any] = {
        "raise_alarm": False,
        "suppressed": False,
        "reason": None,
        "existing_alarm_id": None,
        "repeat_count": 0,
    }
    if event.observation_state != "CONFIRMED":
        decision["reason"] = "EVENT_NOT_CONFIRMED"
        return decision
    hard_reasons = suppressions_for_event(event)
    if hard_reasons:
        decision["suppressed"] = True
        decision["reason"] = hard_reasons[0]
        return decision

    now = _utcnow()
    window_start = now - timedelta(seconds=settings.ALARM_REPEAT_WINDOW_SECONDS)
    recent = (
        db.query(Alarm)
        .filter(
            Alarm.event_id == event.id,
            Alarm.raised_at >= window_start,
        )
        .order_by(Alarm.raised_at.desc())
        .all()
    )
    if recent:
        latest = recent[0]
        decision["existing_alarm_id"] = latest.id
        decision["repeat_count"] = len(recent)
        cooldown_until = _aware(latest.cooldown_until)
        if latest.state in ACTIVE_STATES and cooldown_until and cooldown_until > now:
            decision["suppressed"] = True
            decision["reason"] = SUPPRESSED_COOLDOWN
            return decision
        if len(recent) >= settings.ALARM_MAX_REPEATS_PER_WINDOW:
            decision["suppressed"] = True
            decision["reason"] = SUPPRESSED_REPEAT_LIMIT
            return decision
    decision["raise_alarm"] = True
    decision["reason"] = "CONFIRMED_EVENT_RAISED_ALARM"
    return decision


def raise_alarm_for_event(db: Session, event: Event) -> dict[str, Any]:
    """Raise (or refuse to raise) the software alarm for a confirmed event.

    This is the only function that creates an alarm row. Repeated raises for the
    same event inside the repeat window update the existing open alarm's counters
    and cooldown instead of creating a duplicate, which is what stops a
    sustained condition from producing an alarm storm.
    """
    decision = evaluate_alarm_policy(db, event)
    if not decision["raise_alarm"]:
        return {"raised": False, "state": None, **decision}

    now = _utcnow()
    expires_at = now + timedelta(seconds=settings.ALARM_AUTO_EXPIRE_SECONDS) if settings.ALARM_AUTO_EXPIRE_SECONDS else None
    existing = _open_alarm(db, event.id)

    if existing is not None and existing.state in ACTIVE_STATES:
        existing.raised_count = (existing.raised_count or 0) + 1
        existing.cooldown_until = now + timedelta(seconds=settings.ALARM_COOLDOWN_SECONDS)
        if settings.ALARM_COOLDOWN_SECONDS <= 0:
            existing.cooldown_until = None
        if expires_at:
            existing.expires_at = expires_at
        db.commit()
        db.refresh(existing)
        if settings.ALARM_PHYSICAL_ACTUATION and physical_alarm.configured_transports():
            actuate_physical(db, existing)
            db.refresh(existing)
        return {"raised": True, "state": existing.state, "alarm_id": existing.id, "reactivated": True, **decision}

    alarm = Alarm(
        event_id=event.id,
        camera_id=event.camera_id,
        zone_id=event.zone_id,
        track_id=event.track_id,
        severity=event.severity,
        state="ACTIVE",
        detector_key=event.detector_key,
        confidence=event.confidence,
        model_name=event.model_name,
        model_version=event.model_version,
        reason=f"Confirmed safety event {event.id} raised a {event.severity} alarm",
        cooldown_until=(now + timedelta(seconds=settings.ALARM_COOLDOWN_SECONDS)) if settings.ALARM_COOLDOWN_SECONDS > 0 else None,
        expires_at=expires_at,
        physical_state=physical_alarm.PHYSICAL_NOT_CONFIGURED,
        provenance_json={
            "event_type": event.event_type,
            "verification_state": event.verification_state,
            "observation_state": event.observation_state,
            "workflow_state": event.workflow_state,
            "started_at": event.started_at.isoformat() if event.started_at else None,
            "model_weights_checksum": event.model_weights_checksum,
        },
    )
    db.add(alarm)
    db.flush()
    _record_transition(
        db,
        alarm,
        previous_state=None,
        new_state="ACTIVE",
        reason=alarm.reason,
        user_id=None,
        commit=False,
    )
    db.commit()
    db.refresh(alarm)
    if settings.ALARM_PHYSICAL_ACTUATION and physical_alarm.configured_transports():
        # A real transport is configured, so the alarm attempts real actuation
        # and records whatever that attempt actually returned.
        alarm.physical_state = physical_alarm.ACTIVATION_ATTEMPTED
        db.commit()
        actuate_physical(db, alarm)
        db.refresh(alarm)
    return {"raised": True, "state": "ACTIVE", "alarm_id": alarm.id, "reactivated": False, **decision}


def attach_incident(alarm: Alarm, incident_id: str | None) -> None:
    """Link an alarm to the incident raised from its event, when one exists."""
    if incident_id and alarm.incident_id != incident_id:
        alarm.incident_id = incident_id


def acknowledge_alarm(
    db: Session,
    alarm: Alarm,
    *,
    user_id: str | None = None,
    notes: str | None = None,
) -> Alarm:
    """Acknowledge an active or escalated alarm and audit the operator action."""
    if alarm.state not in ACTIVE_STATES:
        raise AlarmPreconditionError(
            f"An alarm in state '{alarm.state}' cannot be acknowledged; expected one of {list(ACTIVE_STATES)}"
        )
    reason = _require_reason(notes or "Alarm acknowledged by operator")
    previous_state = alarm.state
    alarm.acknowledged_at = _utcnow()
    alarm.acknowledged_by_user_id = user_id
    _record_transition(db, alarm, previous_state, "ACKNOWLEDGED", reason, user_id)
    db.refresh(alarm)
    return alarm


def escalate_alarm(db: Session, alarm: Alarm, *, user_id: str | None = None, reason: str | None = None) -> Alarm:
    """Move an unacknowledged alarm to ESCALATED, keeping the audit trail."""
    if alarm.state != "ACTIVE":
        raise AlarmPreconditionError(f"An alarm in state '{alarm.state}' cannot be escalated")
    cleaned = _require_reason(reason or "Alarm escalated to a higher response tier")
    _record_transition(db, alarm, "ACTIVE", "ESCALATED", cleaned, user_id)
    db.refresh(alarm)
    return alarm


def clear_alarm(
    db: Session,
    alarm: Alarm,
    *,
    user_id: str | None = None,
    reason: str | None = None,
    deactivate_physical: bool = True,
) -> Alarm:
    """Clear a live alarm and release any physical actuator that was activated."""
    if alarm.state not in ACTIVE_STATES + ("ACKNOWLEDGED",):
        raise AlarmPreconditionError(f"An alarm in state '{alarm.state}' cannot be cleared")
    cleaned = _require_reason(reason or "Alarm cleared by operator")
    if deactivate_physical and alarm.physical_state == physical_alarm.ACTIVATED:
        outcome = physical_alarm.actuate(_alarm_context(alarm), "DEACTIVATE")
        alarm.physical_state = outcome["state"]
        alarm.physical_result = outcome["reason"]
        alarm.physical_cleared_at = _utcnow() if outcome["state"] == physical_alarm.DEACTIVATED else None
    previous_state = alarm.state
    alarm.cleared_at = _utcnow()
    _record_transition(db, alarm, previous_state, "CLEARED", cleaned, user_id)
    db.refresh(alarm)
    return alarm


def record_suppression(db: Session, event: Event, reason: str) -> dict[str, Any]:
    """Record that the alarm policy suppressed an alarm for a real event.

    Suppression is not a silent no-op: it increments the open alarm's counter
    when one exists, so an operator can see that a condition kept firing while
    the policy held the board quiet.
    """
    existing = _open_alarm(db, event.id)
    if existing is None:
        return {"recorded": False, "reason": reason, "alarm_id": None}
    previous_state = existing.state
    existing.suppression_count = (existing.suppression_count or 0) + 1
    _record_transition(
        db,
        existing,
        previous_state=previous_state,
        new_state=previous_state,
        reason=f"Alarm suppressed by policy: {reason}",
        user_id=None,
    )
    db.refresh(existing)
    return {"recorded": True, "reason": reason, "alarm_id": existing.id, "suppression_count": existing.suppression_count}


def actuate_physical(db: Session, alarm: Alarm) -> dict[str, Any]:
    """Attempt real physical actuation for an alarm, recording the true result."""
    outcome = physical_alarm.actuate(_alarm_context(alarm), "ACTIVATE")
    alarm.physical_state = outcome["state"]
    alarm.physical_result = f"{outcome['transport']}:{outcome['reason']}" if outcome["reason"] else outcome["transport"]
    if outcome["state"] == physical_alarm.ACTIVATED:
        alarm.physical_activated_at = _utcnow()
    alarm.updated_at = _utcnow()
    db.commit()
    db.refresh(alarm)
    return outcome


def expire_stale_alarms(db: Session, now: datetime | None = None) -> int:
    """Expire unacknowledged alarms whose expiry has passed."""
    moment = now or _utcnow()
    stale = (
        db.query(Alarm)
        .filter(Alarm.state.in_(ACTIVE_STATES), Alarm.expires_at.isnot(None), Alarm.expires_at <= moment)
        .all()
    )
    for alarm in stale:
        _record_transition(
            db,
            alarm,
            previous_state=alarm.state,
            new_state="EXPIRED",
            reason="Alarm expired without acknowledgement",
            user_id=None,
            commit=False,
        )
    if stale:
        db.commit()
    return len(stale)


def active_alarms(db: Session, limit: int = 100) -> list[Alarm]:
    return (
        db.query(Alarm)
        .filter(Alarm.state.in_(ACTIVE_STATES))
        .order_by(Alarm.raised_at.desc())
        .limit(limit)
        .all()
    )


def alarm_summary(db: Session) -> dict[str, Any]:
    """Counts and the newest live alarm, derived only from persisted rows."""
    expire_stale_alarms(db)
    total = db.query(Alarm).count()
    active = db.query(Alarm).filter(Alarm.state.in_(ACTIVE_STATES)).count()
    acknowledged = db.query(Alarm).filter(Alarm.state == "ACKNOWLEDGED").count()
    escalated = db.query(Alarm).filter(Alarm.state == "ESCALATED").count()
    suppressed = db.query(Alarm).filter(Alarm.state == "SUPPRESSED").count()
    expired = db.query(Alarm).filter(Alarm.state == "EXPIRED").count()
    cleared = db.query(Alarm).filter(Alarm.state == "CLEARED").count()
    latest = (
        db.query(Alarm)
        .filter(Alarm.state.in_(ACTIVE_STATES))
        .order_by(Alarm.raised_at.desc())
        .first()
    )
    return {
        "alarm_policy_enabled": settings.ALARM_ENABLED,
        "min_severity": settings.ALARM_MIN_SEVERITY,
        "cooldown_seconds": settings.ALARM_COOLDOWN_SECONDS,
        "max_repeats_per_window": settings.ALARM_MAX_REPEATS_PER_WINDOW,
        "repeat_window_seconds": settings.ALARM_REPEAT_WINDOW_SECONDS,
        "auto_expire_seconds": settings.ALARM_AUTO_EXPIRE_SECONDS,
        "audible_browser_alarm": settings.ALARM_AUDIBLE_BROWSER,
        "total_alarms": total,
        "active_alarm_count": active,
        "escalated_alarm_count": escalated,
        "acknowledged_alarm_count": acknowledged,
        "suppressed_alarm_count": suppressed,
        "expired_alarm_count": expired,
        "cleared_alarm_count": cleared,
        "last_alarm_id": latest.id if latest else None,
        "last_alarm_raised_at": latest.raised_at if latest else None,
        "physical_alarm": physical_alarm.actuator_status(),
    }

"""Alarm centre: live alarms, history, acknowledgement, and physical state.

Every response here is derived from persisted alarm rows. The API never creates
an alarm: alarms are raised by the alarm engine when a real safety event is
confirmed, or by an explicitly authorised operator re-raise of an existing
confirmed event.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

try:
    from app.access_control import log_audit_event, require_permission, require_role
    from app.database import get_db
    from app.models import Alarm, AlarmStateTransition, Event, User
    from app.services import alarm_engine, physical_alarm
except ImportError:  # pragma: no cover - import shim for direct script use
    from backend.app.access_control import log_audit_event, require_permission, require_role
    from backend.app.database import get_db
    from backend.app.models import Alarm, AlarmStateTransition, Event, User
    from backend.app.services import alarm_engine, physical_alarm


router = APIRouter()


class AlarmOut(BaseModel):
    id: str
    event_id: str
    camera_id: Optional[str] = None
    zone_id: Optional[str] = None
    track_id: Optional[str] = None
    incident_id: Optional[str] = None
    severity: str
    state: str
    detector_key: Optional[str] = None
    confidence: Optional[float] = None
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    reason: Optional[str] = None
    raised_count: int
    suppression_count: int
    cooldown_until: Optional[datetime] = None
    raised_at: datetime
    acknowledged_at: Optional[datetime] = None
    acknowledged_by_user_id: Optional[str] = None
    cleared_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    physical_state: str
    physical_result: Optional[str] = None
    physical_activated_at: Optional[datetime] = None
    physical_cleared_at: Optional[datetime] = None
    allowed_transitions: list[str] = Field(default_factory=list)


class AlarmStateTransitionOut(BaseModel):
    previous_state: Optional[str] = None
    new_state: str
    reason: str
    user_id: Optional[str] = None
    transitioned_at: datetime


class AlarmPolicyOut(BaseModel):
    alarm_policy_enabled: bool
    min_severity: str
    cooldown_seconds: float
    max_repeats_per_window: int
    repeat_window_seconds: float
    auto_expire_seconds: float
    audible_browser_alarm: bool
    total_alarms: int
    active_alarm_count: int
    escalated_alarm_count: int
    acknowledged_alarm_count: int
    suppressed_alarm_count: int
    expired_alarm_count: int
    cleared_alarm_count: int
    last_alarm_id: Optional[str] = None
    last_alarm_raised_at: Optional[datetime] = None
    physical_alarm: dict


class PhysicalAlarmTestResult(BaseModel):
    state: str
    transport: str
    reason: Optional[str] = None
    hardware_verified: bool = False


class AcknowledgeRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)


class ClearRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)
    deactivate_physical: bool = True


class ReraiseRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


def _serialize(alarm: Alarm) -> AlarmOut:
    return AlarmOut(
        id=alarm.id,
        event_id=alarm.event_id,
        camera_id=alarm.camera_id,
        zone_id=alarm.zone_id,
        track_id=alarm.track_id,
        incident_id=alarm.incident_id,
        severity=alarm.severity,
        state=alarm.state,
        detector_key=alarm.detector_key,
        confidence=alarm.confidence,
        model_name=alarm.model_name,
        model_version=alarm.model_version,
        reason=alarm.reason,
        raised_count=alarm.raised_count or 0,
        suppression_count=alarm.suppression_count or 0,
        cooldown_until=alarm.cooldown_until,
        raised_at=alarm.raised_at,
        acknowledged_at=alarm.acknowledged_at,
        acknowledged_by_user_id=alarm.acknowledged_by_user_id,
        cleared_at=alarm.cleared_at,
        expires_at=alarm.expires_at,
        physical_state=alarm.physical_state,
        physical_result=alarm.physical_result,
        physical_activated_at=alarm.physical_activated_at,
        physical_cleared_at=alarm.physical_cleared_at,
        allowed_transitions=alarm_engine.allowed_transitions(alarm.state),
    )


@router.get("", response_model=list[AlarmOut])
def list_alarms(
    state: Optional[str] = Query(default=None, max_length=30),
    active_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    """Alarm history, newest first. ``active_only`` returns the live board."""
    if active_only:
        return [_serialize(item) for item in alarm_engine.active_alarms(db, limit=limit)]
    query = db.query(Alarm)
    if state:
        query = query.filter(Alarm.state == state)
    rows = query.order_by(Alarm.raised_at.desc()).offset(offset).limit(limit).all()
    return [_serialize(item) for item in rows]


@router.get("/policy", response_model=AlarmPolicyOut)
def get_alarm_policy(
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    """Report the live alarm policy, counters, and physical actuator state."""
    return alarm_engine.alarm_summary(db)


@router.get("/physical/status", response_model=dict)
def get_physical_alarm_status(
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    """Report the physical alarm state without claiming hardware is connected."""
    return physical_alarm.actuator_status()


@router.post("/physical/test", response_model=PhysicalAlarmTestResult)
def test_physical_alarm(
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER")),
):
    """Attempt a real physical alarm actuation for an authorised operator.

    This is deliberately not a simulation. With no transport configured the
    response is ``PHYSICAL_ALARM_NOT_CONFIGURED``; with a transport configured
    but no hardware or driver library the response is ``ACTUATOR_NOT_CONNECTED``.
    """
    outcome = physical_alarm.actuate(
        {
            "alarm_id": None,
            "event_id": None,
            "incident_id": None,
            "camera_id": None,
            "zone_id": None,
            "severity": "TEST",
            "detector_key": "OPERATOR_TEST",
            "confidence": None,
            "model_name": None,
            "model_version": None,
            "raised_at": datetime.now().astimezone().isoformat(),
        },
        "ACTIVATE",
    )
    log_audit_event(
        db,
        actor.id if actor else None,
        "PHYSICAL_ALARM_TEST_ATTEMPTED",
        "PHYSICAL_ALARM_ACTUATOR",
        details_json={"state": outcome["state"], "transport": outcome["transport"]},
        ip_address=request.client.host if request.client else None,
    )
    status = physical_alarm.actuator_status()
    return {**outcome, "hardware_verified": bool(status.get("hardware_verified")) and outcome["state"] == physical_alarm.ACTIVATED}


@router.get("/{alarm_id}", response_model=AlarmOut)
def get_alarm(
    alarm_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    alarm = db.query(Alarm).filter(Alarm.id == alarm_id).first()
    if alarm is None:
        raise HTTPException(status_code=404, detail="Alarm not found")
    return _serialize(alarm)


@router.get("/{alarm_id}/history", response_model=list[AlarmStateTransitionOut])
def get_alarm_history(
    alarm_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    """The audited alarm state history, oldest first."""
    alarm = db.query(Alarm).filter(Alarm.id == alarm_id).first()
    if alarm is None:
        raise HTTPException(status_code=404, detail="Alarm not found")
    rows = (
        db.query(AlarmStateTransition)
        .filter(AlarmStateTransition.alarm_id == alarm_id)
        .order_by(AlarmStateTransition.transitioned_at.asc())
        .all()
    )
    return [
        AlarmStateTransitionOut(
            previous_state=item.previous_state,
            new_state=item.new_state,
            reason=item.reason,
            user_id=item.user_id,
            transitioned_at=item.transitioned_at,
        )
        for item in rows
    ]


@router.post("/{alarm_id}/acknowledge", response_model=AlarmOut)
def acknowledge_alarm(
    alarm_id: str,
    payload: AcknowledgeRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER", "PLANT_MANAGER", "SUPERVISOR")),
):
    """Acknowledge a live alarm. Viewers are not permitted to acknowledge."""
    alarm = db.query(Alarm).filter(Alarm.id == alarm_id).first()
    if alarm is None:
        raise HTTPException(status_code=404, detail="Alarm not found")
    try:
        alarm = alarm_engine.acknowledge_alarm(
            db, alarm, user_id=actor.id if actor else None, notes=payload.notes
        )
    except alarm_engine.InvalidAlarmTransition as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except alarm_engine.AlarmPreconditionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_audit_event(
        db,
        actor.id if actor else None,
        "ALARM_ACKNOWLEDGED",
        "ALARM",
        resource_id=alarm.id,
        details_json={"event_id": alarm.event_id, "notes": payload.notes},
        ip_address=request.client.host if request.client else None,
    )
    return _serialize(alarm)


@router.post("/{alarm_id}/escalate", response_model=AlarmOut)
def escalate_alarm(
    alarm_id: str,
    request: Request,
    reason: str = Query(..., min_length=1, max_length=2000),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER", "PLANT_MANAGER")),
):
    alarm = db.query(Alarm).filter(Alarm.id == alarm_id).first()
    if alarm is None:
        raise HTTPException(status_code=404, detail="Alarm not found")
    try:
        alarm = alarm_engine.escalate_alarm(db, alarm, user_id=actor.id if actor else None, reason=reason)
    except alarm_engine.InvalidAlarmTransition as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except alarm_engine.AlarmPreconditionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_audit_event(
        db,
        actor.id if actor else None,
        "ALARM_ESCALATED",
        "ALARM",
        resource_id=alarm.id,
        ip_address=request.client.host if request.client else None,
    )
    return _serialize(alarm)


@router.post("/{alarm_id}/clear", response_model=AlarmOut)
def clear_alarm(
    alarm_id: str,
    payload: ClearRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER", "PLANT_MANAGER", "SUPERVISOR")),
):
    alarm = db.query(Alarm).filter(Alarm.id == alarm_id).first()
    if alarm is None:
        raise HTTPException(status_code=404, detail="Alarm not found")
    try:
        alarm = alarm_engine.clear_alarm(
            db,
            alarm,
            user_id=actor.id if actor else None,
            reason=payload.reason,
            deactivate_physical=payload.deactivate_physical,
        )
    except alarm_engine.InvalidAlarmTransition as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except alarm_engine.AlarmPreconditionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_audit_event(
        db,
        actor.id if actor else None,
        "ALARM_CLEARED",
        "ALARM",
        resource_id=alarm.id,
        details_json={"reason": payload.reason},
        ip_address=request.client.host if request.client else None,
    )
    return _serialize(alarm)


@router.post("/events/{event_id}/raise", response_model=AlarmOut)
def raise_alarm_for_event(
    event_id: str,
    payload: ReraiseRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER")),
):
    """Evaluate the alarm policy for a real event and raise an alarm if allowed.

    This never fabricates an alarm: the event must exist and must already be
    ``CONFIRMED``, and the policy can still suppress the raise.
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    decision = alarm_engine.evaluate_alarm_policy(db, event)
    if not decision["raise_alarm"]:
        log_audit_event(
            db,
            actor.id if actor else None,
            "ALARM_RAISE_SUPPRESSED",
            "EVENT",
            resource_id=event.id,
            details_json={"reason": decision["reason"]},
            ip_address=request.client.host if request.client else None,
        )
        raise HTTPException(status_code=409, detail=f"Alarm not raised: {decision['reason']}")
    outcome = alarm_engine.raise_alarm_for_event(db, event)
    alarm = db.query(Alarm).filter(Alarm.id == outcome["alarm_id"]).first()
    if alarm is None:
        raise HTTPException(status_code=409, detail="Alarm was not persisted")
    log_audit_event(
        db,
        actor.id if actor else None,
        "ALARM_RAISED_BY_OPERATOR",
        "ALARM",
        resource_id=alarm.id,
        details_json={"event_id": event.id, "reason": payload.reason},
        ip_address=request.client.host if request.client else None,
    )
    return _serialize(alarm)

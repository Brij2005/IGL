"""Escalation and correlation reporting over persisted events.

Every response here is derived from rows that exist in the database. A policy
that is absent yields ``ESCALATION_NOT_CONFIGURED``, and evaluating an event
that is already handled yields ``EVENT_ALREADY_HANDLED``; neither is reported as
a successful escalation. Calling an evaluate endpoint never creates a
notification by itself: notifications only exist where a notification policy
applied to a real observation.
"""
from __future__ import annotations

from typing import Optional, Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

try:
    from app.access_control import log_audit_event, require_permission, require_role
    from app.database import get_db
    from app.models import Event, EventEscalation, User
    from app.services.correlation_engine import correlation_summary, repeat_event_summary
    from app.services.escalation_engine import (
        acknowledge_escalation,
        evaluate_event_escalation,
        matching_policies,
        pending_escalations,
    )
except ImportError:  # pragma: no cover
    from backend.app.access_control import log_audit_event, require_permission, require_role
    from backend.app.database import get_db
    from backend.app.models import Event, EventEscalation, User
    from backend.app.services.correlation_engine import correlation_summary, repeat_event_summary
    from backend.app.services.escalation_engine import (
        acknowledge_escalation,
        evaluate_event_escalation,
        matching_policies,
        pending_escalations,
    )


router = APIRouter()
ESCALATION_WRITERS = ("ADMIN", "SAFETY_OFFICER")


class EscalationReason(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)


class EventEscalationOut(BaseModel):
    id: str
    event_id: str
    policy_id: str
    escalation_level: int
    from_role: Optional[str] = None
    to_role: str
    reason: str
    triggered_at: Any
    acknowledged_at: Optional[Any] = None
    acknowledged_by_user_id: Optional[str] = None

    model_config = {"from_attributes": True}


class EscalationEvaluationOut(BaseModel):
    """Result of evaluating one event against configured policies."""

    event_id: str
    workflow_state: str
    handled: bool
    status: str
    policies_matched: int
    escalations_created: List[dict] = Field(default_factory=list)


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def _load_event(db: Session, event_id: str) -> Event:
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.get("/events/{event_id}/escalations", response_model=List[EventEscalationOut])
def list_event_escalations(
    event_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    """Escalation records that actually exist for one persisted event."""
    _load_event(db, event_id)
    return (
        db.query(EventEscalation)
        .filter(EventEscalation.event_id == event_id)
        .order_by(EventEscalation.triggered_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.post("/events/{event_id}/escalations/evaluate", response_model=EscalationEvaluationOut)
def evaluate_escalations_for_event(
    event_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role(*ESCALATION_WRITERS)),
):
    """Apply configured escalation policies to one persisted event.

    The endpoint reports what the policies actually decided. With no matching
    policy the status is ``ESCALATION_NOT_CONFIGURED``; with a policy that is
    not yet due it is ``NOT_DUE``. Neither creates a notification.
    """
    event = _load_event(db, event_id)
    report = evaluate_event_escalation(db, event)
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="ESCALATION_EVALUATED",
        resource_type="EVENT",
        resource_id=event.id,
        details_json={
            "status": report["status"],
            "policies_matched": report["policies_matched"],
            "escalations_created": len(report["escalations_created"]),
        },
        ip_address=_client_ip(request),
    )
    return EscalationEvaluationOut(
        event_id=report["event_id"],
        workflow_state=report["workflow_state"],
        handled=report["handled"],
        status=report["status"],
        policies_matched=report["policies_matched"],
        escalations_created=report["escalations_created"],
    )


@router.get("/events/{event_id}/escalations/policies")
def list_matching_escalation_policies(
    event_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    """Show which configured policies would apply, without applying them."""
    event = _load_event(db, event_id)
    policies = matching_policies(db, event)
    return {
        "event_id": event.id,
        "policy_count": len(policies),
        "status": "POLICIES_MATCHED" if policies else "ESCALATION_NOT_CONFIGURED",
        "policies": [
            {
                "id": policy.id,
                "name": policy.name,
                "escalation_level": policy.escalation_level,
                "escalate_after_seconds": policy.escalate_after_seconds,
                "from_role": policy.from_role,
                "to_role": policy.to_role,
                "threshold_source": "CONFIGURED",
            }
            for policy in policies
        ],
    }


@router.post("/escalations/{escalation_id}/acknowledge", response_model=EventEscalationOut)
def acknowledge_escalation_record(
    escalation_id: str,
    payload: EscalationReason,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role(*ESCALATION_WRITERS)),
):
    """Record responsibility for a pending escalation."""
    escalation = db.query(EventEscalation).filter(EventEscalation.id == escalation_id).first()
    if escalation is None:
        raise HTTPException(status_code=404, detail="Escalation not found")
    if escalation.acknowledged_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This escalation was already acknowledged",
        )
    acknowledge_escalation(db, escalation, actor)
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="ESCALATION_ACKNOWLEDGED",
        resource_type="EVENT_ESCALATION",
        resource_id=escalation.id,
        details_json={"event_id": escalation.event_id, "reason": payload.reason},
        ip_address=_client_ip(request),
    )
    db.refresh(escalation)
    return escalation


@router.get("/escalations/pending", response_model=List[EventEscalationOut])
def list_pending_escalations(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    """Escalations raised and not yet acknowledged."""
    return pending_escalations(db)[offset:offset + limit]


@router.get("/analytics/correlations")
def list_event_correlations(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("analytics:view")),
):
    """Correlation groups built from persisted events, with their provenance.

    ``event_count`` is the number of stored events folded into the group; an
    empty deployment reports an empty list rather than placeholder groups.
    """
    groups = correlation_summary(db, limit=limit)
    return {
        "data_status": "AVAILABLE" if groups else "NO_DATA",
        "group_count": len(groups),
        "groups": groups,
    }


@router.get("/analytics/repeat-summary")
def repeat_event_report(
    window_hours: int = Query(default=24, ge=1, le=8760),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("analytics:view")),
):
    """Repeat event counts computed from persisted rows only."""
    rows = repeat_event_summary(db, window_hours=window_hours, limit=limit)
    return {
        "data_status": "AVAILABLE" if rows else "NO_DATA",
        "window_hours": window_hours,
        "counted_from": "PERSISTED_EVENTS",
        "rows": rows,
    }
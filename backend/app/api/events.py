"""Event listing and validated workflow transitions.

Access is anonymous only when the development setting explicitly permits it.
Every state change is written to the audit log with a NULL actor.

The read endpoints return the event's full provenance, including the incidents
raised against it. Incident linkage is derived from the incident rows that point
at the event; no column is added to the event to carry it, and an event with no
incident reports an empty list rather than a null, because "no incident was
raised" and "the linkage is unknown" are different facts.
"""
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import tuple_
from sqlalchemy.orm import Session

try:
    from app.access_control import log_audit_event, require_permission, require_role
    from app.database import get_db
    from app.models import Event, EventCorrelation, EventEvidence, Incident, Track, User, Zone
    from app.schemas_events import EventOut, EventWorkflowTransitionRequest
    from app.services.workflow_engine import InvalidWorkflowTransition, allowed_transitions, transition_event
except ImportError:
    from backend.app.access_control import log_audit_event, require_permission, require_role
    from backend.app.database import get_db
    from backend.app.models import Event, EventCorrelation, EventEvidence, Incident, Track, User, Zone
    from backend.app.schemas_events import EventOut, EventWorkflowTransitionRequest
    from backend.app.services.workflow_engine import InvalidWorkflowTransition, allowed_transitions, transition_event


router = APIRouter()


def _parse_frame_timestamp(event: Event) -> Optional[datetime]:
    """The frame timestamp recorded with the event's provenance, if any.

    Returns the event's start time when the provenance carries no frame
    timestamp, which is the case for events written before the field existed.
    """
    provenance = event.provenance_json or {}
    recorded = provenance.get("frame_timestamp")
    if isinstance(recorded, str) and recorded:
        try:
            return datetime.fromisoformat(recorded)
        except ValueError:
            return event.started_at
    return event.started_at


def _incident_ids_by_event(db: Session, event_ids: Iterable[str]) -> Dict[str, List[str]]:
    event_id_list = list(event_ids)
    linkage: Dict[str, List[str]] = {event_id: [] for event_id in event_id_list}
    if not event_id_list:
        return linkage
    rows = (
        db.query(Incident.event_id, Incident.id)
        .filter(Incident.event_id.in_(event_id_list))
        .order_by(Incident.created_at)
        .all()
    )
    for event_id, incident_id in rows:
        linkage.setdefault(event_id, []).append(incident_id)
    return linkage


def _correlation_keys_by_event(db: Session, events: List[Event]) -> Dict[str, List[str]]:
    """Correlation groups each event belongs to, derived from the persisted groups.

    Only the groups matching a camera and event type on the page are read, so the
    query is bounded by the page being returned rather than by the whole history.
    """
    keys: Dict[str, List[str]] = {event.id: [] for event in events}
    if not events:
        return keys
    scopes = {(event.camera_id, event.event_type) for event in events}
    groups = (
        db.query(EventCorrelation)
        .filter(
            tuple_(EventCorrelation.camera_id, EventCorrelation.event_type).in_(list(scopes))
        )
        .all()
    )
    for group in groups:
        for event_id in group.event_ids_json or []:
            if event_id in keys:
                keys[event_id].append(group.correlation_key)
    return keys


def _evidence_counts(db: Session, event_ids: Iterable[str]) -> Dict[str, int]:
    event_id_list = list(event_ids)
    counts: Dict[str, int] = {event_id: 0 for event_id in event_id_list}
    if not event_id_list:
        return counts
    rows = (
        db.query(EventEvidence.event_id, EventEvidence.id)
        .filter(EventEvidence.event_id.in_(event_id_list))
        .all()
    )
    for event_id, _evidence_id in rows:
        counts[event_id] = counts.get(event_id, 0) + 1
    return counts


def event_payloads(db: Session, events: List[Event]) -> List[Dict[str, Any]]:
    """Serialize events with their provenance and their incident linkage."""
    if not events:
        return []
    event_ids = [event.id for event in events]
    linkage = _incident_ids_by_event(db, event_ids)
    correlations = _correlation_keys_by_event(db, events)
    evidence_counts = _evidence_counts(db, event_ids)
    track_ids = [event.track_id for event in events if event.track_id]
    zone_ids = [event.zone_id for event in events if event.zone_id]
    track_uuids = (
        {row.id: row.track_uuid for row in db.query(Track).filter(Track.id.in_(track_ids)).all()}
        if track_ids
        else {}
    )
    zone_codes = (
        {row.id: row.code for row in db.query(Zone).filter(Zone.id.in_(zone_ids)).all()}
        if zone_ids
        else {}
    )
    return [
        {
            "id": event.id,
            "camera_id": event.camera_id,
            "zone_id": event.zone_id,
            "zone_code": zone_codes.get(event.zone_id) if event.zone_id else None,
            "track_id": event.track_id,
            "track_uuid": track_uuids.get(event.track_id) if event.track_id else None,
            "event_type": event.event_type,
            "observation_state": event.observation_state,
            "severity": event.severity,
            "workflow_state": event.workflow_state,
            "confidence": event.confidence,
            "duration_seconds": event.duration_seconds,
            "started_at": event.started_at,
            "ended_at": event.ended_at,
            "model_version": event.model_version,
            "created_at": event.created_at,
            "detector_key": event.detector_key,
            "verification_state": event.verification_state,
            "temporal_observations": event.temporal_observations,
            "temporal_duration_seconds": event.temporal_duration_seconds,
            "threshold_source": event.threshold_source,
            "source_reference": event.source_reference,
            "model_name": event.model_name,
            "model_weights_checksum": event.model_weights_checksum,
            "frame_timestamp": _parse_frame_timestamp(event),
            "evidence_count": evidence_counts.get(event.id, 0),
            "correlation_keys": correlations.get(event.id, []),
            "incident_ids": linkage.get(event.id, []),
            "allowed_transitions": allowed_transitions(event.workflow_state),
            "provenance": event.provenance_json,
        }
        for event in events
    ]


@router.get("", response_model=list[EventOut])
def list_events(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    events = db.query(Event).order_by(Event.started_at.desc()).offset(offset).limit(limit).all()
    return event_payloads(db, events)


@router.get("/{event_id}", response_model=EventOut)
def get_event(
    event_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    """Read one event with its provenance and the incidents raised against it."""
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event_payloads(db, [event])[0]


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
    return event_payloads(db, [event])[0]

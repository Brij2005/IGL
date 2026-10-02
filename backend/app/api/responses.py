"""Safety response APIs: acknowledgement, assignment, incident, near-miss, action.

Every write in this module is an operator action against a record that already
exists. There is no endpoint that generates an Event, Incident, or NearMiss
from a detection, because no safety detector is connected to this platform yet.
Creating a response record requires a persisted event and an authenticated user
with the appropriate role, and each state change is validated, reasoned, and
audited.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

try:
    from app.access_control import log_audit_event, require_permission
    from app.database import get_db
    from app.models import (
        Acknowledgement,
        Assignment,
        CorrectiveAction,
        Event,
        Incident,
        NearMiss,
        User,
    )
    from app.schemas_response import (
        AcknowledgementCreate,
        AcknowledgementOut,
        AssignmentCreate,
        AssignmentOut,
        CorrectiveActionCreate,
        CorrectiveActionOut,
        IncidentCreate,
        IncidentOut,
        NearMissCreate,
        NearMissOut,
        ResponseLifecycleStatesOut,
        StateTransitionOut,
        TransitionRequest,
    )
    from app.services.response_engine import (
        CORRECTIVE_ACTION_TRANSITIONS,
        INCIDENT_TRANSITIONS,
        NEAR_MISS_TRANSITIONS,
        InvalidStateTransition,
        allowed_states,
        allowed_transitions,
        transition_corrective_action,
        transition_history,
        transition_incident,
        transition_near_miss,
    )
    from app.services.workflow_engine import (
        ALLOWED_TRANSITIONS,
        WorkflowPreconditionError,
        acknowledge_event,
        assign_event,
    )
except ImportError:
    from backend.app.access_control import log_audit_event, require_permission
    from backend.app.database import get_db
    from backend.app.models import (
        Acknowledgement,
        Assignment,
        CorrectiveAction,
        Event,
        Incident,
        NearMiss,
        User,
    )
    from backend.app.schemas_response import (
        AcknowledgementCreate,
        AcknowledgementOut,
        AssignmentCreate,
        AssignmentOut,
        CorrectiveActionCreate,
        CorrectiveActionOut,
        IncidentCreate,
        IncidentOut,
        NearMissCreate,
        NearMissOut,
        ResponseLifecycleStatesOut,
        StateTransitionOut,
        TransitionRequest,
    )
    from backend.app.services.response_engine import (
        CORRECTIVE_ACTION_TRANSITIONS,
        INCIDENT_TRANSITIONS,
        NEAR_MISS_TRANSITIONS,
        InvalidStateTransition,
        allowed_states,
        allowed_transitions,
        transition_corrective_action,
        transition_history,
        transition_incident,
        transition_near_miss,
    )
    from backend.app.services.workflow_engine import (
        ALLOWED_TRANSITIONS,
        WorkflowPreconditionError,
        acknowledge_event,
        assign_event,
    )


router = APIRouter()
RESPONSE_PAGE_SIZE = 100
RESPONSE_MAX_PAGE_SIZE = 500


def client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def get_event_or_404(db: Session, event_id: str) -> Event:
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def incident_payload(incident: Incident) -> dict:
    return {
        "id": incident.id,
        "event_id": incident.event_id,
        "title": incident.title,
        "description": incident.description,
        "severity": incident.severity,
        "status": incident.status,
        "allowed_transitions": allowed_transitions(INCIDENT_TRANSITIONS, incident.status),
        "reported_by_user_id": incident.reported_by_user_id,
        "created_at": incident.created_at,
        "updated_at": incident.updated_at,
        "corrective_action_count": len(incident.corrective_actions),
    }


def near_miss_payload(near_miss: NearMiss) -> dict:
    return {
        "id": near_miss.id,
        "event_id": near_miss.event_id,
        "title": near_miss.title,
        "description": near_miss.description,
        "potential_severity": near_miss.potential_severity,
        "interaction_type": near_miss.interaction_type,
        "status": near_miss.status,
        "allowed_transitions": allowed_transitions(NEAR_MISS_TRANSITIONS, near_miss.status),
        "created_at": near_miss.created_at,
        "updated_at": near_miss.updated_at,
        "closed_at": near_miss.closed_at,
        "corrective_action_count": len(near_miss.corrective_actions),
    }


def corrective_action_payload(action: CorrectiveAction) -> dict:
    return {
        "id": action.id,
        "event_id": action.event_id,
        "incident_id": action.incident_id,
        "near_miss_id": action.near_miss_id,
        "action_description": action.action_description,
        "assigned_to_user_id": action.assigned_to_user_id,
        "status": action.status,
        "allowed_transitions": allowed_transitions(CORRECTIVE_ACTION_TRANSITIONS, action.status),
        "due_date": action.due_date,
        "completed_at": action.completed_at,
        "created_at": action.created_at,
    }


# ============================================================================
# DECLARED LIFECYCLES
# ============================================================================

@router.get("/lifecycle-states", response_model=ResponseLifecycleStatesOut)
def get_lifecycle_states(actor: Optional[User] = Depends(require_permission("events:view"))):
    """Return the declared state machines for every response entity."""
    return {
        "incident": allowed_states(INCIDENT_TRANSITIONS),
        "near_miss": allowed_states(NEAR_MISS_TRANSITIONS),
        "corrective_action": allowed_states(CORRECTIVE_ACTION_TRANSITIONS),
        "event": allowed_states(ALLOWED_TRANSITIONS),
    }


# ============================================================================
# ACKNOWLEDGEMENT
# ============================================================================

@router.get("/events/{event_id}/acknowledgements", response_model=list[AcknowledgementOut])
def list_acknowledgements(
    event_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    get_event_or_404(db, event_id)
    return (
        db.query(Acknowledgement)
        .filter(Acknowledgement.event_id == event_id)
        .order_by(Acknowledgement.acknowledged_at.desc())
        .all()
    )


@router.post("/events/{event_id}/acknowledgements", response_model=AcknowledgementOut, status_code=201)
def acknowledge_event_endpoint(
    event_id: str,
    payload: AcknowledgementCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:acknowledge")),
):
    """Acknowledge an event. Records the acknowledgement and the state change
    together; neither can be written without the other."""
    event = get_event_or_404(db, event_id)
    previous_state = event.workflow_state
    try:
        acknowledgement = acknowledge_event(
            db,
            event,
            actor_id=actor.id if actor else None,
            notes=payload.notes,
        )
    except WorkflowPreconditionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="EVENT_ACKNOWLEDGED",
        resource_type="EVENT",
        resource_id=event.id,
        details_json={
            "acknowledgement_id": acknowledgement.id,
            "previous_state": previous_state,
            "new_state": event.workflow_state,
        },
        ip_address=client_ip(request),
    )
    return acknowledgement


# ============================================================================
# ASSIGNMENT
# ============================================================================

@router.get("/events/{event_id}/assignments", response_model=list[AssignmentOut])
def list_assignments(
    event_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    get_event_or_404(db, event_id)
    return (
        db.query(Assignment)
        .filter(Assignment.event_id == event_id)
        .order_by(Assignment.assigned_at.desc())
        .all()
    )


@router.post("/events/{event_id}/assignments", response_model=AssignmentOut, status_code=201)
def assign_event_endpoint(
    event_id: str,
    payload: AssignmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:assign")),
):
    """Assign an acknowledged event to an active user."""
    event = get_event_or_404(db, event_id)
    assignee = db.query(User).filter(User.id == payload.assigned_to_user_id).first()
    if assignee is None:
        raise HTTPException(status_code=404, detail="Assignee not found")
    try:
        assignment = assign_event(
            db, event, assignee=assignee, assigner=actor, due_at=payload.due_at, notes=payload.notes
        )
    except WorkflowPreconditionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="EVENT_ASSIGNED",
        resource_type="EVENT",
        resource_id=event.id,
        details_json={
            "assignment_id": assignment.id,
            "assigned_to_user_id": assignee.id,
            "new_state": event.workflow_state,
        },
        ip_address=client_ip(request),
    )
    return assignment


# ============================================================================
# INCIDENTS
# ============================================================================

@router.get("/incidents", response_model=list[IncidentOut])
def list_incidents(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    event_id: Optional[str] = None,
    limit: int = Query(default=RESPONSE_PAGE_SIZE, ge=1, le=RESPONSE_MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    query = db.query(Incident)
    if status_filter:
        query = query.filter(Incident.status == status_filter)
    if event_id:
        query = query.filter(Incident.event_id == event_id)
    items = query.order_by(Incident.created_at.desc()).offset(offset).limit(limit).all()
    return [incident_payload(item) for item in items]


@router.post("/incidents", response_model=IncidentOut, status_code=201)
def create_incident(
    payload: IncidentCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("incidents:manage")),
):
    """Raise an incident against a persisted event.

    The event must already exist. This endpoint never creates an event, so an
    incident always references a real recorded observation.
    """
    get_event_or_404(db, payload.event_id)
    incident = Incident(
        event_id=payload.event_id,
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
        status="OPEN",
        reported_by_user_id=actor.id if actor else None,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="INCIDENT_RAISED",
        resource_type="INCIDENT",
        resource_id=incident.id,
        details_json={"event_id": incident.event_id, "severity": incident.severity, "status": incident.status},
        ip_address=client_ip(request),
    )
    return incident_payload(incident)


@router.get("/incidents/{incident_id}", response_model=IncidentOut)
def get_incident(
    incident_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident_payload(incident)


@router.post("/incidents/{incident_id}/transitions", response_model=IncidentOut)
def transition_incident_endpoint(
    incident_id: str,
    payload: TransitionRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("incidents:manage")),
):
    previous_state = db.query(Incident).filter(Incident.id == incident_id).first()
    if previous_state is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    before = previous_state.status
    try:
        incident = transition_incident(
            db, previous_state, payload.new_state, user_id=actor.id if actor else None, reason=payload.reason
        )
    except InvalidStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="INCIDENT_TRANSITIONED",
        resource_type="INCIDENT",
        resource_id=incident.id,
        details_json={"previous_state": before, "new_state": incident.status},
        ip_address=client_ip(request),
    )
    return incident_payload(incident)


@router.get("/incidents/{incident_id}/transitions", response_model=list[StateTransitionOut])
def list_incident_transitions(
    incident_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return transition_history(incident)


# ============================================================================
# NEAR MISSES
# ============================================================================

@router.get("/near-misses", response_model=list[NearMissOut])
def list_near_misses(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    event_id: Optional[str] = None,
    limit: int = Query(default=RESPONSE_PAGE_SIZE, ge=1, le=RESPONSE_MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    query = db.query(NearMiss)
    if status_filter:
        query = query.filter(NearMiss.status == status_filter)
    if event_id:
        query = query.filter(NearMiss.event_id == event_id)
    items = query.order_by(NearMiss.created_at.desc()).offset(offset).limit(limit).all()
    return [near_miss_payload(item) for item in items]


@router.post("/near-misses", response_model=NearMissOut, status_code=201)
def create_near_miss(
    payload: NearMissCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("incidents:manage")),
):
    """Raise a near-miss against a persisted event."""
    get_event_or_404(db, payload.event_id)
    near_miss = NearMiss(
        event_id=payload.event_id,
        title=payload.title,
        description=payload.description,
        potential_severity=payload.potential_severity,
        interaction_type=payload.interaction_type,
        status="REPORTED",
    )
    db.add(near_miss)
    db.commit()
    db.refresh(near_miss)
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="NEAR_MISS_REPORTED",
        resource_type="NEAR_MISS",
        resource_id=near_miss.id,
        details_json={"event_id": near_miss.event_id, "status": near_miss.status},
        ip_address=client_ip(request),
    )
    return near_miss_payload(near_miss)


@router.post("/near-misses/{near_miss_id}/transitions", response_model=NearMissOut)
def transition_near_miss_endpoint(
    near_miss_id: str,
    payload: TransitionRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("incidents:manage")),
):
    record = db.query(NearMiss).filter(NearMiss.id == near_miss_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Near miss not found")
    before = record.status
    try:
        near_miss = transition_near_miss(
            db, record, payload.new_state, user_id=actor.id if actor else None, reason=payload.reason
        )
    except InvalidStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="NEAR_MISS_TRANSITIONED",
        resource_type="NEAR_MISS",
        resource_id=near_miss.id,
        details_json={"previous_state": before, "new_state": near_miss.status},
        ip_address=client_ip(request),
    )
    return near_miss_payload(near_miss)


@router.get("/near-misses/{near_miss_id}/transitions", response_model=list[StateTransitionOut])
def list_near_miss_transitions(
    near_miss_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    record = db.query(NearMiss).filter(NearMiss.id == near_miss_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Near miss not found")
    return transition_history(record)


# ============================================================================
# CORRECTIVE ACTIONS
# ============================================================================

@router.get("/corrective-actions", response_model=list[CorrectiveActionOut])
def list_corrective_actions(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    assigned_to_user_id: Optional[str] = None,
    limit: int = Query(default=RESPONSE_PAGE_SIZE, ge=1, le=RESPONSE_MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    query = db.query(CorrectiveAction)
    if status_filter:
        query = query.filter(CorrectiveAction.status == status_filter)
    if assigned_to_user_id:
        query = query.filter(CorrectiveAction.assigned_to_user_id == assigned_to_user_id)
    items = query.order_by(CorrectiveAction.created_at.desc()).offset(offset).limit(limit).all()
    return [corrective_action_payload(item) for item in items]


@router.post("/corrective-actions", response_model=CorrectiveActionOut, status_code=201)
def create_corrective_action(
    payload: CorrectiveActionCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("corrective_actions:manage")),
):
    """Create a corrective action against exactly one existing record."""
    try:
        parent_field, parent_id = payload.validated_parent()
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    parent_model = {
        "event_id": Event,
        "incident_id": Incident,
        "near_miss_id": NearMiss,
    }[parent_field]
    if db.query(parent_model.id).filter(parent_model.id == parent_id).first() is None:
        raise HTTPException(status_code=404, detail=f"{parent_field} parent record not found")

    if payload.assigned_to_user_id:
        assignee = db.query(User).filter(
            User.id == payload.assigned_to_user_id, User.is_active.is_(True)
        ).first()
        if assignee is None:
            raise HTTPException(status_code=404, detail="Active assignee not found")

    action = CorrectiveAction(
        action_description=payload.action_description,
        assigned_to_user_id=payload.assigned_to_user_id,
        due_date=payload.due_date,
        status="PENDING",
        **{parent_field: parent_id},
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="CORRECTIVE_ACTION_CREATED",
        resource_type="CORRECTIVE_ACTION",
        resource_id=action.id,
        details_json={"parent": parent_field, "parent_id": parent_id, "status": action.status},
        ip_address=client_ip(request),
    )
    return corrective_action_payload(action)


@router.post("/corrective-actions/{action_id}/transitions", response_model=CorrectiveActionOut)
def transition_corrective_action_endpoint(
    action_id: str,
    payload: TransitionRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("corrective_actions:manage")),
):
    record = db.query(CorrectiveAction).filter(CorrectiveAction.id == action_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Corrective action not found")
    before = record.status
    try:
        action = transition_corrective_action(
            db, record, payload.new_state, user_id=actor.id if actor else None, reason=payload.reason
        )
    except InvalidStateTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="CORRECTIVE_ACTION_TRANSITIONED",
        resource_type="CORRECTIVE_ACTION",
        resource_id=action.id,
        details_json={"previous_state": before, "new_state": action.status},
        ip_address=client_ip(request),
    )
    return corrective_action_payload(action)


@router.get("/corrective-actions/{action_id}/transitions", response_model=list[StateTransitionOut])
def list_corrective_action_transitions(
    action_id: str,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    record = db.query(CorrectiveAction).filter(CorrectiveAction.id == action_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="Corrective action not found")
    return transition_history(record)

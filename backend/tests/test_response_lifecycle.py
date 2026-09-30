"""Tests for the safety response lifecycle.

These tests use fixture data only. Nothing here measures real plant behaviour:
every event, incident, and near-miss in this file is created by the test itself
from a fixture camera, and no detection is asserted.
"""
from pathlib import Path
import itertools
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import create_access_token, hash_password
from app.database import Base, get_db
from app.main import app, seed_default_roles
from app.models import (
    Acknowledgement,
    Assignment,
    AuditLog,
    Camera,
    CorrectiveAction,
    CorrectiveActionStateTransition,
    Event,
    Incident,
    IncidentStateTransition,
    NearMiss,
    NearMissStateTransition,
    Role,
    User,
)
from app.services.response_engine import (
    InvalidStateTransition,
    transition_corrective_action,
    transition_incident,
    transition_near_miss,
)
from app.services.workflow_engine import (
    WorkflowPreconditionError,
    acknowledge_event,
    assign_event,
)
from fastapi.testclient import TestClient


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


_CAMERA_SEQUENCE = itertools.count(1)


def create_event(db, workflow_state="NEW"):
    camera = Camera(
        name="fixture camera",
        code=f"FIX-CAM-{next(_CAMERA_SEQUENCE):04d}",
        stream_url="test-only-source",
    )
    db.add(camera)
    db.flush()
    event = Event(camera_id=camera.id, event_type="TEST_ONLY", confidence=0.5, workflow_state=workflow_state)
    db.add(event)
    db.commit()
    return event


def create_user(db, username="response_test_admin", role_name="ADMIN"):
    role = db.query(Role).filter_by(name=role_name).one()
    user = User(
        username=username,
        email=f"{username}@example.test",
        full_name=username.replace("_", " ").title(),
        hashed_password=hash_password("test-only-password"),
        role_id=role.id,
    )
    db.add(user)
    db.commit()
    return user


def create_incident(db, event=None):
    event = event or create_event(db)
    incident = Incident(
        event_id=event.id,
        title="Fixture incident",
        description="Raised by the test suite",
        severity="HIGH",
        status="OPEN",
    )
    db.add(incident)
    db.commit()
    return incident


def create_near_miss(db, event=None):
    event = event or create_event(db)
    near_miss = NearMiss(
        event_id=event.id,
        title="Fixture near miss",
        description="Raised by the test suite",
        potential_severity="HIGH",
        status="REPORTED",
    )
    db.add(near_miss)
    db.commit()
    return near_miss


def create_action(db, incident=None):
    incident = incident or create_incident(db)
    action = CorrectiveAction(
        incident_id=incident.id,
        action_description="Fixture corrective action",
        status="PENDING",
    )
    db.add(action)
    db.commit()
    return action


# ---------------------------------------------------------------------------
# INCIDENT LIFECYCLE
# ---------------------------------------------------------------------------

def test_incident_lifecycle_is_recorded_with_actor_and_reason(db_session):
    incident = create_incident(db_session)
    for state in ("INVESTIGATING", "RESOLVED", "CLOSED"):
        transition_incident(db_session, incident, state, user_id=None, reason=f"fixture step to {state}")

    history = db_session.query(IncidentStateTransition).filter_by(incident_id=incident.id).all()
    assert incident.status == "CLOSED"
    assert [item.new_state for item in history] == ["INVESTIGATING", "RESOLVED", "CLOSED"]
    assert history[0].previous_state == "OPEN"
    assert all(item.reason for item in history)


def test_incident_rejects_invalid_jump_blank_reason_and_closed_rewind(db_session):
    incident = create_incident(db_session)
    with pytest.raises(InvalidStateTransition, match="not allowed"):
        transition_incident(db_session, incident, "CLOSED", user_id=None, reason="skip the workflow")
    with pytest.raises(InvalidStateTransition, match="reason is required"):
        transition_incident(db_session, incident, "INVESTIGATING", user_id=None, reason="   ")

    transition_incident(db_session, incident, "INVESTIGATING", user_id=None, reason="fixture start")
    transition_incident(db_session, incident, "RESOLVED", user_id=None, reason="fixture resolved")
    transition_incident(db_session, incident, "CLOSED", user_id=None, reason="fixture closed")
    with pytest.raises(InvalidStateTransition, match="not allowed"):
        transition_incident(db_session, incident, "OPEN", user_id=None, reason="rewind a closed incident")

    assert incident.status == "CLOSED"
    assert db_session.query(IncidentStateTransition).count() == 3


def test_incident_rejects_unknown_and_self_transitions(db_session):
    incident = create_incident(db_session)
    with pytest.raises(InvalidStateTransition, match="not a recognised state"):
        transition_incident(db_session, incident, "ESCALATED_TO_MARS", user_id=None, reason="unknown state")
    with pytest.raises(InvalidStateTransition, match="already in state"):
        transition_incident(db_session, incident, "OPEN", user_id=None, reason="no-op transition")
    assert db_session.query(IncidentStateTransition).count() == 0


# ---------------------------------------------------------------------------
# NEAR-MISS LIFECYCLE
# ---------------------------------------------------------------------------

def test_near_miss_confirmation_sets_closed_at_only_on_close(db_session):
    near_miss = create_near_miss(db_session)
    transition_near_miss(db_session, near_miss, "UNDER_REVIEW", user_id=None, reason="fixture triage")
    assert near_miss.closed_at is None
    transition_near_miss(db_session, near_miss, "CONFIRMED", user_id=None, reason="fixture confirmed")
    assert near_miss.closed_at is None
    transition_near_miss(db_session, near_miss, "CLOSED", user_id=None, reason="fixture closed")
    assert near_miss.closed_at is not None
    assert near_miss.status == "CLOSED"


def test_near_miss_rejects_skipping_review_and_dismiss_is_terminal(db_session):
    near_miss = create_near_miss(db_session)
    with pytest.raises(InvalidStateTransition, match="not allowed"):
        transition_near_miss(db_session, near_miss, "CLOSED", user_id=None, reason="skip review")
    transition_near_miss(db_session, near_miss, "DISMISSED", user_id=None, reason="fixture dismissed")
    with pytest.raises(InvalidStateTransition, match="not allowed"):
        transition_near_miss(db_session, near_miss, "UNDER_REVIEW", user_id=None, reason="reopen a dismissal")
    assert near_miss.status == "DISMISSED"


# ---------------------------------------------------------------------------
# CORRECTIVE ACTION LIFECYCLE
# ---------------------------------------------------------------------------

def test_corrective_action_verification_stamps_completion_time(db_session):
    action = create_action(db_session)
    transition_corrective_action(db_session, action, "IN_PROGRESS", user_id=None, reason="fixture started")
    assert action.completed_at is None
    transition_corrective_action(db_session, action, "VERIFIED", user_id=None, reason="fixture verified")
    assert action.completed_at is not None
    history = db_session.query(CorrectiveActionStateTransition).filter_by(corrective_action_id=action.id).all()
    assert [item.new_state for item in history] == ["IN_PROGRESS", "VERIFIED"]


def test_corrective_action_rejects_skipping_in_progress(db_session):
    action = create_action(db_session)
    with pytest.raises(InvalidStateTransition, match="not allowed"):
        transition_corrective_action(db_session, action, "CLOSED", user_id=None, reason="skip verification")
    assert action.status == "PENDING"
    assert db_session.query(CorrectiveActionStateTransition).count() == 0


# ---------------------------------------------------------------------------
# ACKNOWLEDGEMENT AND ASSIGNMENT PRECONDITIONS
# ---------------------------------------------------------------------------

def test_acknowledgement_requires_awaiting_state_and_advances_workflow(db_session):
    seed_default_roles(db_session)
    user = create_user(db_session)
    event = create_event(db_session, workflow_state="UNACKNOWLEDGED")
    acknowledgement = acknowledge_event(db_session, event, user=user, notes="fixture acknowledgement")

    assert acknowledgement.user_id == user.id
    assert event.workflow_state == "ACKNOWLEDGED"
    assert db_session.query(Acknowledgement).filter_by(event_id=event.id).count() == 1

    with pytest.raises(WorkflowPreconditionError, match="cannot be acknowledged"):
        acknowledge_event(db_session, event, user=user, notes="second acknowledgement")
    assert db_session.query(Acknowledgement).filter_by(event_id=event.id).count() == 1


def test_acknowledgement_rejects_an_already_investigating_event(db_session):
    seed_default_roles(db_session)
    user = create_user(db_session)
    event = create_event(db_session, workflow_state="UNDER_INVESTIGATION")
    with pytest.raises(WorkflowPreconditionError, match="cannot be acknowledged"):
        acknowledge_event(db_session, event, user=user)
    assert db_session.query(Acknowledgement).count() == 0


def test_assignment_requires_acknowledgement_and_an_active_assignee(db_session):
    seed_default_roles(db_session)
    officer = create_user(db_session, "response_test_officer", "SAFETY_OFFICER")
    inactive = create_user(db_session, "response_test_inactive", "OPERATOR")
    inactive.is_active = False
    db_session.commit()

    event = create_event(db_session, workflow_state="NEW")
    with pytest.raises(WorkflowPreconditionError, match="cannot be assigned"):
        assign_event(db_session, event, assignee=officer, assigner=officer)

    event.workflow_state = "ACKNOWLEDGED"
    db_session.commit()
    with pytest.raises(WorkflowPreconditionError, match="inactive user"):
        assign_event(db_session, event, assignee=inactive, assigner=officer)
    assert db_session.query(Assignment).count() == 0

    assignment = assign_event(db_session, event, assignee=officer, assigner=officer)
    assert event.workflow_state == "ASSIGNED"
    assert assignment.assigned_to_user_id == officer.id
    assert assignment.assigned_by_user_id == officer.id


# ---------------------------------------------------------------------------
# API SURFACE
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db_session):
    seed_default_roles(db_session)
    admin = create_user(db_session)
    token = create_access_token({"sub": admin.username, "user_id": admin.id, "role": "ADMIN"})

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {token}"})
        yield test_client
    app.dependency_overrides.clear()


def test_lifecycle_states_are_published(client):
    response = client.get("/api/v1/lifecycle-states")
    assert response.status_code == 200
    body = response.json()
    assert body["incident"] == ["CLOSED", "INVESTIGATING", "OPEN", "RESOLVED"]
    assert "CONFIRMED" in body["near_miss"]
    assert body["corrective_action"] == ["CLOSED", "IN_PROGRESS", "PENDING", "VERIFIED"]
    assert set(body["event"]) == {
        "NEW", "UNACKNOWLEDGED", "ACKNOWLEDGED", "ASSIGNED",
        "UNDER_INVESTIGATION", "ACTION_REQUIRED", "RESOLVED", "CLOSED",
    }


def test_incident_api_requires_an_existing_event(client, db_session):
    orphan = client.post(
        "/api/v1/incidents",
        json={"event_id": "00000000-0000-0000-0000-000000000000", "title": "Orphan incident"},
    )
    assert orphan.status_code == 404

    event = create_event(db_session)
    created = client.post(
        "/api/v1/incidents",
        json={"event_id": event.id, "title": "Operator raised incident", "severity": "CRITICAL"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "OPEN"
    assert body["allowed_transitions"] == ["INVESTIGATING", "RESOLVED"]

    moved = client.post(
        f"/api/v1/incidents/{body['id']}/transitions",
        json={"new_state": "RESOLVED", "reason": "fixture resolved by operator"},
    )
    assert moved.status_code == 200
    assert moved.json()["status"] == "RESOLVED"

    history = client.get(f"/api/v1/incidents/{body['id']}/transitions")
    assert history.status_code == 200
    assert history.json()[0]["previous_state"] == "OPEN"
    audit = db_session.query(AuditLog).filter_by(action="INCIDENT_TRANSITIONED").one()
    assert audit.details_json == {"previous_state": "OPEN", "new_state": "RESOLVED"}


def test_api_transition_requires_a_reason(client, db_session):
    incident = create_incident(db_session)
    response = client.post(
        f"/api/v1/incidents/{incident.id}/transitions",
        json={"new_state": "INVESTIGATING"},
    )
    assert response.status_code == 422
    assert incident.status == "OPEN"


def test_api_transition_rejects_an_illegal_jump_with_conflict(client, db_session):
    incident = create_incident(db_session)
    response = client.post(
        f"/api/v1/incidents/{incident.id}/transitions",
        json={"new_state": "CLOSED", "reason": "fixture illegal jump"},
    )
    assert response.status_code == 409
    assert incident.status == "OPEN"


def test_corrective_action_requires_exactly_one_existing_parent(client, db_session):
    event = create_event(db_session)
    neither = client.post(
        "/api/v1/corrective-actions",
        json={"action_description": "Fixture action with no parent"},
    )
    assert neither.status_code == 422

    both = client.post(
        "/api/v1/corrective-actions",
        json={
            "action_description": "Fixture action with two parents",
            "event_id": event.id,
            "incident_id": create_incident(db_session).id,
        },
    )
    assert both.status_code == 422

    missing_parent = client.post(
        "/api/v1/corrective-actions",
        json={
            "action_description": "Fixture action with a missing parent",
            "near_miss_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert missing_parent.status_code == 404

    accepted = client.post(
        "/api/v1/corrective-actions",
        json={"action_description": "Fixture action against the event", "event_id": event.id},
    )
    assert accepted.status_code == 201
    assert accepted.json()["status"] == "PENDING"
    assert accepted.json()["allowed_transitions"] == ["IN_PROGRESS", "VERIFIED"]


def test_response_listings_are_paginated_and_filtered(client, db_session):
    event = create_event(db_session)
    for index in range(3):
        client.post(
            "/api/v1/near-misses",
            json={"event_id": event.id, "title": f"Fixture near miss {index}"},
        )
    page = client.get("/api/v1/near-misses", params={"limit": 2, "offset": 0})
    assert page.status_code == 200
    assert len(page.json()) == 2

    filtered = client.get("/api/v1/near-misses", params={"status": "CONFIRMED"})
    assert filtered.json() == []

    bounded = client.get("/api/v1/near-misses", params={"limit": 100000})
    assert bounded.status_code == 422


def test_acknowledgement_endpoint_records_audit_and_rejects_repeat(client, db_session):
    event = create_event(db_session, workflow_state="UNACKNOWLEDGED")
    first = client.post(
        f"/api/v1/events/{event.id}/acknowledgements",
        json={"notes": "fixture acknowledgement via API"},
    )
    assert first.status_code == 201
    assert first.json()["event_id"] == event.id
    assert event.workflow_state == "ACKNOWLEDGED"

    repeat = client.post(f"/api/v1/events/{event.id}/acknowledgements", json={"notes": "again"})
    assert repeat.status_code == 409

    audit = db_session.query(AuditLog).filter_by(action="EVENT_ACKNOWLEDGED").one()
    assert audit.details_json["previous_state"] == "UNACKNOWLEDGED"
    assert audit.details_json["new_state"] == "ACKNOWLEDGED"


def test_assignment_endpoint_records_audit_and_named_assignee(client, db_session):
    assignee = create_user(db_session, "response_test_assignee", "OPERATOR")
    event = create_event(db_session, workflow_state="ACKNOWLEDGED")
    response = client.post(
        f"/api/v1/events/{event.id}/assignments",
        json={"assigned_to_user_id": assignee.id, "notes": "fixture assignment"},
    )
    assert response.status_code == 201
    assert response.json()["assigned_to_user_id"] == assignee.id
    assert event.workflow_state == "ASSIGNED"

    missing = client.post(
        f"/api/v1/events/{event.id}/assignments",
        json={"assigned_to_user_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert missing.status_code == 404

    audit = db_session.query(AuditLog).filter_by(action="EVENT_ASSIGNED").one()
    assert audit.details_json["assigned_to_user_id"] == assignee.id
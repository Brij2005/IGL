"""Event and incident lifecycle: escalation, withdrawal, and incident linkage.

Every event and incident in this file is created by the test itself from a
fixture camera. Nothing here measures real plant behaviour and nothing here
asserts that a safety condition occurred: the file covers the state machines and
the read model, using records the test authored.

The transitions that matter most are the ones that close an event out. A
cancelled event was withdrawn as false or no longer applicable, so it must carry
a reason, must be terminal, and must never be picked up again as an open event.
"""
import itertools
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base, get_db  # noqa: E402
from app.main import app, seed_reference_roles  # noqa: E402
from app.models import (  # noqa: E402
    Camera,
    Event,
    EventStateTransition,
    Incident,
    IncidentStateTransition,
    Role,
    User,
)
from app.services.response_engine import (  # noqa: E402
    INCIDENT_TRANSITIONS,
    InvalidStateTransition,
    transition_incident,
)
from app.services.workflow_engine import (  # noqa: E402
    ACKNOWLEDGABLE_STATES,
    ALLOWED_TRANSITIONS,
    InvalidWorkflowTransition,
    WorkflowPreconditionError,
    acknowledge_event,
    allowed_transitions,
    assign_event,
    transition_event,
)

NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
_CAMERA_SEQUENCE = itertools.count(1)


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


def create_event(db, workflow_state="NEW", **overrides):
    camera = Camera(
        name="fixture camera",
        code=f"IL-CAM-{next(_CAMERA_SEQUENCE):04d}",
        stream_url="test-only-source",
    )
    db.add(camera)
    db.flush()
    payload = {
        "camera_id": camera.id,
        "event_type": "TEST_ONLY",
        "observation_state": "POSSIBLE",
        "severity": "HIGH",
        "workflow_state": workflow_state,
        "started_at": NOW,
    }
    payload.update(overrides)
    event = Event(**payload)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def create_incident(db, event=None, status="OPEN"):
    event = event or create_event(db)
    incident = Incident(
        event_id=event.id,
        title="Fixture incident",
        description="Raised by the test suite",
        severity="HIGH",
        status=status,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


@pytest.fixture
def client(db_session):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Declared transition maps
# ---------------------------------------------------------------------------

def test_escalated_and_cancelled_are_declared_states():
    assert "ESCALATED" in ALLOWED_TRANSITIONS
    assert "CANCELLED" in ALLOWED_TRANSITIONS
    assert "ESCALATED" in ALLOWED_TRANSITIONS["UNACKNOWLEDGED"]
    assert "ESCALATED" in ALLOWED_TRANSITIONS["ACKNOWLEDGED"]
    for cancellable in (
        "NEW",
        "UNACKNOWLEDGED",
        "ACKNOWLEDGED",
        "ASSIGNED",
        "UNDER_INVESTIGATION",
        "ACTION_REQUIRED",
    ):
        assert "CANCELLED" in ALLOWED_TRANSITIONS[cancellable], cancellable


def test_cancelled_is_terminal_on_events_and_incidents():
    assert ALLOWED_TRANSITIONS["CANCELLED"] == set()
    assert INCIDENT_TRANSITIONS["CANCELLED"] == set()
    assert INCIDENT_TRANSITIONS["CLOSED"] == set()


def test_states_that_cannot_be_cancelled_stay_that_way():
    """Withdrawal is only defined before the event was resolved or closed."""
    assert "CANCELLED" not in ALLOWED_TRANSITIONS["RESOLVED"]
    assert "CANCELLED" not in ALLOWED_TRANSITIONS["CLOSED"]
    assert "CANCELLED" not in INCIDENT_TRANSITIONS["RESOLVED"]


def test_acknowledgement_stay_consistent_with_the_transition_map():
    """The acknowledgement shortcut must not reach a state the map forbids.

    NEW is the one state the shortcut is for: it moves straight to ACKNOWLEDGED
    in the same transaction as the acknowledgement row, which is why the map has
    no NEW -> ACKNOWLEDGED edge for a bare transition to use.
    """
    assert ACKNOWLEDGABLE_STATES == {"NEW", "UNACKNOWLEDGED", "ESCALATED"}
    for state in ("UNACKNOWLEDGED", "ESCALATED"):
        assert "ACKNOWLEDGED" in ALLOWED_TRANSITIONS[state], state
    assert "ACKNOWLEDGED" not in ALLOWED_TRANSITIONS["NEW"]
    # A withdrawn event cannot be acknowledged.
    assert "CANCELLED" not in ACKNOWLEDGABLE_STATES
    assert "ACKNOWLEDGED" not in ALLOWED_TRANSITIONS["CANCELLED"]


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------

def test_event_escalates_from_unacknowledged_and_stays_workable(db_session):
    event = create_event(db_session, workflow_state="UNACKNOWLEDGED")
    transition_event(db_session, event, "ESCALATED", user_id=None, reason="Fixture: no acknowledgement inside the SLA")
    assert event.workflow_state == "ESCALATED"
    # Escalation is not a dead end: the event can still be handled.
    assert "ACKNOWLEDGED" in allowed_transitions(event.workflow_state)
    transition_event(db_session, event, "ACKNOWLEDGED", user_id=None, reason="Fixture: officer picked it up")
    assert event.workflow_state == "ACKNOWLEDGED"


def test_escalation_is_recorded_with_actor_state_and_time(db_session):
    event = create_event(db_session, workflow_state="ACKNOWLEDGED")
    transition_event(db_session, event, "ESCALATED", user_id=None, reason="Fixture: escalated for review")
    row = db_session.query(EventStateTransition).filter_by(event_id=event.id).one()
    assert row.previous_state == "ACKNOWLEDGED"
    assert row.new_state == "ESCALATED"
    assert row.reason == "Fixture: escalated for review"
    # Nothing in this build authenticates a request, so the actor is recorded as
    # unknown rather than credited to a seeded user.
    assert row.user_id is None
    assert row.transitioned_at is not None


def test_escalation_outside_the_allowed_states_is_rejected(db_session):
    event = create_event(db_session, workflow_state="NEW")
    with pytest.raises(InvalidWorkflowTransition, match="not allowed"):
        transition_event(db_session, event, "ESCALATED", user_id=None, reason="Fixture: skipped the unacknowledged state")
    assert event.workflow_state == "NEW"
    assert db_session.query(EventStateTransition).count() == 0


def test_escalated_event_can_be_acknowledged_and_assigned(db_session):
    from app.models import Role, User

    seed_reference_roles(db_session)
    officer_role = db_session.query(Role).filter_by(name="SAFETY_OFFICER").one()
    officer = User(username="il_officer", email="il-officer@example.test", full_name="Incident Lifecycle Officer", role_id=officer_role.id)
    db_session.add(officer)
    db_session.commit()

    event = create_event(db_session, workflow_state="ESCALATED")
    acknowledgement = acknowledge_event(db_session, event, actor_id=officer.id, notes="Fixture acknowledgement")
    assert acknowledgement.id is not None
    assert event.workflow_state == "ACKNOWLEDGED"

    assignment = assign_event(db_session, event, assignee=officer, assigner=officer, notes="Fixture assignment")
    assert assignment.id is not None
    assert event.workflow_state == "ASSIGNED"


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_cancelling_an_event_closes_it_out_with_a_reason_and_end_time(db_session):
    event = create_event(db_session, workflow_state="UNDER_INVESTIGATION")
    transition_event(db_session, event, "CANCELLED", user_id=None, reason="Fixture: false trigger withdrawn")
    assert event.workflow_state == "CANCELLED"
    assert event.ended_at is not None
    row = db_session.query(EventStateTransition).filter_by(event_id=event.id).one()
    assert (row.previous_state, row.new_state) == ("UNDER_INVESTIGATION", "CANCELLED")
    assert row.reason == "Fixture: false trigger withdrawn"
    assert row.transitioned_at is not None


def test_cancelling_requires_a_reason(db_session):
    event = create_event(db_session, workflow_state="NEW")
    with pytest.raises(InvalidWorkflowTransition, match="reason is required"):
        transition_event(db_session, event, "CANCELLED", user_id=None, reason="   ")
    assert event.workflow_state == "NEW"
    assert db_session.query(EventStateTransition).count() == 0


def test_cancelled_event_cannot_be_moved_again(db_session):
    event = create_event(db_session, workflow_state="NEW")
    transition_event(db_session, event, "CANCELLED", user_id=None, reason="Fixture: withdrawn")
    for state in ("UNACKNOWLEDGED", "ESCALATED", "RESOLVED", "CLOSED"):
        with pytest.raises(InvalidWorkflowTransition, match="not allowed"):
            transition_event(db_session, event, state, user_id=None, reason="Fixture: must stay withdrawn")
    assert event.workflow_state == "CANCELLED"


def test_cancelled_event_cannot_be_acknowledged_or_assigned(db_session):
    seed_reference_roles(db_session)
    admin_role = db_session.query(Role).filter_by(name="ADMIN").one()
    admin = User(username="il_admin", email="il-admin@example.test", full_name="Incident Lifecycle Admin", role_id=admin_role.id)
    db_session.add(admin)
    db_session.commit()

    event = create_event(db_session, workflow_state="NEW")
    transition_event(db_session, event, "CANCELLED", user_id=admin.id, reason="Fixture: withdrawn by operator")
    with pytest.raises(WorkflowPreconditionError):
        acknowledge_event(db_session, event, actor_id=admin.id, notes="Fixture: too late")
    with pytest.raises(WorkflowPreconditionError):
        assign_event(db_session, event, assignee=admin, assigner=admin)
    assert event.workflow_state == "CANCELLED"


# ---------------------------------------------------------------------------
# Incident escalation and cancellation
# ---------------------------------------------------------------------------

def test_incident_escalates_then_resolves_and_closes(db_session):
    incident = create_incident(db_session)
    transition_incident(db_session, incident, "ESCALATED", user_id=None, reason="Fixture: escalated to the plant manager")
    assert incident.status == "ESCALATED"
    transition_incident(db_session, incident, "RESOLVED", user_id=None, reason="Fixture: cause established")
    transition_incident(db_session, incident, "CLOSED", user_id=None, reason="Fixture: closed out")
    assert incident.status == "CLOSED"
    history = db_session.query(IncidentStateTransition).filter_by(incident_id=incident.id).all()
    assert [(row.previous_state, row.new_state) for row in history] == [
        ("OPEN", "ESCALATED"),
        ("ESCALATED", "RESOLVED"),
        ("RESOLVED", "CLOSED"),
    ]
    assert all(row.reason for row in history)


def test_incident_cancellation_is_terminal_and_requires_a_reason(db_session):
    incident = create_incident(db_session)
    transition_incident(db_session, incident, "CANCELLED", user_id=None, reason="Fixture: raised in error")
    assert incident.status == "CANCELLED"
    with pytest.raises(InvalidStateTransition, match="reason is required"):
        transition_incident(db_session, incident, "OPEN", user_id=None, reason=" ")
    for state in ("OPEN", "INVESTIGATING", "RESOLVED", "CLOSED", "ESCALATED"):
        with pytest.raises(InvalidStateTransition):
            transition_incident(db_session, incident, state, user_id=None, reason="Fixture: must stay withdrawn")
    assert incident.status == "CANCELLED"


def test_incident_illegal_jump_is_still_rejected(db_session):
    incident = create_incident(db_session)
    with pytest.raises(InvalidStateTransition, match="not allowed"):
        transition_incident(db_session, incident, "CLOSED", user_id=None, reason="Fixture: skipped the states")
    with pytest.raises(InvalidStateTransition, match="not a recognised state"):
        transition_incident(db_session, incident, "WITHDRAWN", user_id=None, reason="Fixture: unknown state")
    assert incident.status == "OPEN"
    assert db_session.query(IncidentStateTransition).count() == 0


# ---------------------------------------------------------------------------
# Event read model: incident linkage
# ---------------------------------------------------------------------------

def test_event_read_exposes_incident_ids_from_the_incident_rows(client, db_session):
    event = create_event(db_session)
    first = create_incident(db_session, event=event, status="OPEN")
    second = create_incident(db_session, event=event, status="OPEN")

    response = client.get(f"/api/v1/events/{event.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["incident_ids"] == [first.id, second.id]
    assert body["id"] == event.id


def test_event_without_an_incident_reports_an_empty_list(client, db_session):
    """No incident raised is a real answer, not missing data."""
    event = create_event(db_session)
    response = client.get(f"/api/v1/events/{event.id}")
    assert response.status_code == 200
    assert response.json()["incident_ids"] == []


def test_event_list_exposes_incident_ids_and_provenance(client, db_session):
    event = create_event(
        db_session,
        detector_key="FIRE",
        verification_state="VERIFIED",
        model_name="fixture-model",
        model_weights_checksum="0" * 64,
        threshold_source="CONFIGURED",
        source_reference="IGL-SOP-FIRE-001",
        temporal_observations=3,
        temporal_duration_seconds=2.0,
        provenance_json={"frame_timestamp": NOW.isoformat(), "frame_size": [480, 640]},
    )
    incident = create_incident(db_session, event=event)
    create_event(db_session)  # a second event with no incident on this page

    response = client.get("/api/v1/events")
    assert response.status_code == 200
    by_id = {row["id"]: row for row in response.json()}
    linked = by_id[event.id]
    assert linked["incident_ids"] == [incident.id]
    assert linked["detector_key"] == "FIRE"
    assert linked["verification_state"] == "VERIFIED"
    assert linked["model_name"] == "fixture-model"
    assert linked["model_weights_checksum"] == "0" * 64
    assert linked["threshold_source"] == "CONFIGURED"
    assert linked["source_reference"] == "IGL-SOP-FIRE-001"
    assert linked["temporal_observations"] == 3
    assert datetime.fromisoformat(linked["frame_timestamp"]) == NOW
    assert linked["allowed_transitions"] == sorted(ALLOWED_TRANSITIONS["NEW"])
    # The other event on the page has no incident and is reported as such.
    other = next(row for row in response.json() if row["id"] != event.id)
    assert other["incident_ids"] == []


def test_event_provenance_absent_values_are_null_not_invented(client, db_session):
    event = create_event(db_session)
    body = client.get(f"/api/v1/events/{event.id}").json()
    for field in (
        "detector_key",
        "verification_state",
        "model_name",
        "model_weights_checksum",
        "source_reference",
        "track_uuid",
    ):
        assert body[field] is None
    assert body["temporal_observations"] is None
    assert body["evidence_count"] == 0
    # With no recorded frame timestamp the event's start time is reported, which
    # is the frame timestamp it was opened from.
    assert datetime.fromisoformat(body["frame_timestamp"]) == event.started_at


def test_event_read_of_an_unknown_id_is_not_found(client, db_session):
    response = client.get("/api/v1/events/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# API transitions
# ---------------------------------------------------------------------------

def test_api_cancels_an_event_and_audits_it(client, db_session):
    event = create_event(db_session, workflow_state="NEW")
    response = client.post(
        f"/api/v1/events/{event.id}/transitions",
        json={"new_state": "CANCELLED", "reason": "Fixture: withdrawn as a false trigger"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_state"] == "CANCELLED"
    assert body["ended_at"] is not None
    assert body["allowed_transitions"] == []
    row = db_session.query(EventStateTransition).filter_by(event_id=event.id).one()
    assert (row.previous_state, row.new_state) == ("NEW", "CANCELLED")
    assert row.reason == "Fixture: withdrawn as a false trigger"


def test_api_rejects_an_invalid_cancellation_jump(client, db_session):
    event = create_event(db_session, workflow_state="RESOLVED")
    response = client.post(
        f"/api/v1/events/{event.id}/transitions",
        json={"new_state": "CANCELLED", "reason": "Fixture: cannot withdraw a resolved event"},
    )
    assert response.status_code == 409
    assert event.workflow_state == "RESOLVED"
    assert db_session.query(EventStateTransition).count() == 0


def test_api_requires_a_reason_for_a_cancellation(client, db_session):
    event = create_event(db_session, workflow_state="NEW")
    response = client.post(
        f"/api/v1/events/{event.id}/transitions",
        json={"new_state": "CANCELLED"},
    )
    assert response.status_code == 422
    assert event.workflow_state == "NEW"


def test_api_cancels_and_escalates_an_incident(client, db_session):
    incident = create_incident(db_session)
    escalated = client.post(
        f"/api/v1/incidents/{incident.id}/transitions",
        json={"new_state": "ESCALATED", "reason": "Fixture: escalated to the plant manager"},
    )
    assert escalated.status_code == 200
    assert escalated.json()["status"] == "ESCALATED"
    assert "CANCELLED" in escalated.json()["allowed_transitions"]

    cancelled = client.post(
        f"/api/v1/incidents/{incident.id}/transitions",
        json={"new_state": "CANCELLED", "reason": "Fixture: raised in error"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["allowed_transitions"] == []

    history = client.get(f"/api/v1/incidents/{incident.id}/transitions").json()
    assert [row["new_state"] for row in history] == ["ESCALATED", "CANCELLED"]
    assert all(row["reason"] for row in history)

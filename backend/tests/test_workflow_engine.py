from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base
from app.database import get_db
from app.models import AuditLog, Camera, Event, EventStateTransition, Role, User
from app.auth import create_access_token, hash_password
from app.main import app, seed_default_roles
from app.services.workflow_engine import InvalidWorkflowTransition, transition_event
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


def create_event(db):
    camera = Camera(name="test camera", code="TEST-CAM", stream_url="test-only-source")
    db.add(camera)
    db.flush()
    event = Event(camera_id=camera.id, event_type="TEST_ONLY", confidence=0.5, workflow_state="NEW")
    db.add(event)
    db.commit()
    return event


def test_event_workflow_accepts_and_audits_valid_transitions(db_session):
    event = create_event(db_session)
    for state in ("UNACKNOWLEDGED", "ACKNOWLEDGED", "ASSIGNED", "UNDER_INVESTIGATION", "ACTION_REQUIRED", "RESOLVED", "CLOSED"):
        transition_event(db_session, event, state, user_id=None, reason="test-only transition")
    history = db_session.query(EventStateTransition).filter_by(event_id=event.id).all()
    assert event.workflow_state == "CLOSED"
    assert len(history) == 7
    assert history[0].previous_state == "NEW"
    assert history[-1].new_state == "CLOSED"
    assert all(item.reason == "test-only transition" for item in history)


def test_event_workflow_rejects_invalid_jump_and_empty_reason(db_session):
    event = create_event(db_session)
    with pytest.raises(InvalidWorkflowTransition, match="not allowed"):
        transition_event(db_session, event, "CLOSED", user_id=None, reason="skip states")
    with pytest.raises(InvalidWorkflowTransition, match="reason is required"):
        transition_event(db_session, event, "UNACKNOWLEDGED", user_id=None, reason=" ")
    assert event.workflow_state == "NEW"
    assert db_session.query(EventStateTransition).count() == 0


def test_event_api_rejects_invalid_transition_and_audits_valid_transition(db_session):
    seed_default_roles(db_session)
    admin_role = db_session.query(Role).filter_by(name="ADMIN").one()
    admin = User(
        username="workflow_test_admin",
        email="workflow-admin@example.test",
        full_name="Workflow Test Admin",
        hashed_password=hash_password("test-only-password"),
        role_id=admin_role.id,
    )
    db_session.add(admin)
    db_session.commit()
    event = create_event(db_session)
    token = create_access_token({"sub": admin.username, "user_id": admin.id, "role": "ADMIN"})

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {token}"}
            invalid = client.post(
                f"/api/v1/events/{event.id}/transitions",
                json={"new_state": "CLOSED", "reason": "test-only invalid jump"},
                headers=headers,
            )
            assert invalid.status_code == 409
            valid = client.post(
                f"/api/v1/events/{event.id}/transitions",
                json={"new_state": "UNACKNOWLEDGED", "reason": "test-only valid transition"},
                headers=headers,
            )
            assert valid.status_code == 200
            assert valid.json()["workflow_state"] == "UNACKNOWLEDGED"
    finally:
        app.dependency_overrides.clear()

    transition = db_session.query(EventStateTransition).filter_by(event_id=event.id).one()
    audit = db_session.query(AuditLog).filter_by(action="EVENT_WORKFLOW_TRANSITIONED").one()
    assert transition.previous_state == "NEW"
    assert transition.user_id == admin.id
    assert audit.details_json == {"previous_state": "NEW", "new_state": "UNACKNOWLEDGED"}

from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.analytics import get_summary_analytics
from app.database import Base
from app.models import Camera, Event, Role, User
from app.services.notification_engine import enqueue_notification


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


def add_actual_record_fixtures(db):
    role = Role(name="TEST_OPERATOR")
    db.add(role)
    db.flush()
    user = User(
        username="test-operator",
        email="operator@example.test",
        full_name="Test Operator",
        hashed_password="test-only-hash",
        role_id=role.id,
    )
    camera = Camera(name="test camera", code="NOT-IGL-CAMERA", stream_url="test-only-source")
    db.add_all([user, camera])
    db.flush()
    event = Event(camera_id=camera.id, event_type="TEST_ONLY", confidence=0.5)
    db.add(event)
    db.commit()
    assert event.observation_state == "NOT_VALIDATED"
    return user, camera, event


def test_analytics_returns_database_empty_state(db_session):
    result = get_summary_analytics(db_session, None)
    assert result["data_status"] == "NO_DATA"
    assert result["camera_count"] == 0
    assert result["event_count"] == 0
    assert result["accuracy_metrics_status"] == "NOT_MEASURED"
    assert result["igl_validated"] is False


def test_analytics_counts_only_persisted_test_records(db_session):
    user, camera, event = add_actual_record_fixtures(db_session)
    result = get_summary_analytics(db_session, user)
    assert result["data_status"] == "AVAILABLE"
    assert result["camera_count"] == 1
    assert result["event_count"] == 1
    assert result["verified_event_count"] == 0
    assert result["incident_count"] == 0


def test_notification_status_does_not_claim_external_delivery(db_session):
    user, camera, event = add_actual_record_fixtures(db_session)
    dashboard = enqueue_notification(db_session, event_id=event.id, user_id=user.id)
    external = enqueue_notification(
        db_session,
        event_id=event.id,
        user_id=user.id,
        channel="EMAIL",
        recipient=user.email,
    )
    assert dashboard.status == "QUEUED"
    assert external.status == "NOT_CONFIGURED"
    assert "not configured" in external.error_message


def test_notification_requires_existing_event_and_active_user(db_session):
    user, _, _ = add_actual_record_fixtures(db_session)
    with pytest.raises(ValueError, match="existing event"):
        enqueue_notification(db_session, event_id="missing-event", user_id=user.id)
    user.is_active = False
    db_session.commit()
    with pytest.raises(ValueError, match="active user"):
        enqueue_notification(db_session, event_id=db_session.query(Event).first().id, user_id=user.id)

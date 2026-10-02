from pathlib import Path
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.analytics import get_summary_analytics
from app.database import Base
from app.models import Camera, Event, Role, User
from app.schemas_system import NotificationOut
from app.services.escalation_engine import acknowledge_escalation
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
    # No SMTP transport exists in this test deployment, so the row must say so
    # instead of claiming a delivery that was never attempted.
    assert external.status == "NOT_CONFIGURED"
    # The row has to say the transport is unconfigured, and it must not claim a
    # delivery happened.
    message = (external.error_message or "").lower()
    assert "configured" in message
    assert "no " in message
    assert external.sent_at is None


def test_notification_requires_existing_event_and_active_user(db_session):
    user, _, _ = add_actual_record_fixtures(db_session)
    with pytest.raises(ValueError, match="existing event"):
        enqueue_notification(db_session, event_id="missing-event", user_id=user.id)
    user.is_active = False
    db_session.commit()
    with pytest.raises(ValueError, match="active identity"):
        enqueue_notification(db_session, event_id=db_session.query(Event).first().id, user_id=user.id)


def test_role_addressed_notification_response_allows_null_user_id():
    response = NotificationOut.model_validate({
        "id": "notification-fixture",
        "event_id": "event-fixture",
        "user_id": None,
        "recipient_role": "SAFETY_OFFICER",
        "channel": "DASHBOARD",
        "recipient": "SAFETY_OFFICER",
        "status": "QUEUED",
        "error_message": None,
        "created_at": datetime.now(timezone.utc),
    })
    assert response.user_id is None
    assert response.recipient_role == "SAFETY_OFFICER"


def test_configured_external_channel_is_not_claimed_deliverable(db_session, monkeypatch):
    from app.config import settings
    from app.services.notification_engine import channel_status

    user, _, event = add_actual_record_fixtures(db_session)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setattr(settings, "NOTIFICATION_FROM_ADDRESS", "safety@example.invalid")

    notification = enqueue_notification(
        db_session,
        event_id=event.id,
        user_id=user.id,
        channel="EMAIL",
        recipient=user.email,
    )
    email_status = next(item for item in channel_status(db_session) if item["channel"] == "EMAIL")

    assert notification.status == "NOT_IMPLEMENTED"
    assert "sender is not implemented" in notification.error_message
    assert notification.sent_at is None
    assert email_status["configuration_state"] == "NOT_IMPLEMENTED"


def test_escalation_can_be_acknowledged_without_fabricating_an_actor():
    class SessionStub:
        def commit(self):
            pass

        def refresh(self, _record):
            pass

    escalation = SimpleNamespace(acknowledged_at=None, acknowledged_by_user_id=None)
    acknowledged_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    result = acknowledge_escalation(SessionStub(), escalation, None, now=acknowledged_at)

    assert result.acknowledged_at == acknowledged_at
    assert result.acknowledged_by_user_id is None

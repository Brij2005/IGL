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


def test_configured_email_channel_is_queued_for_real_delivery(db_session, monkeypatch):
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

    assert notification.status == "QUEUED"
    assert notification.error_message is None
    assert notification.sent_at is None
    assert email_status["configuration_state"] == "CONFIGURED"


def test_email_sender_reports_unconfigured_without_claiming_success(monkeypatch):
    from app.config import settings
    from app.services.notification_delivery import send_email

    monkeypatch.setattr(settings, "SMTP_HOST", None)
    result = send_email(["operator@example.test"], "test", "test")
    assert result["status"] == "EMAIL_NOT_CONFIGURED"
    assert result["provider"] == "SMTP"


def test_email_sender_uses_real_smtp_transport_without_exposing_credentials(monkeypatch):
    import smtplib
    from app.config import settings
    from app.services.notification_delivery import send_email

    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent["connection"] = (host, port, timeout)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def ehlo(self):
            pass

        def starttls(self, context):
            sent["tls"] = context is not None

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message, to_addrs):
            sent["message"] = message
            sent["recipients"] = to_addrs
            return {}

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "operator")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", __import__("pydantic").SecretStr("private-test-secret"))
    monkeypatch.setattr(settings, "SMTP_USE_TLS", True)
    monkeypatch.setattr(settings, "SMTP_USE_SSL", False)
    monkeypatch.setattr(settings, "NOTIFICATION_FROM_ADDRESS", "safety@example.test")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    result = send_email(["operator@example.test"], "Safety event", "Real event summary")
    assert result["status"] == "SENT"
    assert sent["recipients"] == ["operator@example.test"]
    assert sent["message"]["Subject"] == "Safety event"
    assert sent["login"] == ("operator", "private-test-secret")


def test_whatsapp_sender_reports_unconfigured_and_validates_real_recipient(monkeypatch):
    from pydantic import SecretStr
    from app.config import settings
    from app.services.notification_delivery import send_whatsapp

    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", None)
    assert send_whatsapp("+15551234567", "test")["status"] == "WHATSAPP_NOT_CONFIGURED"

    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", SecretStr("private-token"))
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setattr(settings, "WHATSAPP_API_VERSION", "v23.0")
    assert send_whatsapp("not-a-phone", "test")["status"] == "DELIVERY_FAILED"


def test_whatsapp_sender_reports_provider_acceptance_only(monkeypatch):
    from pydantic import SecretStr
    from app.config import settings
    from app.services.notification_delivery import send_whatsapp

    captured = {}

    class Response:
        status_code = 200
        def json(self):
            return {"messages": [{"id": "wamid.test-accepted"}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["json"] = kwargs["json"]
        return Response()

    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", SecretStr("private-token"))
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setattr(settings, "WHATSAPP_API_VERSION", "v23.0")
    monkeypatch.setattr("app.services.notification_delivery.httpx.post", fake_post)
    result = send_whatsapp("+15551234567", "Test message")
    assert result["status"] == "SENT"
    assert result["message_id"] == "wamid.test-accepted"
    assert "private-token" not in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer private-token"
    assert captured["json"]["messaging_product"] == "whatsapp"


def test_delivery_worker_records_sent_only_after_transport_acceptance(db_session, monkeypatch):
    from app.config import settings
    from app.models import Notification
    from app.services.notification_delivery import deliver_due_notifications

    user, _, event = add_actual_record_fixtures(db_session)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "NOTIFICATION_FROM_ADDRESS", "safety@example.test")
    row = enqueue_notification(db_session, event_id=event.id, user_id=user.id, channel="EMAIL", recipient=user.email)
    monkeypatch.setattr(
        "app.services.notification_delivery.deliver_notification",
        lambda _db, _row: {"status": "SENT", "provider": "SMTP", "error": None, "message_id": "smtp-test-accepted"},
    )

    summary = deliver_due_notifications(db_session)
    db_session.refresh(row)
    assert summary["sent"] == 1
    assert row.status == "SENT"
    assert row.retry_count == 1
    assert row.sent_at is not None
    assert row.provider == "SMTP"
    assert row.provider_message_id == "smtp-test-accepted"


def test_notification_role_targets_do_not_broadcast_to_unrelated_fallback_recipients(db_session, monkeypatch):
    from app.config import settings
    from app.services.notification_delivery import _email_recipients

    user, _, event = add_actual_record_fixtures(db_session)
    monkeypatch.setattr(settings, "NOTIFICATION_RECIPIENTS", ["fallback@example.test"])
    role_target = enqueue_notification(
        db_session,
        event_id=event.id,
        recipient_role="TEST_OPERATOR",
        channel="EMAIL",
        recipient="TEST_OPERATOR",
    )
    assert _email_recipients(db_session, role_target) == [user.email]


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

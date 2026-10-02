"""Template rendering rules for operator-visible notification messages.

These tests use a real persisted Event row so the rendered message can be
checked against real event facts. They never send anything: no SMTP server, no
WhatsApp credential and no provider is contacted from this file.
"""
from pathlib import Path
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.database import Base
from app.models import Area, Camera, Event, Plant, Zone
from app.services.notification_templates import (
    NOT_AVAILABLE,
    NOT_MEASURED,
    PLACEHOLDERS,
    TEMPLATE_KINDS,
    event_context,
    message_kind,
    placeholders_in,
    render,
    render_email,
    render_whatsapp,
)


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


@pytest.fixture
def real_event(db_session):
    camera = Camera(name="template camera", code="NOT-IGL-CAMERA", stream_url="test-only-source")
    plant = Plant(name="template plant", code="NOT-IGL-PLANT")
    db_session.add_all([camera, plant])
    db_session.flush()
    area = Area(name="template area", code="NOT-IGL-AREA", plant_id=plant.id)
    db_session.add(area)
    db_session.flush()
    zone = Zone(name="template zone", code="NOT-IGL-ZONE", area_id=area.id)
    db_session.add(zone)
    db_session.flush()
    event = Event(
        camera_id=camera.id,
        zone_id=zone.id,
        event_type="PPE_NON_COMPLIANCE",
        severity="HIGH",
        workflow_state="ACKNOWLEDGED",
        observation_state="CONFIRMED",
        confidence=0.87,
        detector_key="PPE",
        model_name="test-detector",
        model_version="test-weights-v1",
        started_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    )
    db_session.add(event)
    db_session.commit()
    return event


def test_documented_placeholder_set_is_the_only_substitutable_set():
    assert PLACEHOLDERS == frozenset(
        {
            "event_id",
            "event_type",
            "severity",
            "state",
            "workflow_state",
            "camera_id",
            "zone_id",
            "confidence",
            "started_at",
            "detector_key",
            "model_name",
            "model_version",
            "incident_id",
            "alarm_id",
            "notes",
        }
    )
    assert set(TEMPLATE_KINDS) == {"INCIDENT", "ESCALATION", "TEST"}


def test_incident_email_renders_real_event_facts(monkeypatch, real_event):
    monkeypatch.setattr(settings, "EMAIL_INCIDENT_BODY_TEMPLATE", "Event {event_id} is {severity} {event_type} in zone {zone_id}")
    monkeypatch.setattr(settings, "EMAIL_INCIDENT_SUBJECT_TEMPLATE", "{severity} safety event: {event_type}")

    message = render_email("INCIDENT", real_event)

    assert message.subject == "HIGH safety event: PPE_NON_COMPLIANCE"
    assert message.body == f"Event {real_event.id} is HIGH PPE_NON_COMPLIANCE in zone {real_event.zone_id}"
    assert message.not_substituted == []


def test_every_documented_placeholder_is_filled_from_a_real_event(monkeypatch, real_event):
    monkeypatch.setattr(settings, "EMAIL_INCIDENT_BODY_TEMPLATE", " | ".join(f"{{{name}}}" for name in sorted(PLACEHOLDERS)))
    context = event_context(real_event)

    text, not_substituted = render(settings.EMAIL_INCIDENT_BODY_TEMPLATE, context)

    assert not_substituted == []
    assert text == " | ".join(context[name] for name in sorted(PLACEHOLDERS))
    assert real_event.id in text
    assert "CONFIRMED" in text and "ACKNOWLEDGED" in text
    assert "0.87" in text
    assert "2026-01-02T03:04:05" in text
    assert "test-detector" in text and "test-weights-v1" in text


def test_missing_facts_render_sentinels_never_invented_values(monkeypatch, db_session):
    camera = Camera(name="no zone camera", code="NOT-IGL-CAMERA", stream_url="test-only-source")
    db_session.add(camera)
    db_session.flush()
    event = Event(camera_id=camera.id, event_type="SMOKE", confidence=None)
    db_session.add(event)
    db_session.commit()
    monkeypatch.setattr(
        settings,
        "EMAIL_INCIDENT_BODY_TEMPLATE",
        "zone={zone_id} confidence={confidence} incident={incident_id} alarm={alarm_id} notes={notes} detector={detector_key}",
    )

    message = render_email("INCIDENT", event)

    assert "zone=NOT_AVAILABLE" in message.body
    assert f"confidence={NOT_MEASURED}" in message.body
    assert "incident=NOT_AVAILABLE" in message.body
    assert "alarm=NOT_AVAILABLE" in message.body
    assert "notes=NOT_AVAILABLE" in message.body
    assert "detector=NOT_AVAILABLE" in message.body
    assert message.not_substituted == []


def test_unknown_placeholder_is_left_visible_and_reported(monkeypatch, real_event):
    monkeypatch.setattr(settings, "EMAIL_INCIDENT_BODY_TEMPLATE", "Event {event_id} for {weather_station}")

    message = render_email("INCIDENT", real_event)

    assert "weather_station" in message.not_substituted
    assert "{weather_station}" in message.body
    assert "Event " + real_event.id in message.body


def test_secret_looking_placeholder_is_never_substituted_from_settings(monkeypatch, real_event):
    monkeypatch.setattr(settings, "SMTP_PASSWORD", SecretStr("smtp-password-must-not-leak"))
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", SecretStr("whatsapp-token-must-not-leak"))
    monkeypatch.setattr(
        settings,
        "EMAIL_INCIDENT_BODY_TEMPLATE",
        "id={event_id} pw={smtp_password} token={whatsapp_access_token} nested={notification.payload_summary}",
    )
    context = event_context(real_event)
    # Even a caller that smuggles a secret into the context cannot get it
    # rendered: the name is outside the documented placeholder set.
    context["smtp_password"] = "smtp-password-must-not-leak"

    text, not_substituted = render(settings.EMAIL_INCIDENT_BODY_TEMPLATE, context)

    assert "smtp-password-must-not-leak" not in text
    assert "whatsapp-token-must-not-leak" not in text
    assert "{smtp_password}" in text
    assert "{whatsapp_access_token}" in text
    assert "smtp_password" in not_substituted
    assert "whatsapp_access_token" in not_substituted


def test_substituted_value_is_not_rescanned_as_a_placeholder(real_event):
    # A value containing braces must not trigger a second substitution pass.
    context = event_context(real_event)
    context["event_type"] = "SMOKE_{severity}"

    text, not_substituted = render("type={event_type}", context)

    assert text == "type=SMOKE_{severity}"
    assert not_substituted == []


def test_absent_body_template_falls_back_to_the_existing_default_text(monkeypatch, real_event):
    monkeypatch.setattr(settings, "EMAIL_INCIDENT_BODY_TEMPLATE", None)
    monkeypatch.setattr(settings, "EMAIL_INCIDENT_SUBJECT_TEMPLATE", "{severity} safety event: {event_type}")
    monkeypatch.setattr(settings, "WHATSAPP_INCIDENT_TEMPLATE", None)

    email_message = render_email("INCIDENT", real_event)
    whatsapp_message = render_whatsapp("INCIDENT", real_event)

    assert email_message.subject == f"{real_event.severity} safety event: {real_event.event_type}"
    assert email_message.body.startswith(f"Safety event: {real_event.event_type}\nSeverity: {real_event.severity}")
    assert f"Event ID: {real_event.id}" in email_message.body
    assert f"Started at: {real_event.started_at.isoformat()}" in email_message.body
    assert f"Camera ID: {real_event.camera_id}" in email_message.body
    assert f"Zone ID: {real_event.zone_id}" in email_message.body
    assert "Confidence: 0.87" in email_message.body
    assert whatsapp_message.body == email_message.body
    assert email_message.not_substituted == []


def test_escalation_and_test_kinds_use_their_own_templates(monkeypatch, real_event):
    monkeypatch.setattr(settings, "EMAIL_ESCALATION_SUBJECT_TEMPLATE", "ESCALATED {event_type} {incident_id}")
    monkeypatch.setattr(settings, "EMAIL_ESCALATION_BODY_TEMPLATE", "Escalated: {event_type}")
    monkeypatch.setattr(settings, "EMAIL_TEST_BODY_TEMPLATE", "Operator test for {event_type}")
    monkeypatch.setattr(settings, "WHATSAPP_ESCALATION_TEMPLATE", "ESCALATED WA {event_type}")
    monkeypatch.setattr(settings, "WHATSAPP_TEST_TEMPLATE", "Operator test WA {event_type}")

    escalation = render_email("ESCALATION", real_event)
    escalation_wa = render_whatsapp("ESCALATION", real_event)
    test_message = render_email("TEST", real_event)
    test_wa = render_whatsapp("TEST", real_event)

    assert escalation.subject == "ESCALATED PPE_NON_COMPLIANCE NOT_AVAILABLE"
    assert escalation.body == "Escalated: PPE_NON_COMPLIANCE"
    assert escalation_wa.body == "ESCALATED WA PPE_NON_COMPLIANCE"
    assert test_message.subject == "IGL Safety Intelligence test"
    assert test_message.body == "Operator test for PPE_NON_COMPLIANCE"
    assert test_wa.body == "Operator test WA PPE_NON_COMPLIANCE"


def test_default_test_templates_are_the_configured_operator_text(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_TEST_BODY_TEMPLATE", None)
    monkeypatch.setattr(settings, "WHATSAPP_TEST_TEMPLATE", None)

    assert render_email("TEST").body == "SMTP test requested by an operator."
    assert render_whatsapp("TEST").body == "IGL Safety Intelligence operator test message."


def test_message_kind_is_incident_until_an_escalation_exists():
    # Plain objects, not persisted rows: the kind depends only on whether the
    # event already carries an escalation record.
    assert message_kind(SimpleNamespace(escalations=[])) == "INCIDENT"
    assert message_kind(SimpleNamespace(escalations=[SimpleNamespace(id="e-1")])) == "ESCALATION"


def test_placeholders_in_lists_unknown_names_in_order():
    assert placeholders_in("{b} {a} {b} {c_1}") == ["b", "a", "c_1"]
    assert placeholders_in(None) == []


def test_render_of_empty_template_is_empty_and_reports_nothing():
    assert render("", {"event_id": "x"}) == ("", [])
    assert render(None, {"event_id": "x"}) == ("", [])
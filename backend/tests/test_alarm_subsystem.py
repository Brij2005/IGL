"""Alarm subsystem tests.

An alarm is a real record of a real, confirmed safety event. These tests assert
the policy that keeps that true:

* nothing is raised for an event that is not CONFIRMED,
* the policy can suppress a raise for an explicit, auditable reason,
* cooldown and repeat limits stop one sustained condition from becoming a storm,
* every state change is validated, reasoned and audited,
* and the physical actuator is never reported as activated unless a real
  transport call returned success.

Where a transport is exercised it is a monkeypatched unit stub, named and
documented as such. Nothing in this file contacts a real broker, relay or siren.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _import_path in (str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")):
    if _import_path not in sys.path:
        sys.path.insert(0, _import_path)

from app.config import settings  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.main import app, seed_reference_roles  # noqa: E402
from app.models import (  # noqa: E402
    Alarm,
    AlarmStateTransition,
    Area,
    Camera,
    Event,
    Plant,
    Role,
    User,
    Zone,
)
from app.services import alarm_engine, physical_alarm  # noqa: E402
from app.services.escalation_engine import (  # noqa: E402
    acknowledge_escalation,
    evaluate_event_escalation,
)

NOW = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///file:alarm_subsystem_db?mode=memory&cache=shared&uri=true",
        connect_args={"check_same_thread": False, "uri": True},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def confirmed_event(db):
    """A persisted, temporally verified event. This is the only kind of event
    that may ever raise an alarm."""
    seed_reference_roles(db)
    plant = Plant(name="Alarm Fixture Plant", code="AFP-1")
    db.add(plant)
    db.flush()
    area = Area(plant_id=plant.id, name="Alarm Fixture Area", code="AFA-1")
    db.add(area)
    db.flush()
    zone = Zone(area_id=area.id, name="Alarm Fixture Zone", code="AFZ-1", zone_type="RESTRICTED")
    db.add(zone)
    db.flush()
    camera = Camera(name="Alarm Fixture Camera", code="AFC-1", stream_url="rtsp://fixture.invalid/live", zone_id=zone.id)
    db.add(camera)
    db.flush()
    event = Event(
        camera_id=camera.id,
        zone_id=zone.id,
        event_type="FIRE",
        observation_state="CONFIRMED",
        severity="HIGH",
        workflow_state="NEW",
        confidence=0.91,
        started_at=NOW,
        detector_key="FIRE",
        verification_state="VERIFIED",
        temporal_observations=5,
        model_name="fixture-model",
        model_version="fixture-v0",
    )
    db.add(event)
    officer_role = db.query(Role).filter(Role.name == "SAFETY_OFFICER").one()
    officer = User(username="alarm_officer", email="alarm@example.test", full_name="Alarm Officer", role_id=officer_role.id)
    db.add(officer)
    db.commit()
    db.refresh(event)
    return db, event, officer


def test_unconfirmed_event_never_raises_an_alarm(confirmed_event):
    db, event, _ = confirmed_event
    event.observation_state = "POSSIBLE"
    db.commit()
    decision = alarm_engine.evaluate_alarm_policy(db, event)
    assert decision["raise_alarm"] is False
    assert decision["reason"] == "EVENT_NOT_CONFIRMED"
    assert db.query(Alarm).count() == 0


def test_confirmed_event_raises_one_audited_alarm(confirmed_event):
    db, event, _ = confirmed_event
    outcome = alarm_engine.raise_alarm_for_event(db, event)
    assert outcome["raised"] is True
    alarm = db.query(Alarm).filter(Alarm.id == outcome["alarm_id"]).one()
    assert alarm.state == "ACTIVE"
    assert alarm.event_id == event.id
    assert alarm.severity == "HIGH"
    assert alarm.detector_key == "FIRE"
    # A physical alarm was never attempted, so it must not claim actuation.
    assert alarm.physical_state == physical_alarm.PHYSICAL_NOT_CONFIGURED
    assert alarm.physical_activated_at is None
    transitions = db.query(AlarmStateTransition).filter(AlarmStateTransition.alarm_id == alarm.id).all()
    assert len(transitions) == 1
    assert transitions[0].previous_state is None
    assert transitions[0].new_state == "ACTIVE"


def test_disabled_alarm_policy_raises_nothing(confirmed_event, monkeypatch):
    db, event, _ = confirmed_event
    monkeypatch.setattr(settings, "ALARM_ENABLED", False)
    decision = alarm_engine.evaluate_alarm_policy(db, event)
    assert decision["suppressed"] is True
    assert decision["reason"] == alarm_engine.SUPPRESSED_DISABLED
    assert alarm_engine.raise_alarm_for_event(db, event)["raised"] is False
    assert db.query(Alarm).count() == 0


def test_severity_below_policy_raises_nothing(confirmed_event, monkeypatch):
    db, event, _ = confirmed_event
    event.severity = "LOW"
    db.commit()
    monkeypatch.setattr(settings, "ALARM_MIN_SEVERITY", "HIGH")
    decision = alarm_engine.evaluate_alarm_policy(db, event)
    assert decision["suppressed"] is True
    assert decision["reason"] == alarm_engine.SUPPRESSED_BELOW_MIN_SEVERITY


def test_cooldown_suppresses_a_repeat_raise(confirmed_event, monkeypatch):
    db, event, _ = confirmed_event
    monkeypatch.setattr(settings, "ALARM_COOLDOWN_SECONDS", 120.0)
    first = alarm_engine.raise_alarm_for_event(db, event)
    second = alarm_engine.raise_alarm_for_event(db, event)
    assert first["raised"] is True
    assert second["raised"] is False
    assert second["suppressed"] is True
    assert second["reason"] == alarm_engine.SUPPRESSED_COOLDOWN
    assert second["existing_alarm_id"] == first["alarm_id"]
    assert db.query(Alarm).count() == 1


def test_repeat_inside_cooldown_renews_the_open_alarm(confirmed_event, monkeypatch):
    db, event, _ = confirmed_event
    monkeypatch.setattr(settings, "ALARM_COOLDOWN_SECONDS", 0.0)
    first = alarm_engine.raise_alarm_for_event(db, event)
    second = alarm_engine.raise_alarm_for_event(db, event)
    assert second["alarm_id"] == first["alarm_id"]
    assert second["reactivated"] is True
    alarm = db.query(Alarm).filter(Alarm.id == first["alarm_id"]).one()
    assert alarm.raised_count == 2
    assert db.query(Alarm).count() == 1


def test_repeat_limit_suppresses_an_alarm_storm(confirmed_event, monkeypatch):
    db, event, _ = confirmed_event
    monkeypatch.setattr(settings, "ALARM_COOLDOWN_SECONDS", 0.0)
    monkeypatch.setattr(settings, "ALARM_MAX_REPEATS_PER_WINDOW", 1)
    monkeypatch.setattr(settings, "ALARM_REPEAT_WINDOW_SECONDS", 900.0)
    first = alarm_engine.raise_alarm_for_event(db, event)
    alarm_engine.clear_alarm(db, db.query(Alarm).filter(Alarm.id == first["alarm_id"]).one(), reason="test cycle")
    second = alarm_engine.raise_alarm_for_event(db, event)
    assert second["raised"] is False
    assert second["reason"] == alarm_engine.SUPPRESSED_REPEAT_LIMIT
    assert second["suppressed"] is True


def test_suppression_is_recorded_on_the_open_alarm(confirmed_event, monkeypatch):
    db, event, _ = confirmed_event
    raised = alarm_engine.raise_alarm_for_event(db, event)
    recorded = alarm_engine.record_suppression(db, event, alarm_engine.SUPPRESSED_COOLDOWN)
    assert recorded["recorded"] is True
    alarm = db.query(Alarm).filter(Alarm.id == raised["alarm_id"]).one()
    assert alarm.suppression_count == 1
    reasons = [row.reason for row in db.query(AlarmStateTransition).filter(AlarmStateTransition.alarm_id == alarm.id).all()]
    assert any("suppressed by policy" in reason for reason in reasons)


def test_acknowledgement_is_reasoned_and_audited(confirmed_event):
    db, event, officer = confirmed_event
    alarm = db.query(Alarm).filter(Alarm.id == alarm_engine.raise_alarm_for_event(db, event)["alarm_id"]).one()
    alarm = alarm_engine.acknowledge_alarm(db, alarm, user_id=officer.id)
    assert alarm.state == "ACKNOWLEDGED"
    assert alarm.acknowledged_by_user_id == officer.id
    assert alarm.acknowledged_at is not None
    transition = db.query(AlarmStateTransition).filter(AlarmStateTransition.alarm_id == alarm.id).order_by(AlarmStateTransition.transitioned_at).all()[-1]
    assert transition.user_id == officer.id
    assert transition.previous_state == "ACTIVE"
    assert transition.new_state == "ACKNOWLEDGED"


def test_acknowledging_a_cleared_alarm_is_rejected(confirmed_event):
    db, event, officer = confirmed_event
    alarm = db.query(Alarm).filter(Alarm.id == alarm_engine.raise_alarm_for_event(db, event)["alarm_id"]).one()
    alarm_engine.clear_alarm(db, alarm, reason="Operator cleared it")
    with pytest.raises(alarm_engine.AlarmPreconditionError):
        alarm_engine.acknowledge_alarm(db, alarm, user_id=officer.id)


def test_a_transition_without_a_reason_is_rejected(confirmed_event):
    db, event, _ = confirmed_event
    alarm = db.query(Alarm).filter(Alarm.id == alarm_engine.raise_alarm_for_event(db, event)["alarm_id"]).one()
    with pytest.raises(alarm_engine.InvalidAlarmTransition):
        alarm_engine.clear_alarm(db, alarm, reason="   ")


def test_cleared_alarm_is_terminal(confirmed_event):
    db, event, _ = confirmed_event
    alarm = db.query(Alarm).filter(Alarm.id == alarm_engine.raise_alarm_for_event(db, event)["alarm_id"]).one()
    alarm = alarm_engine.clear_alarm(db, alarm, reason="Condition cleared")
    assert alarm.state == "CLEARED"
    assert alarm.cleared_at is not None
    assert alarm_engine.allowed_transitions("CLEARED") == []
    with pytest.raises(alarm_engine.AlarmPreconditionError):
        alarm_engine.escalate_alarm(db, alarm, reason="too late")


def test_unacknowledged_alarm_expires(confirmed_event, monkeypatch):
    db, event, _ = confirmed_event
    monkeypatch.setattr(settings, "ALARM_AUTO_EXPIRE_SECONDS", 60.0)
    alarm = db.query(Alarm).filter(Alarm.id == alarm_engine.raise_alarm_for_event(db, event)["alarm_id"]).one()
    raised_at = alarm.raised_at
    assert alarm_engine.expire_stale_alarms(db, now=raised_at + timedelta(seconds=30)) == 0
    assert alarm_engine.expire_stale_alarms(db, now=raised_at + timedelta(seconds=120)) == 1
    db.refresh(alarm)
    assert alarm.state == "EXPIRED"


def test_alarm_summary_counts_only_persisted_alarms(confirmed_event, monkeypatch):
    db, event, _ = confirmed_event
    empty = alarm_engine.alarm_summary(db)
    assert empty["total_alarms"] == 0
    assert empty["active_alarm_count"] == 0
    assert empty["last_alarm_id"] is None
    assert empty["physical_alarm"]["state"] == physical_alarm.PHYSICAL_NOT_CONFIGURED
    assert empty["physical_alarm"]["hardware_verified"] is False
    alarm_engine.raise_alarm_for_event(db, event)
    populated = alarm_engine.alarm_summary(db)
    assert populated["total_alarms"] == 1
    assert populated["active_alarm_count"] == 1
    assert populated["last_alarm_id"] is not None


def test_escalation_moves_the_linked_alarm_to_escalated(confirmed_event):
    from app.models import EscalationPolicy, EventEscalation

    db, event, officer = confirmed_event
    alarm = db.query(Alarm).filter(Alarm.id == alarm_engine.raise_alarm_for_event(db, event)["alarm_id"]).one()
    policy = EscalationPolicy(
        name="Fixture escalation",
        event_type="FIRE",
        severity="HIGH",
        escalation_level=1,
        from_role="SAFETY_OFFICER",
        to_role="PLANT_MANAGER",
        escalate_after_seconds=0,
        is_active=True,
    )
    db.add(policy)
    db.commit()
    report = evaluate_event_escalation(db, event, now=NOW + timedelta(seconds=10))
    assert report["status"] == "ESCALATED"
    db.refresh(alarm)
    assert alarm.state == "ESCALATED"
    assert report["alarm_escalations"] == [alarm.id]

    escalation = db.query(EventEscalation).filter(EventEscalation.event_id == event.id).one()
    acknowledge_escalation(db, escalation, officer)
    db.refresh(alarm)
    assert alarm.state == "ACKNOWLEDGED"
    assert alarm.acknowledged_by_user_id == officer.id


def test_physical_alarm_reports_not_configured_without_a_transport(monkeypatch):
    monkeypatch.setattr(settings, "ALARM_PHYSICAL_ACTUATION", True)
    for field in (
        "PHYSICAL_ALARM_HTTP_URL",
        "PHYSICAL_ALARM_MQTT_HOST",
        "PHYSICAL_ALARM_SERIAL_PORT",
    ):
        monkeypatch.setattr(settings, field, None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_GPIO_PIN", 0)
    status = physical_alarm.actuator_status()
    assert status["state"] == physical_alarm.PHYSICAL_NOT_CONFIGURED
    assert status["configured_transports"] == []
    outcome = physical_alarm.actuate({"alarm_id": "fixture"})
    assert outcome["state"] == physical_alarm.PHYSICAL_NOT_CONFIGURED
    assert outcome["state"] != physical_alarm.ACTIVATED


def test_physical_alarm_reports_not_connected_when_the_driver_is_missing(monkeypatch):
    """A configured MQTT transport with no client library is not an activation."""
    monkeypatch.setattr(settings, "ALARM_PHYSICAL_ACTUATION", True)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_MQTT_HOST", "broker.fixture.invalid")
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_MQTT_TOPIC", "plant/alarm")
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_HTTP_URL", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_SERIAL_PORT", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_GPIO_PIN", 0)
    outcome = physical_alarm.actuate({"alarm_id": "fixture"})
    if outcome["transport"] == physical_alarm.TRANSPORT_MQTT:
        assert outcome["state"] in (
            physical_alarm.ACTUATOR_NOT_CONNECTED,
            physical_alarm.ACTIVATION_FAILED,
        )
        assert outcome["state"] != physical_alarm.ACTIVATED
    else:
        assert outcome["state"] in (
            physical_alarm.ACTUATOR_NOT_CONNECTED,
            physical_alarm.ACTIVATION_FAILED,
        )


def test_physical_alarm_activates_only_on_a_real_transport_success(monkeypatch):
    """Unit stub: httpx is monkeypatched, so this proves the code path, not hardware."""
    import httpx

    class StubResponse:
        status_code = 204

    class StubClient:
        def __init__(self, *args, **kwargs):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, json=None, headers=None):
            self.calls.append((method, url, json))
            return StubResponse()

    monkeypatch.setattr(settings, "ALARM_PHYSICAL_ACTUATION", True)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_HTTP_URL", SecretStr("https://actuator.fixture.invalid/activate"))
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_MQTT_HOST", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_SERIAL_PORT", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_GPIO_PIN", 0)
    monkeypatch.setattr(httpx, "Client", StubClient)
    outcome = physical_alarm.actuate({"alarm_id": "fixture", "severity": "HIGH"}, "ACTIVATE")
    assert outcome["state"] == physical_alarm.ACTIVATED
    assert outcome["transport"] == physical_alarm.TRANSPORT_HTTP


def test_physical_alarm_does_not_claim_activation_on_a_failed_call(monkeypatch):
    import httpx

    class StubResponse:
        status_code = 503

    class StubClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, json=None, headers=None):
            return StubResponse()

    monkeypatch.setattr(settings, "ALARM_PHYSICAL_ACTUATION", True)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_HTTP_URL", SecretStr("https://actuator.fixture.invalid/activate"))
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_MQTT_HOST", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_SERIAL_PORT", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_GPIO_PIN", 0)
    monkeypatch.setattr(httpx, "Client", StubClient)
    outcome = physical_alarm.actuate({"alarm_id": "fixture"}, "ACTIVATE")
    assert outcome["state"] == physical_alarm.ACTIVATION_FAILED
    assert outcome["reason"] == "PROVIDER_HTTP_503"


def test_alarm_api_exposes_policy_and_history(confirmed_event):
    db, event, _ = confirmed_event
    alarm = db.query(Alarm).filter(Alarm.id == alarm_engine.raise_alarm_for_event(db, event)["alarm_id"]).one()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            policy = client.get("/api/v1/alarms/policy").json()
            assert policy["active_alarm_count"] == 1
            assert policy["physical_alarm"]["state"] == physical_alarm.PHYSICAL_NOT_CONFIGURED
            assert policy["audible_browser_alarm"] in (True, False)

            live = client.get("/api/v1/alarms", params={"active_only": True}).json()
            assert [item["id"] for item in live] == [alarm.id]
            assert live[0]["physical_state"] == physical_alarm.PHYSICAL_NOT_CONFIGURED
            assert "ACKNOWLEDGED" in live[0]["allowed_transitions"]

            history = client.get(f"/api/v1/alarms/{alarm.id}/history").json()
            assert history[-1]["new_state"] == "ACTIVE"

            physical = client.get("/api/v1/alarms/physical/status").json()
            assert physical["hardware_verified"] is False

            acknowledged = client.post(f"/api/v1/alarms/{alarm.id}/acknowledge", json={"notes": "Operator acknowledged"})
            assert acknowledged.status_code == 200
            assert acknowledged.json()["state"] == "ACKNOWLEDGED"

            conflict = client.post(f"/api/v1/alarms/{alarm.id}/acknowledge", json={"notes": "Again"})
            assert conflict.status_code == 409
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_alarm_api_refuses_to_raise_for_an_unconfirmed_event(confirmed_event):
    db, event, _ = confirmed_event
    event.observation_state = "POSSIBLE"
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.post(f"/api/v1/alarms/events/{event.id}/raise", json={"reason": "Operator asked"})
            assert response.status_code == 409
            assert "EVENT_NOT_CONFIRMED" in response.json()["detail"]
            assert db.query(Alarm).count() == 0
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_alarm_api_reports_not_configured_for_a_physical_test(confirmed_event):
    db, _, _ = confirmed_event
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/alarms/physical/test")
            assert response.status_code == 200
            payload = response.json()
            assert payload["state"] == physical_alarm.PHYSICAL_NOT_CONFIGURED
            assert payload["hardware_verified"] is False
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_alarm_api_returns_404_for_an_unknown_alarm(confirmed_event):
    db, _, _ = confirmed_event
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/alarms/does-not-exist").status_code == 404
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_raising_an_alarm_attempts_real_physical_actuation_when_a_transport_is_configured(
    confirmed_event, monkeypatch
):
    """Unit stub: httpx is monkeypatched, so this proves the code path only."""
    import httpx

    class StubResponse:
        status_code = 200

    class StubClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, json=None, headers=None):
            return StubResponse()

    db, event, _ = confirmed_event
    monkeypatch.setattr(settings, "ALARM_PHYSICAL_ACTUATION", True)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_HTTP_URL", SecretStr("https://actuator.fixture.invalid/activate"))
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_MQTT_HOST", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_SERIAL_PORT", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_GPIO_PIN", 0)
    monkeypatch.setattr(httpx, "Client", StubClient)
    outcome = alarm_engine.raise_alarm_for_event(db, event)
    alarm = db.query(Alarm).filter(Alarm.id == outcome["alarm_id"]).one()
    assert alarm.physical_state == physical_alarm.ACTIVATED
    assert alarm.physical_activated_at is not None


def test_physical_state_records_a_failed_actuation_instead_of_activation(confirmed_event, monkeypatch):
    import httpx

    class StubResponse:
        status_code = 500

    class StubClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, json=None, headers=None):
            return StubResponse()

    db, event, _ = confirmed_event
    monkeypatch.setattr(settings, "ALARM_PHYSICAL_ACTUATION", True)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_HTTP_URL", SecretStr("https://actuator.fixture.invalid/activate"))
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_MQTT_HOST", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_SERIAL_PORT", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_GPIO_PIN", 0)
    monkeypatch.setattr(httpx, "Client", StubClient)
    outcome = alarm_engine.raise_alarm_for_event(db, event)
    alarm = db.query(Alarm).filter(Alarm.id == outcome["alarm_id"]).one()
    assert alarm.physical_state == physical_alarm.ACTIVATION_FAILED
    assert alarm.physical_activated_at is None
    assert "PROVIDER_HTTP_500" in alarm.physical_result


def test_clearing_an_activated_alarm_records_the_deactivation(confirmed_event, monkeypatch):
    import httpx

    class StubResponse:
        status_code = 200

    class StubClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, json=None, headers=None):
            return StubResponse()

    db, event, _ = confirmed_event
    monkeypatch.setattr(settings, "ALARM_PHYSICAL_ACTUATION", True)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_HTTP_URL", SecretStr("https://actuator.fixture.invalid/activate"))
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_MQTT_HOST", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_SERIAL_PORT", None)
    monkeypatch.setattr(settings, "PHYSICAL_ALARM_GPIO_PIN", 0)
    monkeypatch.setattr(httpx, "Client", StubClient)
    alarm = db.query(Alarm).filter(Alarm.id == alarm_engine.raise_alarm_for_event(db, event)["alarm_id"]).one()
    assert alarm.physical_state == physical_alarm.ACTIVATED
    alarm = alarm_engine.clear_alarm(db, alarm, reason="Operator cleared the condition")
    assert alarm.physical_state == physical_alarm.DEACTIVATED
    assert alarm.physical_cleared_at is not None


def test_alarm_without_a_configured_actuator_is_never_activated(confirmed_event, monkeypatch):
    db, event, _ = confirmed_event
    monkeypatch.setattr(settings, "ALARM_PHYSICAL_ACTUATION", False)
    outcome = alarm_engine.raise_alarm_for_event(db, event)
    alarm = db.query(Alarm).filter(Alarm.id == outcome["alarm_id"]).one()
    assert alarm.physical_state == physical_alarm.PHYSICAL_NOT_CONFIGURED
    assert alarm.physical_activated_at is None

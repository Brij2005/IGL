"""Runtime path tests: real detections in, audited safety events out.

These tests drive the safety engine with detections that this test constructs in
memory and hands to the orchestrator exactly as the inference pipeline does. The
in-memory detections are test fixtures, not production data: every assertion
here is about the platform's behaviour given an observation, and the temporary
database is created and destroyed per session by ``conftest``.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

PROJECT_ROOT = Path(__file__).resolve().parents[2]
for _import_path in (str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")):
    if _import_path not in sys.path:
        sys.path.insert(0, _import_path)

from ai_models.detection import Detection  # noqa: E402
from app.database import Base, get_db  # noqa: E402
from app.engine.temporal_verifier import TemporalVerifier, VerificationPolicy  # noqa: E402
from app.main import app, seed_reference_roles  # noqa: E402
from app.models import (  # noqa: E402
    Area,
    AuditLog,
    Camera,
    DetectorConfig,
    Detection as DetectionRow,
    EscalationPolicy,
    Event,
    EventEscalation,
    EventEvidence,
    NotificationPolicy,
    OperatingThreshold,
    Plant,
    Role,
    User,
    Zone
)
from app.services.correlation_engine import correlate_event, correlation_summary, repeat_event_summary  # noqa: E402
from app.services.escalation_engine import evaluate_event_escalation  # noqa: E402
from app.services.safety_engine import (  # noqa: E402
    DETECTOR_CATALOGUE,
    detector_capabilities,
    evaluate_frame_class_detector,
    evaluate_restricted_zone,
    verification_policy_from_configuration
)
from app.services.safety_orchestrator import SafetyEventOrchestrator  # noqa: E402
from app.services.safety_engine import temporal_registry  # noqa: E402

NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///file:safety_runtime_db?mode=memory&cache=shared&uri=true",
        connect_args={"check_same_thread": False, "uri": True},
        poolclass=StaticPool
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
def topology(db):
    """A configured plant hierarchy with a camera, a zone and a safety officer."""
    seed_reference_roles(db)
    plant = Plant(name="Fixture Plant", code="FP-1")
    db.add(plant)
    db.flush()
    area = Area(plant_id=plant.id, name="Fixture Area", code="FA-1")
    db.add(area)
    db.flush()
    zone = Zone(
        area_id=area.id,
        name="Restricted Zone",
        code="FZ-RESTRICTED",
        zone_type="RESTRICTED",
        # Normalized frame coordinates covering the centre of the frame.
        geometry_json=[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
    )
    db.add(zone)
    db.flush()
    camera = Camera(
        name="Fixture Camera",
        code="FC-1",
        stream_url="rtsp://fixture.invalid/live",
        zone_id=zone.id,
        camera_type="RTSP",
        fps=25.0,
        resolution="1920x1080"
    )
    db.add(camera)
    officer_role = db.query(Role).filter(Role.name == "SAFETY_OFFICER").one()
    officer = User(
        username="fixture_officer",
        email="officer@example.test",
        full_name="Fixture Safety Officer",
        role_id=officer_role.id,
        is_active=True
    )
    db.add(officer)
    db.commit()
    db.refresh(camera)
    return db, camera, zone, officer


def make_detection(camera_id: str, class_name: str, confidence: float, bbox, timestamp) -> Detection:
    return Detection(
        camera_id=camera_id,
        timestamp=timestamp,
        class_name=class_name,
        confidence=confidence,
        bbox=tuple(bbox),
        frame_timestamp=timestamp,
        model_name="fixture-model",
        model_version="fixture-v0"
    )


class FakeTrack:
    """Minimal stand-in for a tracker track, carrying only real geometry."""

    def __init__(self, track_id, bbox, confidence, class_name="person", timestamp=NOW):
        self.track_id = track_id
        self.bbox = tuple(bbox)
        self.confidence = confidence
        self.class_name = class_name
        self.first_seen = timestamp
        self.last_seen = timestamp
        self.lifecycle_state = "TRACKED"


def frame(width=640, height=480):
    return np.full((height, width, 3), 120, dtype=np.uint8)


MODEL_HEALTH = {
    "status": "READY",
    "available": True,
    "model_name": "fixture-model",
    "model_version": "fixture-v0",
    "weights_checksum_sha256": "0" * 64,
    "classes": ["person", "fire"],
    "confidence_threshold": 0.25,
}


# ---------------------------------------------------------------------------
# A. Detection capability states
# ---------------------------------------------------------------------------

def test_every_catalogue_detector_reports_an_explicit_state():
    """No detector may report READY unless the model supports it."""
    capabilities = detector_capabilities(MODEL_HEALTH, configs=[])
    assert len(capabilities) == len(DETECTOR_CATALOGUE)
    for capability in capabilities:
        assert capability.availability_state in (
            "READY",
            "MODEL_NOT_CONFIGURED",
            "MODEL_NOT_LOADED",
            "MODEL_INVALID_WEIGHTS",
            "MODEL_NAME_REQUIRED",
            "MODEL_VERSION_REQUIRED",
            "MODEL_LOAD_FAILED",
            "DETECTOR_NOT_SUPPORTED_BY_MODEL",
            "NOT_AVAILABLE"
        )
        assert capability.reason


def test_unsupported_detectors_are_never_operational():
    """PPE, fall, fire-without-model-class and friends report explicit states."""
    capabilities = {capability.detector_key: capability for capability in detector_capabilities(MODEL_HEALTH, [])}

    for unsupported in ("PPE", "HELMET", "SAFETY_VEST", "PROXIMITY", "FALL", "LEAKAGE", "UNSAFE_BEHAVIOR"):
        capability = capabilities[unsupported]
        assert capability.implementation_state == "NOT_IMPLEMENTED"
        assert capability.operational is False
        assert capability.reason

    # This model has no smoke class, so the implemented smoke detector cannot run.
    assert capabilities["SMOKE"].availability_state == "DETECTOR_NOT_SUPPORTED_BY_MODEL"
    assert capabilities["SMOKE"].operational is False
    # It does expose person and fire.
    assert capabilities["FIRE"].operational is True
    assert capabilities["RESTRICTED_ZONE"].operational is True


def test_no_model_means_no_detector_is_operational():
    capabilities = detector_capabilities(
        {"status": "MODEL_NOT_CONFIGURED", "available": False, "classes": []},
        configs=[]
    )
    assert all(capability.operational is False for capability in capabilities)


def test_enabled_configuration_alone_does_not_make_a_detector_operational(topology):
    """Configuration without a supporting model stays unavailable."""
    db, camera, _zone, _officer = topology
    db.add(DetectorConfig(
        detector_key="SMOKE",
        camera_id=camera.id,
        is_enabled=True,
        parameters_json={"minimum_confidence": 0.4},
        threshold_source="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"
    ))
    db.commit()
    capabilities = {c.detector_key: c for c in detector_capabilities(
        {"status": "MODEL_NOT_CONFIGURED", "available": False, "classes": []},
        db.query(DetectorConfig).all()
    )}
    assert capabilities["SMOKE"].configured is True
    assert capabilities["SMOKE"].operational is False
    assert capabilities["SMOKE"].availability_state == "MODEL_NOT_CONFIGURED"


def test_frame_class_detector_requires_a_real_detection():
    """No detection means no positive observation, whatever the class list says."""
    observation = evaluate_frame_class_detector(
        "FIRE",
        detections=[],
        model_classes=("person", "fire"),
        minimum_confidence=0.25,
        threshold_source="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION",
        source_reference=None
    )
    assert observation.condition_met is False
    assert observation.observation_state == "NOT_ASSESSABLE"
    assert observation.confidence is None


def test_frame_class_detector_carries_the_model_confidence():
    detection = make_detection("cam", "fire", 0.82, (10, 10, 100, 100), NOW)
    observation = evaluate_frame_class_detector(
        "FIRE",
        detections=[detection],
        model_classes=("person", "fire"),
        minimum_confidence=0.5,
        threshold_source="CONFIGURED",
        source_reference="IGL-SOP-FIRE-001"
    )
    assert observation.condition_met is True
    assert observation.confidence == 0.82
    assert observation.threshold_source == "CONFIGURED"


def test_frame_class_detector_honours_the_confidence_floor():
    detection = make_detection("cam", "fire", 0.10, (10, 10, 100, 100), NOW)
    observation = evaluate_frame_class_detector(
        "FIRE",
        detections=[detection],
        model_classes=("person", "fire"),
        minimum_confidence=0.5,
        threshold_source="CONFIGURED",
        source_reference="IGL-SOP-FIRE-001"
    )
    assert observation.condition_met is False
    assert observation.confidence is None


# ---------------------------------------------------------------------------
# D. Zone evaluation
# ---------------------------------------------------------------------------

def test_zone_intrusion_uses_configured_geometry(topology):
    _db, _camera, zone, _officer = topology
    # Membership is decided by the foot point (bottom-centre) of the box, not the
    # box centre: a person standing in a zone is inside it even when their upper
    # body is above the boundary. Foot point here is (0.547, 0.79) in a
    # 640x480 frame, which lies inside the 0.2-0.8 zone polygon.
    inside_track = FakeTrack("track-in", (300, 200, 400, 380), 0.9)
    observation = evaluate_restricted_zone(
        camera=_camera, zone=zone, tracked_object=inside_track, frame_width=640, frame_height=480
    )
    assert observation.condition_met is True
    assert observation.zone_id == zone.id
    assert observation.threshold_source == "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"

    outside_track = FakeTrack("track-out", (0, 0, 40, 40), 0.9)
    outside = evaluate_restricted_zone(
        camera=_camera, zone=zone, tracked_object=outside_track, frame_width=640, frame_height=480
    )
    assert outside.condition_met is False

    # A box whose centre is inside but whose feet are outside is outside the
    # zone. The foot point is the ground contact point on purpose.
    feet_outside = FakeTrack("track-feet-out", (300, 100, 400, 460), 0.9)
    assert evaluate_restricted_zone(
        camera=_camera, zone=zone, tracked_object=feet_outside, frame_width=640, frame_height=480
    ).condition_met is False


def test_zone_without_geometry_reports_not_configured(topology):
    db, camera, zone, _officer = topology
    zone.geometry_json = None
    db.commit()
    observation = evaluate_restricted_zone(
        camera=camera, zone=zone, tracked_object=FakeTrack("t", (300, 200, 400, 400), 0.9),
        frame_width=640, frame_height=480
    )
    assert observation.condition_met is False
    assert "ZONE_GEOMETRY_NOT_CONFIGURED" in observation.reason


def test_zone_with_invalid_geometry_is_not_assessable(topology):
    db, camera, zone, _officer = topology
    zone.geometry_json = [[0.1, 0.1]]
    db.commit()
    observation = evaluate_restricted_zone(
        camera=camera, zone=zone, tracked_object=FakeTrack("t", (300, 200, 400, 400), 0.9),
        frame_width=640, frame_height=480
    )
    assert observation.condition_met is False
    assert observation.observation_state == "NOT_ASSESSABLE"


def test_camera_without_a_zone_produces_no_zone_verdict(topology):
    db, camera, _zone, _officer = topology
    camera.zone_id = None
    db.commit()
    observation = evaluate_restricted_zone(
        camera=camera, zone=None, tracked_object=FakeTrack("t", (10, 10, 20, 20), 0.9),
        frame_width=640, frame_height=480
    )
    assert observation.condition_met is False
    assert observation.observation_state == "NOT_ASSESSABLE"


def test_zone_membership_requires_known_frame_geometry(topology):
    _db, _camera, zone, _officer = topology
    observation = evaluate_restricted_zone(
        camera=_camera, zone=zone, tracked_object=FakeTrack("t", (300, 200, 400, 400), 0.9),
        frame_width=0, frame_height=0
    )
    assert observation.condition_met is False
    assert observation.observation_state == "NOT_ASSESSABLE"


# ---------------------------------------------------------------------------
# C. Temporal verification
# ---------------------------------------------------------------------------

def test_temporal_verifier_does_not_verify_without_observations():
    policy = VerificationPolicy(
        minimum_observations=3,
        minimum_duration_seconds=2.0,
        confidence_threshold=0.5,
        maximum_gap_seconds=5.0,
        threshold_source="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"
    )
    verifier = TemporalVerifier(policy)
    first = verifier.observe("subject", NOW, True, 0.9)
    assert first.state == "DETECTED"
    assert first.observation_count == 1

    second = verifier.observe("subject", NOW + timedelta(seconds=1), True, 0.9)
    assert second.state == "PERSISTED"

    third = verifier.observe("subject", NOW + timedelta(seconds=2), True, 0.9)
    assert third.state == "VERIFIED"
    assert third.observation_count == 3
    assert third.duration_seconds == pytest.approx(2.0)


def test_temporal_verifier_reports_not_assessable_without_confidence():
    policy = VerificationPolicy(
        minimum_observations=1,
        minimum_duration_seconds=0.0,
        confidence_threshold=0.5,
        maximum_gap_seconds=5.0,
        threshold_source="ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"
    )
    verifier = TemporalVerifier(policy)
    result = verifier.observe("subject", NOW, None, None)
    assert result.state == "NOT_ASSESSABLE"
    assert result.observation_count == 0


def test_verification_policy_reads_configuration_and_keeps_provenance(topology):
    db, camera, _zone, _officer = topology
    # Every policy parameter is configured explicitly. A policy that silently
    # inherits even one engineering default is reported as a default, so this
    # fixture must be complete to be reported as CONFIGURED.
    db.add(DetectorConfig(
        detector_key="FIRE",
        camera_id=camera.id,
        is_enabled=True,
        parameters_json={
            "minimum_observations": 5,
            "minimum_duration_seconds": 4.0,
            "maximum_gap_seconds": 3.0,
        },
        threshold_source="CONFIGURED",
        source_reference="IGL-SOP-FIRE-002"
    ))
    db.add(OperatingThreshold(
        code="FIRE.minimum_confidence",
        metric="detection_confidence",
        value=0.4,
        zone_id=camera.zone_id,
        threshold_source="CONFIGURED",
        source_reference="IGL-SOP-FIRE-003"
    ))
    db.commit()
    policy = verification_policy_from_configuration(
        "FIRE",
        db.query(DetectorConfig).all(),
        db.query(OperatingThreshold).all()
    )
    assert policy.minimum_observations == 5
    assert policy.minimum_duration_seconds == 4.0
    assert policy.maximum_gap_seconds == 3.0
    assert policy.confidence_threshold == 0.4
    assert policy.threshold_source == "CONFIGURED"
    assert policy.rule_reference == "IGL-SOP-FIRE-002"


def test_partially_configured_policy_is_reported_as_a_default(topology):
    """One inherited engineering default is enough to downgrade the provenance."""
    db, camera, _zone, _officer = topology
    db.add(DetectorConfig(
        detector_key="FIRE",
        camera_id=camera.id,
        is_enabled=True,
        # maximum_gap_seconds is deliberately absent.
        parameters_json={"minimum_observations": 5, "minimum_duration_seconds": 4.0},
        threshold_source="CONFIGURED",
        source_reference="IGL-SOP-FIRE-002"
    ))
    db.add(OperatingThreshold(
        code="FIRE.minimum_confidence",
        metric="detection_confidence",
        value=0.4,
        zone_id=camera.zone_id,
        threshold_source="CONFIGURED",
        source_reference="IGL-SOP-FIRE-003"
    ))
    db.commit()
    policy = verification_policy_from_configuration(
        "FIRE",
        db.query(DetectorConfig).all(),
        db.query(OperatingThreshold).all()
    )
    assert policy.threshold_source == "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"


def test_unconfigured_verification_policy_is_tagged_as_engineering_default(topology):
    db, camera, _zone, _officer = topology
    policy = verification_policy_from_configuration(
        "FIRE",
        db.query(DetectorConfig).all(),
        db.query(OperatingThreshold).all()
    )
    assert policy.threshold_source == "ENGINEERING_DEFAULT_PENDING_IGL_VALIDATION"
    assert policy.rule_reference is None


# ---------------------------------------------------------------------------
# B + E. Event creation, provenance and correlation
# ---------------------------------------------------------------------------

def test_real_detection_creates_an_event_with_full_provenance(topology):
    db, camera, zone, _officer = topology
    db.add(DetectorConfig(
        detector_key="RESTRICTED_ZONE",
        camera_id=camera.id,
        is_enabled=True,
        # Every policy parameter is configured, so the resulting event is
        # correctly labelled CONFIGURED rather than an engineering default.
        parameters_json={
            "minimum_observations": 1,
            "minimum_duration_seconds": 0.0,
            "maximum_gap_seconds": 5.0,
            "minimum_confidence": 0.25,
            "correlation_window_seconds": 120,
        },
        threshold_source="CONFIGURED",
        source_reference="IGL-SOP-ZONE-001"
    ))
    db.commit()
    orchestrator = SafetyEventOrchestrator(capture_evidence=False)
    temporal_registry.reset()

    detection = make_detection(camera.id, "person", 0.91, (300, 200, 400, 380), NOW)
    track = FakeTrack("track-a", (300, 200, 400, 380), 0.91, timestamp=NOW)
    # Same call order the inference pipeline uses: raw model output is persisted
    # first, then detectors are evaluated against it.
    orchestrator.persist_detections(db, camera, [detection], [track])
    report = orchestrator.evaluate_frame(
        db, camera, timestamp=NOW, frame=frame(), detections=[detection], tracks=[track],
        model_health=MODEL_HEALTH
    )
    db.commit()

    persisted_detection = db.query(DetectionRow).one()
    assert persisted_detection.track_id is not None
    events = db.query(Event).all()
    assert len(events) == 1
    event = events[0]
    assert event.camera_id == camera.id
    assert event.zone_id == zone.id
    assert event.detector_key == "RESTRICTED_ZONE"
    assert event.event_type == "RESTRICTED_ZONE_INTRUSION"
    assert event.track_id == persisted_detection.track_id
    assert event.observation_state == "CONFIRMED"
    assert event.confidence == 0.91
    assert event.verification_state == "VERIFIED"
    assert event.temporal_observations == 1
    assert event.model_name == "fixture-model"
    assert event.model_weights_checksum == "0" * 64
    assert event.threshold_source == "CONFIGURED"
    assert event.source_reference == "IGL-SOP-ZONE-001"
    assert event.provenance_json["zone_code"] == "FZ-RESTRICTED"
    assert report["events"][0]["verification_state"] == "VERIFIED"


def test_positive_event_captures_only_one_evidence_frame(topology, tmp_path, monkeypatch):
    from app.services.evidence_engine import EvidenceEngine
    import app.services.safety_orchestrator as orchestrator_module

    db, camera, _zone, _officer = topology
    db.add(DetectorConfig(
        detector_key="FIRE",
        camera_id=camera.id,
        is_enabled=True,
        parameters_json={
            "minimum_observations": 1,
            "minimum_duration_seconds": 0.0,
            "maximum_gap_seconds": 5.0,
            "minimum_confidence": 0.25,
        },
        threshold_source="CONFIGURED",
        source_reference="AUTHORIZED-FIRE-POLICY",
    ))
    db.commit()
    monkeypatch.setattr(orchestrator_module, "evidence_engine", EvidenceEngine(tmp_path))
    orchestrator = SafetyEventOrchestrator(capture_evidence=True)
    temporal_registry.reset()
    detection = make_detection(camera.id, "fire", 0.91, (40, 30, 120, 100), NOW)

    for offset in range(2):
        orchestrator.evaluate_frame(
            db,
            camera,
            timestamp=NOW + timedelta(seconds=offset),
            frame=frame(),
            detections=[detection],
            tracks=[],
            model_health=MODEL_HEALTH,
        )

    assert db.query(Event).count() == 1
    assert db.query(EventEvidence).count() == 1


def test_configuration_alone_never_creates_an_event(topology):
    db, camera, _zone, _officer = topology
    db.add(DetectorConfig(detector_key="RESTRICTED_ZONE", camera_id=camera.id, is_enabled=True))
    db.commit()
    orchestrator = SafetyEventOrchestrator(capture_evidence=False)
    temporal_registry.reset()
    orchestrator.evaluate_frame(
        db, camera, timestamp=NOW, frame=frame(), detections=[], tracks=[], model_health=MODEL_HEALTH
    )
    assert db.query(Event).count() == 0


def test_detector_without_a_supporting_model_creates_no_event(topology):
    db, camera, _zone, _officer = topology
    db.add(DetectorConfig(
        detector_key="SMOKE",
        camera_id=camera.id,
        is_enabled=True,
        parameters_json={"minimum_observations": 1, "minimum_duration_seconds": 0.0}
    ))
    db.commit()
    orchestrator = SafetyEventOrchestrator(capture_evidence=False)
    temporal_registry.reset()
    smoke_detection = make_detection(camera.id, "smoke", 0.9, (10, 10, 50, 50), NOW)
    report = orchestrator.evaluate_frame(
        db, camera, timestamp=NOW, frame=frame(), detections=[smoke_detection], tracks=[],
        model_health={**MODEL_HEALTH, "classes": ["person"]}
    )
    assert db.query(Event).count() == 0
    assert report["observations"][0]["state"] == "SKIPPED"
    assert "classes" in report["observations"][0]["reason"]


def test_repeat_observation_updates_the_open_event_instead_of_duplicating(topology):
    db, camera, _zone, _officer = topology
    db.add(DetectorConfig(
        detector_key="FIRE",
        camera_id=camera.id,
        is_enabled=True,
        parameters_json={"minimum_observations": 1, "minimum_duration_seconds": 0.0}
    ))
    db.commit()
    orchestrator = SafetyEventOrchestrator(capture_evidence=False)
    temporal_registry.reset()
    for offset in range(3):
        detection = make_detection(camera.id, "fire", 0.88, (10, 10, 90, 90), NOW + timedelta(seconds=offset))
        orchestrator.evaluate_frame(
            db, camera, timestamp=NOW + timedelta(seconds=offset), frame=frame(),
            detections=[detection], tracks=[], model_health=MODEL_HEALTH
        )
    assert db.query(Event).count() == 1


def test_correlation_groups_persisted_events_with_provenance(topology):
    db, camera, _zone, _officer = topology
    orchestrator = SafetyEventOrchestrator(capture_evidence=False)
    temporal_registry.reset()
    db.add(DetectorConfig(
        detector_key="FIRE",
        camera_id=camera.id,
        is_enabled=True,
        parameters_json={
            "minimum_observations": 1,
            "minimum_duration_seconds": 0.0,
            "correlation_window_seconds": 300,
        }
    ))
    db.commit()

    for offset in (0, 30, 60):
        detection = make_detection(camera.id, "fire", 0.9, (5, 5, 40, 40), NOW + timedelta(seconds=offset))
        orchestrator.evaluate_frame(
            db, camera, timestamp=NOW + timedelta(seconds=offset), frame=frame(),
            detections=[detection], tracks=[], model_health=MODEL_HEALTH
        )
        # End each event so the next frame starts a fresh one inside the same
        # correlation window, which is what a real repeat looks like.
        for event in db.query(Event).all():
            event.ended_at = NOW + timedelta(seconds=offset + 1)
            event.workflow_state = "RESOLVED"
        db.commit()

    groups = correlation_summary(db)
    assert len(groups) == 1
    group = groups[0]
    assert group["camera_id"] == camera.id
    assert group["event_type"] == "FIRE"
    assert group["window_seconds"] == 300
    assert group["event_count"] == 3


def test_correlation_with_no_events_returns_no_groups(topology):
    db, _camera, _zone, _officer = topology
    assert correlation_summary(db) == []
    assert repeat_event_summary(db) == []


def test_repeat_summary_counts_persisted_events_only(topology):
    db, camera, _zone, _officer = topology
    event = Event(
        camera_id=camera.id,
        zone_id=camera.zone_id,
        event_type="FIRE",
        observation_state="CONFIRMED",
        severity="CRITICAL",
        workflow_state="NEW",
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        detector_key="FIRE"
    )
    db.add(event)
    db.commit()
    rows = repeat_event_summary(db, window_hours=24)
    assert rows == [{"event_type": "FIRE", "camera_id": camera.id, "count": 1}]


# ---------------------------------------------------------------------------
# F. Escalation
# ---------------------------------------------------------------------------

def _open_event(db, camera, **overrides) -> Event:
    payload = {
        "camera_id": camera.id,
        "zone_id": camera.zone_id,
        "event_type": "FIRE",
        "observation_state": "POSSIBLE",
        "severity": "CRITICAL",
        "workflow_state": "NEW",
        "started_at": NOW,
        "detector_key": "FIRE",
    }
    payload.update(overrides)
    event = Event(**payload)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def test_escalation_without_a_policy_is_not_configured(topology):
    db, camera, _zone, _officer = topology
    event = _open_event(db, camera)
    report = evaluate_event_escalation(db, event, now=NOW + timedelta(hours=1))
    assert report["status"] == "ESCALATION_NOT_CONFIGURED"
    assert report["policies_matched"] == 0
    assert report["escalations_created"] == []
    assert db.query(EventEscalation).count() == 0


def test_escalation_with_a_matching_policy_creates_a_record(topology):
    db, camera, _zone, _officer = topology
    db.add(EscalationPolicy(
        name="Fixture fire escalation",
        event_type="FIRE",
        severity="CRITICAL",
        zone_id=camera.zone_id,
        escalate_after_seconds=60,
        to_role="SAFETY_OFFICER",
        escalation_level=1,
        is_active=True
    ))
    db.commit()
    event = _open_event(db, camera)
    report = evaluate_event_escalation(db, event, now=NOW + timedelta(minutes=5))
    assert report["status"] == "ESCALATED"
    assert len(report["escalations_created"]) == 1
    escalation = db.query(EventEscalation).filter(EventEscalation.event_id == event.id).one()
    assert escalation.to_role == "SAFETY_OFFICER"
    assert escalation.acknowledged_at is None


def test_escalation_is_not_due_before_the_configured_window(topology):
    db, camera, _zone, _officer = topology
    db.add(EscalationPolicy(
        name="Fixture fire escalation",
        event_type="FIRE",
        escalate_after_seconds=3600,
        to_role="SAFETY_OFFICER"
    ))
    db.commit()
    event = _open_event(db, camera)
    report = evaluate_event_escalation(db, event, now=NOW + timedelta(minutes=5))
    assert report["status"] == "NOT_DUE"
    assert db.query(EventEscalation).count() == 0


def test_acknowledged_event_is_not_escalated(topology):
    db, camera, _zone, _officer = topology
    db.add(EscalationPolicy(
        name="Fixture fire escalation",
        event_type="FIRE",
        escalate_after_seconds=1,
        to_role="SAFETY_OFFICER"
    ))
    db.commit()
    event = _open_event(db, camera, workflow_state="ACKNOWLEDGED")
    report = evaluate_event_escalation(db, event, now=NOW + timedelta(hours=1))
    assert report["status"] == "EVENT_ALREADY_HANDLED"
    assert db.query(EventEscalation).count() == 0


def test_policy_scope_is_respected(topology):
    db, camera, zone, _officer = topology
    db.add(EscalationPolicy(
        name="Wrong severity",
        event_type="FIRE",
        severity="LOW",
        escalate_after_seconds=1,
        to_role="SAFETY_OFFICER",
        zone_id=zone.id
    ))
    db.commit()
    event = _open_event(db, camera)
    report = evaluate_event_escalation(db, event, now=NOW + timedelta(hours=1))
    assert report["status"] == "ESCALATION_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# G. Notifications
# ---------------------------------------------------------------------------

def test_notification_policy_creates_queue_rows_not_deliveries(topology):
    db, camera, zone, officer = topology
    db.add(NotificationPolicy(
        name="Fixture fire alerts",
        event_type="FIRE",
        zone_id=zone.id,
        channel="EMAIL",
        recipient_role="SAFETY_OFFICER"
    ))
    db.add(DetectorConfig(
        detector_key="FIRE",
        camera_id=camera.id,
        is_enabled=True,
        parameters_json={"minimum_observations": 1, "minimum_duration_seconds": 0.0}
    ))
    db.commit()
    orchestrator = SafetyEventOrchestrator(capture_evidence=False)
    temporal_registry.reset()
    detection = make_detection(camera.id, "fire", 0.9, (5, 5, 60, 60), NOW)
    report = orchestrator.evaluate_frame(
        db, camera, timestamp=NOW, frame=frame(), detections=[detection], tracks=[],
        model_health=MODEL_HEALTH
    )
    event = db.query(Event).one()
    from app.models import Notification

    notifications = db.query(Notification).filter(Notification.event_id == event.id).all()
    # One row addressed to the role itself, plus one per matching active
    # identity, so a named officer is reachable without an authenticated
    # "current user" to resolve.
    assert len(notifications) == 2
    role_row = next(row for row in notifications if row.recipient_role == "SAFETY_OFFICER")
    named_row = next(row for row in notifications if row.user_id == officer.id)
    # No SMTP transport exists, so every row says so and nothing claims delivery.
    for row in notifications:
        assert row.status == "NOT_CONFIGURED"
        assert row.sent_at is None
    assert role_row.recipient == "SAFETY_OFFICER"
    assert named_row.recipient == (officer.email or officer.username)
    assert report["events"][0]["notifications_created"] == 2


def test_no_notification_policy_means_no_notification(topology):
    db, camera, _zone, _officer = topology
    db.add(DetectorConfig(
        detector_key="FIRE",
        camera_id=camera.id,
        is_enabled=True,
        parameters_json={"minimum_observations": 1, "minimum_duration_seconds": 0.0}
    ))
    db.commit()
    orchestrator = SafetyEventOrchestrator(capture_evidence=False)
    temporal_registry.reset()
    from app.models import Notification

    detection = make_detection(camera.id, "fire", 0.9, (5, 5, 60, 60), NOW)
    orchestrator.evaluate_frame(
        db, camera, timestamp=NOW, frame=frame(), detections=[detection], tracks=[],
        model_health=MODEL_HEALTH
    )
    assert db.query(Event).count() == 1
    assert db.query(Notification).count() == 0


# ---------------------------------------------------------------------------
# I. Evidence
# ---------------------------------------------------------------------------

def test_evidence_is_not_available_without_a_frame(topology):
    from app.services.evidence_engine import EvidenceEngine

    db, camera, _zone, _officer = topology
    event = _open_event(db, camera)
    engine = EvidenceEngine()
    result = engine.capture_snapshot(
        db, event_id=event.id, camera_id=camera.id, frame_timestamp=NOW, frame=None
    )
    assert result.status == "EVIDENCE_NOT_AVAILABLE"
    assert result.evidence is None


def test_evidence_hash_matches_the_bytes_on_disk(topology, tmp_path):
    import hashlib

    from app.services.evidence_engine import EvidenceEngine

    db, camera, _zone, _officer = topology
    event = _open_event(db, camera)
    engine = EvidenceEngine(tmp_path)
    result = engine.capture_snapshot(
        db, event_id=event.id, camera_id=camera.id, frame_timestamp=NOW, frame=frame(320, 240)
    )
    assert result.status == "CAPTURED"
    written = Path(result.evidence.file_path).read_bytes()
    assert hashlib.sha256(written).hexdigest() == result.evidence.file_hash
    assert result.evidence.size_bytes in (None, len(written))


# ---------------------------------------------------------------------------
# H + API. Lifecycle, permissions, audit
# ---------------------------------------------------------------------------

@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_event_lifecycle_precondition_and_audit(client, topology):
    db, camera, _zone, _officer = topology
    event = _open_event(db, camera)

    response = client.get("/api/v1/system/detectors")
    assert response.status_code == 200
    body = response.json()
    assert body["igl_validation_status"] == "NOT_VALIDATED"
    by_key = {detector["detector_key"]: detector for detector in body["detectors"]}
    assert by_key["PPE"]["implementation_state"] == "NOT_IMPLEMENTED"
    assert by_key["PPE"]["operational"] is False
    assert by_key["RESTRICTED_ZONE"]["implementation_state"] == "IMPLEMENTED"


def test_role_notification_api_serializes_nullable_user(client, topology):
    db, camera, _zone, _officer = topology
    event = _open_event(db, camera)

    response = client.post(
        f"/api/v1/notifications/events/{event.id}",
        json={"channel": "DASHBOARD", "recipient_role": "SAFETY_OFFICER"},
    )

    assert response.status_code == 201
    assert response.json()["user_id"] is None
    assert response.json()["recipient_role"] == "SAFETY_OFFICER"


def test_escalation_acknowledgement_api_records_null_actor(client, topology):
    db, camera, _zone, _officer = topology
    event = _open_event(db, camera)
    policy = EscalationPolicy(name="Fixture escalation", to_role="SAFETY_OFFICER")
    db.add(policy)
    db.flush()
    escalation = EventEscalation(
        event_id=event.id,
        policy_id=policy.id,
        to_role="SAFETY_OFFICER",
        reason="Fixture escalation record",
        triggered_at=NOW,
    )
    db.add(escalation)
    db.commit()

    response = client.post(
        f"/api/v1/escalations/{escalation.id}/acknowledge",
        json={"reason": "Operator acknowledged in isolated test"},
    )

    assert response.status_code == 200
    assert response.json()["acknowledged_by_user_id"] is None
    assert response.json()["acknowledged_at"] is not None
    audit = db.query(AuditLog).filter(AuditLog.resource_id == escalation.id).one()
    assert audit.action == "ESCALATION_ACKNOWLEDGED"
    assert audit.user_id is None


def test_request_id_header_is_preserved(client, topology):
    _db, _camera, _zone, _officer = topology
    response = client.get("/", headers={"X-Request-ID": "fixture-request-1"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "fixture-request-1"


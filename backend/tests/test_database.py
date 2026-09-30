"""
Unit tests for SQLAlchemy domain models and database foundation.
Verifies all 22 required domain entities, schema constraints, relationships,
identity nullability, observation states, and workflow states.
"""
import pytest
import sys
import os
import sqlite3
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure backend directory is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Base
from app.models import (
    Plant, Area, Zone, Camera, CameraHealth,
    Role, User, PPERule, Equipment, Track, Detection,
    Event, EventEvidence, Incident, NearMiss, Acknowledgement,
    Assignment, CorrectiveAction, Notification, AuditLog,
    ModelVersion, ModelMetric
)


@pytest.fixture
def db_session():
    """Create an in-memory SQLite database session for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_init_db_creates_all_22_tables(db_session):
    """Verify all 22 required tables exist in metadata."""
    expected_tables = {
        'plants', 'areas', 'zones', 'cameras', 'camera_health',
        'roles', 'users', 'ppe_rules', 'equipments', 'tracks',
        'detections', 'events', 'event_evidence', 'incidents',
        'near_misses', 'acknowledgements', 'assignments',
        'corrective_actions', 'notifications', 'audit_logs',
        'model_versions', 'model_metrics'
    }
    created_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(created_tables)


def test_plant_hierarchy_cascade(db_session):
    """Verify Plant -> Area -> Zone -> Camera relationship and cascade."""
    plant = Plant(name="IGL Kashipur Plant", code="IGL-KASHIPUR-01", location="Kashipur, Uttarakhand")
    db_session.add(plant)
    db_session.commit()

    area = Area(plant_id=plant.id, name="Chemical Synthesis", code="CS-01")
    db_session.add(area)
    db_session.commit()

    zone = Zone(area_id=area.id, name="Reactor Bay 1", code="RB-01", zone_type="HAZARDOUS")
    db_session.add(zone)
    db_session.commit()

    camera = Camera(zone_id=zone.id, name="Reactor Camera 1", code="CAM-RB1-01", stream_url="rtsp://10.0.0.1/live/1")
    db_session.add(camera)
    db_session.commit()

    assert plant.areas[0].name == "Chemical Synthesis"
    assert plant.areas[0].zones[0].name == "Reactor Bay 1"
    assert plant.areas[0].zones[0].cameras[0].code == "CAM-RB1-01"


def test_track_employee_identity_is_strictly_nullable(db_session):
    """
    CRITICAL RULE: Visual AI track ID is NOT employee identity.
    Verify employee_id is None by default and does not auto-assign identity.
    """
    plant = Plant(name="Test Plant", code="TP-01")
    db_session.add(plant)
    db_session.commit()
    area = Area(plant_id=plant.id, name="Test Area", code="TA-01")
    db_session.add(area)
    db_session.commit()
    zone = Zone(area_id=area.id, name="Test Zone", code="TZ-01")
    db_session.add(zone)
    db_session.commit()
    camera = Camera(zone_id=zone.id, name="Test Cam", code="TC-01", stream_url="rtsp://test")
    db_session.add(camera)
    db_session.commit()

    track = Track(camera_id=camera.id, track_uuid="track-xyz-123", object_class="PERSON")
    db_session.add(track)
    db_session.commit()

    db_session.refresh(track)
    assert track.employee_id is None
    assert track.employee is None


def test_observation_states_and_not_assessable(db_session):
    """Verify NOT_ASSESSABLE state is supported in Detection and Event."""
    plant = Plant(name="Test Plant", code="TP-02")
    db_session.add(plant)
    db_session.commit()

    area = Area(plant_id=plant.id, name="Test Area", code="TA-02")
    db_session.add(area)
    db_session.commit()

    zone = Zone(area_id=area.id, name="Test Zone", code="TZ-02")
    db_session.add(zone)
    db_session.commit()

    camera = Camera(zone_id=zone.id, name="Test Cam", code="TC-02", stream_url="rtsp://test")
    db_session.add(camera)
    db_session.commit()

    # Create detection with NOT_ASSESSABLE observation state (e.g. occlusion)
    detection = Detection(
        camera_id=camera.id,
        object_class="PERSON",
        confidence=0.45,
        bbox_json=[100, 100, 200, 400],
        observation_state="NOT_ASSESSABLE",
        metadata_json={"occlusion": True, "reason": "Heavy steam plume obscuring PPE"}
    )
    db_session.add(detection)
    db_session.commit()

    assert detection.observation_state == "NOT_ASSESSABLE"

    # Create event with NOT_ASSESSABLE observation state
    event = Event(
        camera_id=camera.id,
        zone_id=zone.id,
        event_type="NOT_ASSESSABLE",
        observation_state="NOT_ASSESSABLE",
        severity="LOW",
        workflow_state="NEW",
        confidence=0.5
    )
    db_session.add(event)
    db_session.commit()

    assert event.observation_state == "NOT_ASSESSABLE"


def test_event_workflow_states(db_session):
    """Verify complete event workflow states transitions."""
    plant = Plant(name="Test Plant", code="TP-03")
    db_session.add(plant)
    db_session.commit()

    area = Area(plant_id=plant.id, name="Test Area", code="TA-03")
    db_session.add(area)
    db_session.commit()

    zone = Zone(area_id=area.id, name="Test Zone", code="TZ-03")
    db_session.add(zone)
    db_session.commit()

    camera = Camera(zone_id=zone.id, name="Test Cam", code="TC-03", stream_url="rtsp://test")
    db_session.add(camera)
    db_session.commit()

    event = Event(
        camera_id=camera.id,
        zone_id=zone.id,
        event_type="PPE_NON_COMPLIANCE",
        observation_state="CONFIRMED",
        severity="HIGH",
        workflow_state="NEW",
        confidence=0.92
    )
    db_session.add(event)
    db_session.commit()

    assert event.workflow_state == "NEW"

    # Transition to ACKNOWLEDGED
    event.workflow_state = "ACKNOWLEDGED"
    db_session.commit()
    assert event.workflow_state == "ACKNOWLEDGED"

    # Transition to ASSIGNED -> ACTION_REQUIRED -> RESOLVED -> CLOSED
    for state in ["ASSIGNED", "UNDER_INVESTIGATION", "ACTION_REQUIRED", "RESOLVED", "CLOSED"]:
        event.workflow_state = state
        db_session.commit()
        assert event.workflow_state == state


def test_camera_health_and_ai_metrics(db_session):
    """Verify CameraHealth monitoring entity and ModelMetric validation flag."""
    plant = Plant(name="Test Plant", code="TP-04")
    db_session.add(plant)
    db_session.commit()

    area = Area(plant_id=plant.id, name="Test Area", code="TA-04")
    db_session.add(area)
    db_session.commit()

    zone = Zone(area_id=area.id, name="Test Zone", code="TZ-04")
    db_session.add(zone)
    db_session.commit()

    camera = Camera(zone_id=zone.id, name="Test Cam", code="TC-04", stream_url="rtsp://test")
    db_session.add(camera)
    db_session.commit()

    health = CameraHealth(
        camera_id=camera.id,
        status="ONLINE",
        fps=24.5,
        latency_ms=120.0,
        is_frozen=False,
        is_black=False,
        image_quality_score=0.98,
        inference_status="RUNNING"
    )
    db_session.add(health)
    db_session.commit()

    assert health.status == "ONLINE"
    assert health.camera.code == "TC-04"

    model_ver = ModelVersion(
        model_name="yolov8n-ppe-igl",
        version="v1.0.0",
        task_type="PPE_DETECTOR",
        weights_path="ai_models/ppe_v1.pt"
    )
    db_session.add(model_ver)
    db_session.commit()

    metric = ModelMetric(
        model_version_id=model_ver.id,
        dataset_name="coco_val_ppe",
        precision=0.89,
        recall=0.86,
        f1_score=0.87,
        is_igl_validated=False  # Must be False until real IGL validation dataset is tested
    )
    db_session.add(metric)
    db_session.commit()

    assert metric.is_igl_validated is False


def test_alembic_upgrade_downgrade_upgrade_uses_temporary_database(tmp_path):
    project_root = Path(__file__).resolve().parents[2]
    database_file = tmp_path / "migration-roundtrip.sqlite"
    database_url = "sqlite:///" + str(database_file).replace("\\", "/")
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url

    def run_alembic(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *arguments],
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def domain_tables():
        with sqlite3.connect(database_file) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        return tables - {"alembic_version"}

    run_alembic("upgrade", "head")
    upgraded_tables = domain_tables()
    assert "event_state_transitions" in upgraded_tables
    assert {"incident_state_transitions", "near_miss_state_transitions", "corrective_action_state_transitions"} <= upgraded_tables
    run_alembic("downgrade", "base")
    assert domain_tables() == set()
    run_alembic("upgrade", "head")
    assert domain_tables() == upgraded_tables

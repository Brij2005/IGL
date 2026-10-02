"""
Automated unit & integration tests for Phase 3 Camera Management, Video Ingestion,
and Camera Health Diagnostics.
"""
import pytest
import sys
import os
import time
import numpy as np
import cv2
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Base, get_db
from app.models import User, Role, Camera, CameraHealth, Event, AuditLog

from app.services.camera_manager import CameraManager
from app.services.health_monitor import health_monitor
from app.services.video_ingestion import StreamReader, ingestion_manager
from app.main import app, seed_reference_roles


@pytest.fixture
def test_db():
    """Create an isolated in-memory SQLite database session."""
    engine = create_engine(
        "sqlite:///file:camera_test_db?mode=memory&cache=shared&uri=true",
        connect_args={"check_same_thread": False, "uri": True},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    
    seed_reference_roles(session)
    
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(test_db):
    """FastAPI TestClient with db override."""
    def override_get_db():
        yield test_db
            
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def admin_auth_headers(test_db):
    """Create an anonymous-mode admin identity for audit and notification paths."""
    admin_role = test_db.query(Role).filter(Role.name == "ADMIN").first()
    admin_user = User(
        username="camera_admin",
        email="cam_admin@igl.test",
        full_name="Camera Admin",
        role_id=admin_role.id,
        is_active=True
    )
    test_db.add(admin_user)
    test_db.commit()
    return {}


def test_camera_crud_api(client, admin_auth_headers, test_db):
    """Verify camera registration, listing, updating, and deactivation."""
    # 1. Register Camera
    payload = {
        "name": "Kashipur Reactor Bay Camera 1",
        "code": "CAM-KAS-RB-01",
        "stream_url": "rtsp://10.0.0.100/live/ch1",
        "camera_type": "RTSP",
        "fps": 25.0,
        "resolution": "1920x1080",
        "location_description": "Reactor Bay 1 North Wall"
    }
    resp = client.post("/api/v1/cameras", json=payload, headers=admin_auth_headers)
    assert resp.status_code == 201
    camera_data = resp.json()
    camera_id = camera_data["id"]
    assert camera_data["code"] == "CAM-KAS-RB-01"
    assert camera_data["health"]["status"] == "CONFIGURED"
    assert camera_data["health"]["measured_fps"] is None
    assert camera_data["health"]["observed_resolution"] is None
    assert camera_data["health"]["brightness_score"] is None

    # 2. List Cameras
    list_resp = client.get("/api/v1/cameras", headers=admin_auth_headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) >= 1

    # 3. Update Camera
    update_payload = {"location_description": "Updated Location", "fps": 30.0}
    up_resp = client.put(f"/api/v1/cameras/{camera_id}", json=update_payload, headers=admin_auth_headers)
    assert up_resp.status_code == 200
    assert up_resp.json()["location_description"] == "Updated Location"
    assert up_resp.json()["fps"] == 30.0

    # 4. Deactivate Camera
    del_resp = client.delete(f"/api/v1/cameras/{camera_id}", headers=admin_auth_headers)
    assert del_resp.status_code == 204

    camera_logs = test_db.query(AuditLog).filter(AuditLog.resource_type == "CAMERA").all()
    assert {entry.action for entry in camera_logs} >= {
        "CAMERA_CREATED", "CAMERA_UPDATED", "CAMERA_DEACTIVATED"
    }
    assert all("rtsp://" not in str(entry.details_json) for entry in camera_logs)


def test_black_frame_detection():
    """Verify health monitor detects pitch-black images (mean brightness < 10.0)."""
    black_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    is_black, quality_score = health_monitor.analyze_frame_quality(black_frame)
    assert is_black is True
    assert quality_score == 0.0

    normal_frame = np.ones((480, 640, 3), dtype=np.uint8) * 128
    is_black_normal, _ = health_monitor.analyze_frame_quality(normal_frame)
    assert is_black_normal is False


def test_frozen_frame_detection():
    """A stalled source is only reported from distinct observed frames.

    Repeating one frame in a buffer is not evidence of a stall: it is the same
    observation counted again. The monitor therefore refuses to return a
    verdict until it has seen the configured number of distinct frames.
    """
    static_frame = np.ones((480, 640, 3), dtype=np.uint8) * 100

    # Fewer frames than the configured minimum of distinct frames yields no
    # verdict at all, because one or two observations cannot establish a stall.
    is_frozen, reason = health_monitor.is_source_frozen([static_frame])
    assert is_frozen is False
    assert "INSUFFICIENT_OBSERVED_FRAMES" in reason

    # Distinct frames that genuinely do not change support a freeze verdict.
    static_variants = []
    for index in range(4):
        variant = static_frame.copy()
        variant[0, 0] = index  # negligible change: still a static scene
        static_variants.append(variant)
    is_frozen, reason = health_monitor.is_source_frozen(static_variants)
    assert is_frozen is True
    assert "DISTINCT_FRAMES" in reason

    # A scene that keeps changing is not a stalled source.
    moving = [np.full((480, 640, 3), value, dtype=np.uint8) for value in (10, 90, 170, 240)]
    is_frozen, reason = health_monitor.is_source_frozen(moving)
    assert is_frozen is False
    assert reason is None


def test_camera_failure_dispatches_not_assessable_event(test_db):
    """
    CRITICAL SAFETY SIGNAL RULE:
    Verify camera failure generates a CAMERA_FAILURE event with observation_state = NOT_ASSESSABLE.
    """
    camera = Camera(
        name="Test Failing Cam",
        code="CAM-FAIL-01",
        stream_url="rtsp://invalid-ip-999.999/live",
        camera_type="RTSP",
        is_active=True
    )
    test_db.add(camera)
    test_db.commit()

    # Evaluate health of disconnected stream
    health = health_monitor.evaluate_camera_health(test_db, camera)
    assert health.status == "OFFLINE"

    # Query dispatched event in DB
    event = test_db.query(Event).filter(
        Event.camera_id == camera.id,
        Event.event_type == "CAMERA_FAILURE"
    ).first()

    assert event is not None
    assert event.observation_state == "NOT_ASSESSABLE"
    assert event.severity == "HIGH"
    # No model scored this observation. A fabricated 1.0 would present an
    # unmeasured value as a model result.
    assert event.confidence is None
    assert event.duration_seconds is None


def test_camera_health_endpoint_does_not_fabricate_a_health_state(test_db):
    """An unobserved camera must report CONFIGURED with null telemetry, not ONLINE."""
    camera = Camera(
        name="Test Unobserved Cam",
        code="CAM-UNOBSERVED-01",
        stream_url="rtsp://10.0.0.11/live",
        camera_type="RTSP",
        is_active=True
    )
    test_db.add(camera)
    test_db.commit()

    health = health_monitor.evaluate_camera_health(test_db, camera)
    assert health.status != "ONLINE"
    assert health.measured_fps is None
    assert health.frame_latency_ms is None


def test_camera_health_telemetry_endpoint(client, admin_auth_headers, test_db):
    """Verify GET /api/v1/cameras/{camera_id}/health returns diagnostic telemetry."""
    camera = Camera(
        name="Test Health Cam",
        code="CAM-HEALTH-01",
        stream_url="rtsp://10.0.0.10/live",
        is_active=True
    )
    test_db.add(camera)
    test_db.commit()

    resp = client.get(f"/api/v1/cameras/{camera.id}/health", headers=admin_auth_headers)
    assert resp.status_code == 200
    telemetry = resp.json()
    assert telemetry["camera_id"] == camera.id
    # A camera that was registered but never evaluated may only report one of
    # the unobserved states; it may never claim a verified health verdict.
    assert telemetry["status"] in (
        "NEVER_EVALUATED",
        "CONFIGURED",
        "STALE",
        "OFFLINE",
        "BLACK_FRAME",
        "FROZEN"
    )
    assert telemetry["status"] not in ("ONLINE", "DEGRADED", "UNRELIABLE")
    # Nothing was measured on a read of an unobserved camera.
    assert telemetry["measured_fps"] is None
    assert telemetry["frame_latency_ms"] is None
    assert telemetry["is_black"] is None
    assert telemetry["is_frozen"] is None

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
from app.auth import hash_password, create_access_token
from app.services.camera_manager import CameraManager
from app.services.health_monitor import health_monitor
from app.services.video_ingestion import StreamReader, ingestion_manager
from app.main import app, seed_default_roles


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
    
    seed_default_roles(session)
    
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
    """Create test admin user and return auth headers."""
    admin_role = test_db.query(Role).filter(Role.name == "ADMIN").first()
    admin_user = User(
        username="camera_admin",
        email="cam_admin@igl.test",
        hashed_password=hash_password("AdminPass123!"),
        full_name="Camera Admin",
        role_id=admin_role.id,
        is_active=True
    )
    test_db.add(admin_user)
    test_db.commit()

    token = create_access_token({"sub": admin_user.username, "user_id": admin_user.id, "role": "ADMIN"})
    return {"Authorization": f"Bearer {token}"}


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
    assert camera_data["health"]["status"] == "UNKNOWN"

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
    """Verify health monitor flags identical static frames as frozen."""
    camera_id = "test_frozen_cam_123"
    static_frame = np.ones((480, 640, 3), dtype=np.uint8) * 100

    # Repeat frame check 5 times
    is_frozen = False
    for _ in range(6):
        is_frozen = health_monitor.check_frozen_frame(camera_id, static_frame)

    assert is_frozen is True


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
    assert telemetry["status"] in ("OFFLINE", "UNKNOWN", "ONLINE", "DEGRADED", "UNRELIABLE")

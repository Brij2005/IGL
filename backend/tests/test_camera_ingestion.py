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
from app.services import inference_pipeline
from app.services.inference_pipeline import InferencePipelineManager
from app.services.video_ingestion import (
    RECONNECT_STATES,
    STREAM_STATES,
    StreamReader,
    ingestion_manager,
)
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


def test_operator_camera_start_and_stop_update_pipeline_lifecycle(client, test_db, monkeypatch):
    """Explicit camera controls must clear/set the health worker's stop latch."""
    camera = Camera(
        name="Lifecycle webcam",
        code="CAM-LIFECYCLE-01",
        stream_url="webcam://0",
        camera_type="WEBCAM",
        is_active=True,
    )
    test_db.add(camera)
    test_db.commit()
    calls = []

    def start_stream(camera_id, stream_url, target_fps, *, operator_start=False):
        calls.append(("start", camera_id, stream_url, target_fps, operator_start))

    def stop_stream(camera_id, *, operator_initiated=False):
        calls.append(("stop", camera_id, operator_initiated))

    monkeypatch.setattr("app.api.cameras.pipeline_manager.start_stream", start_stream)
    monkeypatch.setattr("app.api.cameras.pipeline_manager.stop_stream", stop_stream)

    assert client.post(f"/api/v1/cameras/{camera.id}/start").status_code == 200
    assert client.post(f"/api/v1/cameras/{camera.id}/stop").status_code == 200
    assert calls == [
        ("start", camera.id, "webcam://0", camera.fps, True),
        ("stop", camera.id, True),
    ]


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


# ===========================================================================
# STREAM REOPEN AND RECONNECT STATE
# ===========================================================================


class FakeCapture:
    """A capture handle that serves a fixed list of frames, then stalls."""

    def __init__(self, frames=()):
        self._frames = list(frames)
        self.released = False
        self.opened = True

    def isOpened(self):
        return self.opened

    def read(self):
        if self._frames:
            return True, self._frames.pop(0)
        return False, None

    def get(self, _property):
        return 0.0

    def set(self, *_args):
        return True

    def release(self):
        self.released = True
        self.opened = False


class BlockingCapture(FakeCapture):
    """A capture whose read blocks, so the reader stays connected with no frame."""

    def __init__(self, release_event):
        super().__init__()
        self._release = release_event

    def read(self):
        self._release.wait(10.0)
        return False, None


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_reopen_states_are_declared():
    """An undeclared state is a bug, so the reopen states must be part of the set."""
    assert "REOPENING" in STREAM_STATES
    assert "REOPENED" in STREAM_STATES


def test_a_deliberately_stopped_source_stays_reopening_not_stopped(monkeypatch):
    reader = StreamReader("cam-reopen-state", "webcam://0")
    monkeypatch.setattr(reader, "_open_capture", lambda: FakeCapture([np.zeros((8, 8, 3), dtype=np.uint8)]))
    reader.start()
    try:
        assert _wait_for(lambda: reader.is_alive())
        reader.mark_reopening()
        assert reader.status == "REOPENING"

        reader.stop()

        # A source being reopened is mid-reopen, not merely stopped, and the
        # ordinary stop path must not overwrite that with a bare STOPPED.
        assert reader.reopen_requested is True
        assert reader.status == "REOPENING"
        assert reader.status in STREAM_STATES
    finally:
        reader.stop()


def test_a_reopened_source_reports_reopened_until_a_real_frame_arrives(monkeypatch):
    """REOPENED means re-established but unobserved, not running."""
    import threading

    release = threading.Event()
    monkeypatch.setattr(StreamReader, "_open_capture", lambda self: BlockingCapture(release))
    reader = StreamReader("cam-reopened", "webcam://0", reopen_requested=True)
    reader.start()
    try:
        assert _wait_for(lambda: reader.status == "REOPENED", timeout=5.0)
        # The capture handle is genuinely open, but no frame has been observed
        # from the new session, so it may not report RUNNING.
        assert reader.is_connected is True
        assert reader.total_frames_read == 0
        assert reader.reopen_requested is True
        assert reader.telemetry()["stream_state"] == "REOPENED"
    finally:
        release.set()
        reader.stop(timeout=1.0)


def test_reopen_states_clear_once_a_frame_from_the_new_session_arrives(monkeypatch):
    monkeypatch.setattr(
        StreamReader,
        "_open_capture",
        lambda self: FakeCapture([np.full((8, 8, 3), value, dtype=np.uint8) for value in (10, 40, 90)]),
    )
    reader = StreamReader("cam-reopened-live", "webcam://0", reopen_requested=True)
    reader.start()
    try:
        assert _wait_for(lambda: reader.total_frames_read > 0, timeout=5.0)
        assert _wait_for(lambda: reader.status == "RUNNING", timeout=5.0)
        assert reader.reopen_requested is False
        assert reader.status in STREAM_STATES
    finally:
        reader.stop()


def test_replacing_a_live_source_marks_the_outgoing_reader_reopening(monkeypatch):
    monkeypatch.setattr(
        StreamReader,
        "_open_capture",
        lambda self: FakeCapture([np.full((8, 8, 3), value, dtype=np.uint8) for value in (10, 40, 90, 140)]),
    )
    try:
        first = ingestion_manager.start_stream("cam-reopen-manager", "webcam://0")
        assert _wait_for(lambda: first.is_alive() and first.total_frames_read > 0, timeout=5.0)

        second = ingestion_manager.start_stream("cam-reopen-manager", "webcam://1")

        assert first.status == "REOPENING"
        # The replacement completes the reopen truthfully: the first frame of
        # the new session moves it out of the reopen states and into RUNNING.
        assert _wait_for(lambda: second.total_frames_read > 0 and second.status == "RUNNING", timeout=5.0)
        assert second.reopen_requested is False
    finally:
        ingestion_manager.stop_stream("cam-reopen-manager")


def test_reconnect_state_is_derived_from_live_reader_state():
    reader = StreamReader("cam-reconnect-state", "rtsp://10.0.0.9/live")
    reader.reconnect_attempts = 3

    # Never opened and not waiting: nothing is being retried right now.
    assert reader.reconnect_state() == "IDLE"
    assert reader.reconnect_state() in RECONNECT_STATES

    # A thread genuinely sleeping out its backoff interval is BACKOFF.
    reader._running = True
    reader._reconnect_waiting = True
    assert reader.reconnect_state() == "BACKOFF"

    # An exhausted attempt budget is terminal and stays that way.
    reader._reconnect_waiting = False
    reader.status = "RECONNECT_EXHAUSTED"
    assert reader.reconnect_state() == "RECONNECT_EXHAUSTED"
    reader._running = False


def test_camera_status_exposes_reconnect_attempts_and_name(client, test_db, monkeypatch):
    """The reader already counts attempts; the API has to stop dropping them."""
    camera = Camera(
        name="Reconnect Report Cam",
        code="CAM-RECONNECT-01",
        stream_url="rtsp://10.0.0.12/live",
        is_active=True,
    )
    test_db.add(camera)
    test_db.commit()
    reader = StreamReader(camera.id, camera.stream_url)
    reader.reconnect_attempts = 4
    reader.reconnects = 2
    reader.status = "RECONNECT_EXHAUSTED"
    reader.last_error = "Reconnect attempts exhausted after 4 attempts"
    monkeypatch.setattr("app.api.cameras.ingestion_manager.get_reader", lambda camera_id: reader)

    response = client.get(f"/api/v1/cameras/{camera.id}/status")

    assert response.status_code == 200
    body = response.json()
    assert body["camera_name"] == "Reconnect Report Cam"
    assert body["reconnect_attempts"] == 4
    assert body["reconnects"] == 2
    assert body["reconnect_state"] == "RECONNECT_EXHAUSTED"
    assert body["reopen_in_progress"] is False


def test_camera_status_of_a_camera_that_never_started_reports_zero_attempts(client, test_db):
    camera = Camera(
        name="Never Started Cam",
        code="CAM-NEVER-STARTED-01",
        stream_url="rtsp://10.0.0.13/live",
        is_active=True,
    )
    test_db.add(camera)
    test_db.commit()

    body = client.get(f"/api/v1/cameras/{camera.id}/status").json()

    assert body["reconnect_attempts"] == 0
    assert body["reconnect_state"] == "IDLE"
    assert body["stream_state"] == "NOT_STARTED"
    assert body["camera_name"] == "Never Started Cam"


# ===========================================================================
# OPERATOR STOP INTENT
# ===========================================================================


def test_operator_stop_intent_survives_a_backend_restart(monkeypatch, tmp_path):
    """A camera an operator stopped must not be resumed by the health worker."""
    marker = tmp_path / "camera_operator_stops.json"
    monkeypatch.setattr(inference_pipeline, "operator_stop_marker_path", lambda: marker)

    running = InferencePipelineManager()
    assert running.is_operator_stopped("cam-operator-stop") is False

    running.record_operator_stop("cam-operator-stop")

    assert marker.is_file()
    assert running.is_operator_stopped("cam-operator-stop") is True
    # A brand new manager stands in for a restarted backend process.
    restarted = InferencePipelineManager()
    assert restarted.is_operator_stopped("cam-operator-stop") is True

    # An operator start is the only thing that clears the intent.
    restarted.clear_operator_stop("cam-operator-stop")
    assert inference_pipeline.load_operator_stop_markers() == {}
    assert InferencePipelineManager().is_operator_stopped("cam-operator-stop") is False


def test_operator_stop_marker_holds_no_configuration(monkeypatch, tmp_path):
    """The marker records a decision, never a stream URL or a credential."""
    marker = tmp_path / "camera_operator_stops.json"
    monkeypatch.setattr(inference_pipeline, "operator_stop_marker_path", lambda: marker)

    manager = InferencePipelineManager()
    manager.record_operator_stop("cam-marker-content")

    payload = inference_pipeline.load_operator_stop_markers()
    assert set(payload) == {"cam-marker-content"}
    assert "rtsp://" not in marker.read_text(encoding="utf-8")
    assert payload["cam-marker-content"].endswith("+00:00")


def test_a_corrupt_marker_file_does_not_prevent_startup(monkeypatch, tmp_path):
    """Losing a stop record is bad; refusing to start because of it is worse."""
    marker = tmp_path / "camera_operator_stops.json"
    marker.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(inference_pipeline, "operator_stop_marker_path", lambda: marker)

    assert inference_pipeline.load_operator_stop_markers() == {}
    assert InferencePipelineManager().is_operator_stopped("cam-any") is False


def test_shutdown_does_not_erase_recorded_operator_stops(monkeypatch, tmp_path):
    """A backend restart is not an operator decision about any camera."""
    marker = tmp_path / "camera_operator_stops.json"
    monkeypatch.setattr(inference_pipeline, "operator_stop_marker_path", lambda: marker)
    monkeypatch.setattr(
        inference_pipeline.ingestion_manager, "stop_all", lambda: None, raising=False
    )

    manager = InferencePipelineManager()
    manager.record_operator_stop("cam-keep-stopped")
    manager.stop_all()

    assert manager.is_operator_stopped("cam-keep-stopped") is True
    assert InferencePipelineManager().is_operator_stopped("cam-keep-stopped") is True

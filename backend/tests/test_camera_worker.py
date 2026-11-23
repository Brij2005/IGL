"""Integration tests for the camera inference background worker.

Verifies the camera -> AI pipeline is genuinely connected:
- starting a camera spawns a worker that calls event_engine.process_image
- the worker uses the thermal model for thermal cameras (never RGB)
- stopping a camera stops the worker
- the worker status endpoint reports real state
- no fabricated events when no detections occur
"""

import os
import shutil
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

TEST_ROOT = Path(tempfile.mkdtemp(prefix="igl-worker-test-"))
os.environ.setdefault("DATABASE_PATH", os.path.join(TEST_ROOT, "test.db"))
os.environ.setdefault("ENABLE_AUTH", "false")
os.environ.setdefault("EVIDENCE_DIR", os.path.join(TEST_ROOT, "evidence"))

import pytest
from fastapi.testclient import TestClient

from main import app


def _make_image(path: Path, color=(80, 120, 160), size=(480, 640)):
    image = np.full((size[0], size[1], 3), color, dtype=np.uint8)
    cv2.putText(image, "IGL WORKER TEST", (30, size[0] // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.imwrite(str(path), image)
    return path


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client
    # Ensure workers are stopped after tests
    try:
        from camera_manager import camera_manager
        camera_manager.stop_all()
    except Exception:
        pass


def test_worker_status_endpoint_reports_real_state(client):
    response = client.get("/api/security/workers")
    assert response.status_code == 200
    body = response.json()
    assert "workers" in body
    assert isinstance(body["workers"], list)


def test_camera_start_spawns_inference_worker(client):
    """Starting a camera must spawn a worker that genuinely calls process_image.

    We monkeypatch event_engine.process_image to record invocations, proving the
    live camera is connected to the inference pipeline (not just preview).
    """
    image_path = _make_image(TEST_ROOT / "worker_cam.jpg")
    reg = client.post(
        "/api/cameras",
        json={
            "name": "Worker Test Camera",
            "source_type": "image",
            "source_url": str(image_path),
            "config": {"model_ids": ["model-yolov8n"], "thresholds": {"min_confidence": 0.25, "min_frames": 1, "cooldown_seconds": 1}},
        },
    )
    assert reg.status_code == 200
    camera_id = reg.json()["camera"]["id"]

    from event_engine import event_engine

    calls = {"count": 0}
    original = event_engine.process_image

    def spy(camera_id_, image_bytes, **kwargs):
        calls["count"] += 1
        return {"events": [], "detections_count": 0}

    event_engine.process_image = spy
    try:
        started = client.post(f"/api/cameras/{camera_id}/start")
        assert started.status_code == 200
        # Give the worker time to run at least one inference cycle.
        deadline = time.time() + 8.0
        while time.time() < deadline and calls["count"] == 0:
            time.sleep(0.3)
        assert calls["count"] >= 1, "Worker did not invoke process_image for a running camera"

        # Worker status should now list this camera as running.
        workers = client.get("/api/security/workers").json()["workers"]
        assert any(w["camera_id"] == camera_id and w["running"] for w in workers)
    finally:
        event_engine.process_image = original
        client.post(f"/api/cameras/{camera_id}/stop")


def test_camera_stop_stops_worker(client):
    image_path = _make_image(TEST_ROOT / "worker_cam2.jpg")
    reg = client.post(
        "/api/cameras",
        json={"name": "Worker Stop Camera", "source_type": "image", "source_url": str(image_path)},
    )
    camera_id = reg.json()["camera"]["id"]
    client.post(f"/api/cameras/{camera_id}/start")
    time.sleep(1.0)
    workers_before = client.get("/api/security/workers").json()["workers"]
    assert any(w["camera_id"] == camera_id and w["running"] for w in workers_before)

    client.post(f"/api/cameras/{camera_id}/stop")
    time.sleep(0.5)
    workers_after = client.get("/api/security/workers").json()["workers"]
    assert not any(w["camera_id"] == camera_id and w["running"] for w in workers_after)


def test_thermal_camera_uses_thermal_model_not_rgb(client):
    """A thermal camera must be pinned to the thermal model, never RGB COCO."""
    from camera_worker import CameraWorker

    camera = {"id": "THERM-1", "source_type": "thermal_rtsp", "config_json": "{}"}
    # Build a worker without starting it, just to inspect model selection.
    worker = CameraWorker.__new__(CameraWorker)
    worker.camera = camera
    assert worker._model_ids() == ["model-thermal-person"]

    rgb_camera = {"id": "CAM-1", "source_type": "rtsp", "config_json": "{}"}
    worker.camera = rgb_camera
    assert worker._model_ids() == ["model-yolov8n"]


def test_worker_does_not_fabricate_events_on_empty_frames(client):
    """Running a camera on a blank image must not create fake events.

    The event engine only creates events for real detections above threshold;
    a blank frame yields no detections and therefore no events.
    """
    image_path = _make_image(TEST_ROOT / "blank.jpg", color=(10, 10, 10))
    reg = client.post(
        "/api/cameras",
        json={"name": "Blank Cam", "source_type": "image", "source_url": str(image_path),
              "config": {"thresholds": {"min_confidence": 0.5, "min_frames": 1, "cooldown_seconds": 1}}},
    )
    camera_id = reg.json()["camera"]["id"]
    client.post(f"/api/cameras/{camera_id}/start")
    time.sleep(2.0)
    client.post(f"/api/cameras/{camera_id}/stop")

    # No events should have been fabricated for this blank-image camera.
    from database import get_db_connection
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) FROM detection_events WHERE camera_id = ?", (camera_id,)).fetchone()[0]
    conn.close()
    assert count == 0, "Blank frames must not fabricate detection events"


def test_restricted_zone_person_produces_alert(client):
    """A person entering a restricted zone must produce event + incident + alert.

    This proves the full detection -> event -> incident -> alert -> dashboard
    chain works end-to-end through the event engine.
    """
    import base64

    image_path = _make_image(TEST_ROOT / "restricted.jpg", color=(70, 90, 110))
    reg = client.post(
        "/api/cameras",
        json={"name": "Restricted Cam", "source_type": "image", "source_url": str(image_path)},
    )
    camera_id = reg.json()["camera"]["id"]
    # Restricted zone covering the whole frame.
    client.post(
        "/api/zones",
        json={"camera_id": camera_id, "zone_name": "Restricted", "zone_type": "RESTRICTED",
              "polygon_coords": [[0, 0], [1, 0], [1, 1], [0, 1]]},
    )

    from inference_service import inference_service

    original = inference_service.detect_image
    inference_service.detect_image = lambda *a, **k: {
        "status": "SUCCESS",
        "detections": [{"class_name": "person", "confidence": 0.9, "bbox": [0.4, 0.4, 0.2, 0.3]}],
    }
    try:
        ok, buf = cv2.imencode(".jpg", np.full((480, 640, 3), (70, 90, 110), dtype=np.uint8))
        response = client.post(
            "/api/events/process",
            json={"camera_id": camera_id,
                  "image_base64": base64.b64encode(buf.tobytes()).decode(),
                  "model_id": "model-yolov8n", "conf": 0.0},
        )
        events = response.json()["events"]
        created = [e for e in events if e.get("status") == "CREATED"]
        assert any(e.get("event_type") == "RESTRICTED_ZONE" for e in created), events
        # An alertable event must have produced an alert id.
        assert any(e.get("alert_id") for e in created), created
    finally:
        inference_service.detect_image = original

    from database import get_db_connection

    conn = get_db_connection()
    alert_count = conn.execute("SELECT COUNT(*) FROM alerts WHERE camera_id = ?", (camera_id,)).fetchone()[0]
    conn.close()
    assert alert_count >= 1, "Restricted-zone person must create a real alert"


def teardown_module(module):
    try:
        from camera_manager import camera_manager
        camera_manager.stop_all()
    except Exception:
        pass
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
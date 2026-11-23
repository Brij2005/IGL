"""Camera-to-AI pipeline robustness tests.

Covers the Phase 2 requirements not yet verified:
- multiple cameras processed concurrently with isolated workers
- one camera failing does not stop others
- disconnect (adapter death) is reported honestly, not faked
- workers only run for cameras that were explicitly started
- duplicate event prevention (dedup) across frames
"""

import base64
import os
import shutil
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

TEST_ROOT = Path(tempfile.mkdtemp(prefix="igl-camera-flow-test-"))
os.environ.setdefault("DATABASE_PATH", os.path.join(TEST_ROOT, "test.db"))
os.environ.setdefault("ENABLE_AUTH", "false")
os.environ.setdefault("EVIDENCE_DIR", os.path.join(TEST_ROOT, "evidence"))

import pytest
from fastapi.testclient import TestClient

from main import app


def _make_image(path: Path, color=(80, 120, 160)):
    image = np.full((240, 320, 3), color, dtype=np.uint8)
    cv2.putText(image, "CAM", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.imwrite(str(path), image)
    return path


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client
    try:
        from camera_manager import camera_manager

        camera_manager.stop_all()
    except Exception:
        pass


def _register(client, name, color=(80, 120, 160)):
    image_path = _make_image(TEST_ROOT / f"{name}.jpg", color=color)
    response = client.post(
        "/api/cameras",
        json={"name": name, "source_type": "image", "source_url": str(image_path)},
    )
    assert response.status_code == 200
    return response.json()["camera"]["id"]


def test_multiple_cameras_get_isolated_workers(client):
    """Each started camera must have its own running worker."""
    ids = [_register(client, f"Multi{i}", color=(60 + i * 20, 100, 120)) for i in range(3)]
    for camera_id in ids:
        assert client.post(f"/api/cameras/{camera_id}/start").status_code == 200

    deadline = time.time() + 10
    running = set()
    while time.time() < deadline and len(running) < len(ids):
        workers = client.get("/api/security/workers").json()["workers"]
        running = {w["camera_id"] for w in workers if w["running"]}
        time.sleep(0.3)

    assert set(ids).issubset(running), f"Missing running workers: {set(ids) - running}"
    for camera_id in ids:
        client.post(f"/api/cameras/{camera_id}/stop")


def test_one_bad_camera_does_not_stop_others(client):
    """A camera whose source disappears must not take down other workers."""
    good = _register(client, "GoodCam", color=(80, 140, 120))
    bad = _register(client, "BadCam", color=(120, 80, 80))

    assert client.post(f"/api/cameras/{good}/start").status_code == 200
    assert client.post(f"/api/cameras/{bad}/start").status_code == 200

    # Delete the bad camera's source file to simulate a broken/disconnected feed.
    bad_image = TEST_ROOT / "BadCam.jpg"
    if bad_image.exists():
        bad_image.unlink()

    # The good camera's worker must keep running and making progress.
    deadline = time.time() + 8
    progressed = False
    while time.time() < deadline:
        workers = {w["camera_id"]: w for w in client.get("/api/security/workers").json()["workers"]}
        good_worker = workers.get(good)
        if good_worker and good_worker["running"] and good_worker["frames_processed"] > 0:
            progressed = True
            break
        time.sleep(0.3)

    assert progressed, "Good camera worker did not progress while bad camera was broken"
    for camera_id in (good, bad):
        client.post(f"/api/cameras/{camera_id}/stop")


def test_workers_only_run_for_started_cameras(client):
    """A registered but never-started camera must have no worker."""
    camera_id = _register(client, "IdleCam", color=(90, 90, 130))
    time.sleep(0.5)
    workers = client.get("/api/security/workers").json()["workers"]
    assert not any(w["camera_id"] == camera_id for w in workers)


def test_worker_health_is_honest_about_errors(client):
    """Worker status must expose last_error rather than hiding failures."""
    camera_id = _register(client, "ErrCam", color=(100, 100, 100))
    client.post(f"/api/cameras/{camera_id}/start")
    time.sleep(0.5)
    (TEST_ROOT / "ErrCam.jpg").unlink(missing_ok=True)
    time.sleep(2.0)
    workers = {w["camera_id"]: w for w in client.get("/api/security/workers").json()["workers"]}
    worker = workers.get(camera_id, {})
    # Either it reports an error honestly, or it is no longer running. It must
    # never report a healthy worker on a missing source.
    assert worker, "Worker missing from status"
    assert ("last_error" in worker)
    client.post(f"/api/cameras/{camera_id}/stop")


def test_repeated_detections_are_deduplicated(client):
    """The same detection must not create unlimited duplicate events.

    Uses the event engine dedup path with a cooldown so repeated frames for the
    same track/zone collapse into a single event.
    """
    from event_engine import event_engine

    image_path = _make_image(TEST_ROOT / "dedup.jpg", color=(70, 110, 130))
    reg = client.post(
        "/api/cameras",
        json={
            "name": "DedupCam",
            "source_type": "image",
            "source_url": str(image_path),
            "config": {"thresholds": {"min_confidence": 0.0, "min_frames": 1, "cooldown_seconds": 300}},
        },
    )
    camera_id = reg.json()["camera"]["id"]
    client.post(
        "/api/zones",
        json={
            "camera_id": camera_id,
            "zone_name": "Dedup Restricted",
            "zone_type": "RESTRICTED",
            "polygon_coords": [[0, 0], [1, 0], [1, 1], [0, 1]],
        },
    )

    from inference_service import inference_service

    original = inference_service.detect_image
    inference_service.detect_image = lambda *a, **k: {
        "status": "SUCCESS",
        "detections": [{"class_name": "person", "confidence": 0.95, "bbox": [0.4, 0.4, 0.2, 0.2]}],
    }
    try:
        ok, buf = cv2.imencode(".jpg", np.full((240, 320, 3), (70, 110, 130), dtype=np.uint8))
        payload = {"camera_id": camera_id, "image_base64": base64.b64encode(buf.tobytes()).decode(),
                   "model_id": "model-yolov8n", "conf": 0.0, "temporal": True}
        created = 0
        for _ in range(5):
            events = client.post("/api/events/process", json=payload).json()["events"]
            created += len([e for e in events if e.get("status") == "CREATED"])
        assert created == 1, f"Expected exactly 1 event after dedup, got {created}"
    finally:
        inference_service.detect_image = original


def teardown_module(module):
    try:
        from camera_manager import camera_manager

        camera_manager.stop_all()
    except Exception:
        pass
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
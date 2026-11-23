import base64
import json
import os
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np

TEST_ROOT = Path(tempfile.mkdtemp(prefix="igl-visionguard-test-"))

import pytest
from fastapi.testclient import TestClient

from main import app
from security import create_access_token, decode_access_token, hash_password, verify_password
from video_sources import MockAdapter


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _make_image(path: Path, color=(80, 120, 160), size=(480, 640)):
    image = np.full((size[0], size[1], 3), color, dtype=np.uint8)
    cv2.putText(image, "IGL SOFTWARE TEST FRAME", (30, size[0] // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.imwrite(str(path), image)
    return path


def test_root_and_health(client):
    assert client.get("/").status_code == 200
    response = client.get("/api/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["database"]["connected"] is True
    assert "models" in body


def test_authentication_and_password_hashing():
    hashed = hash_password("test-password")
    assert verify_password("test-password", hashed)
    assert not verify_password("wrong-password", hashed)
    token = create_access_token("USR-TEST", "tester", "admin")
    payload = decode_access_token(token)
    assert payload["username"] == "tester"
    assert payload["role"] == "admin"


def test_login_and_me(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "ChangeMe-IGL-2026!"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    assert decode_access_token(token)["username"] == "admin"
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert "user" in me.json()


def test_camera_registration_zone_and_config(client):
    image_path = _make_image(TEST_ROOT / "camera.jpg")
    response = client.post(
        "/api/cameras",
        json={
            "name": "Software Test Camera",
            "source_type": "image",
            "source_url": str(image_path),
            "config": {"model_ids": ["model-yolov8n"], "thresholds": {"min_confidence": 0.25}},
        },
    )
    assert response.status_code == 200
    camera_id = response.json()["camera"]["id"]

    cameras = client.get("/api/cameras")
    assert cameras.status_code == 200
    assert any(camera["id"] == camera_id for camera in cameras.json())

    zone_response = client.post(
        "/api/zones",
        json={
            "camera_id": camera_id,
            "zone_name": "Software Test Zone",
            "zone_type": "MONITORING",
            "polygon_coords": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
            "model_ids": ["model-yolov8n"],
        },
    )
    assert zone_response.status_code == 200
    zone_id = zone_response.json()["zone_id"]

    zones = client.get(f"/api/zones?camera_id={camera_id}")
    assert zones.status_code == 200
    assert any(zone["id"] == zone_id for zone in zones.json())

    config_response = client.put(
        f"/api/cameras/{camera_id}/config",
        json={"config": {"thresholds": {"min_confidence": 0.3}}},
    )
    assert config_response.status_code == 200

    detail = client.get(f"/api/cameras/{camera_id}")
    assert detail.status_code == 200
    assert detail.json()["config"]["thresholds"]["min_confidence"] == 0.3


def test_camera_lifecycle(client):
    image_path = _make_image(TEST_ROOT / "lifecycle.jpg")
    response = client.post(
        "/api/cameras",
        json={"name": "Lifecycle Test Camera", "source_type": "image", "source_url": str(image_path)},
    )
    assert response.status_code == 200
    camera_id = response.json()["camera"]["id"]

    tested = client.post(f"/api/cameras/{camera_id}/test")
    assert tested.status_code == 200
    assert tested.json()["success"] is True

    started = client.post(f"/api/cameras/{camera_id}/start")
    assert started.status_code == 200

    frame = client.get(f"/api/cameras/{camera_id}/frame.jpg")
    assert frame.status_code == 200
    assert frame.headers["content-type"].startswith("image/")

    health = client.get(f"/api/cameras/{camera_id}/health")
    assert health.status_code == 200
    assert health.json()["running"] is True

    stopped = client.post(f"/api/cameras/{camera_id}/stop")
    assert stopped.status_code == 200


def test_real_yolo_inference_on_generated_image(client):
    image_path = _make_image(TEST_ROOT / "yolo.jpg", color=(90, 110, 130))
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    response = client.post(
        "/api/models/model-yolov8n/detect",
        json={"image_base64": encoded, "conf": 0.10, "imgsz": 640},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert "detections" in body


def test_leakage_model_is_not_fabricated(client):
    image_path = _make_image(TEST_ROOT / "leakage.jpg")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    response = client.post(
        "/api/models/model-water-leakage/detect",
        json={"image_base64": encoded},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "MODEL_UNAVAILABLE"


def test_trained_leakage_model_inference(client):
    backend_root = Path(__file__).resolve().parents[1]
    weights = backend_root / "runs" / "detect" / "models" / "model-water-leakage" / "weights" / "best.pt"
    image_dir = backend_root / "data" / "leakage" / "prepared" / "test" / "images"
    if not weights.exists() or not image_dir.exists():
        pytest.skip("Trained leakage weights or dataset test images are not present")
    images = sorted(image_dir.glob("*.jpg"))
    if not images:
        pytest.skip("No leakage test images available")
    from database import get_db_connection

    conn = get_db_connection()
    conn.execute(
        "UPDATE ai_models SET status = 'AVAILABLE', weights_path = ?, updated_at = ? WHERE id = 'model-water-leakage'",
        (str(weights), "2026-11-21T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    try:
        reload_response = client.post("/api/models/model-water-leakage/reload")
        assert reload_response.status_code == 200
        assert reload_response.json()["loaded"] is True
        encoded = base64.b64encode(images[0].read_bytes()).decode("ascii")
        response = client.post(
            "/api/models/model-water-leakage/detect",
            json={"image_base64": encoded, "conf": 0.10, "imgsz": 320},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "SUCCESS"
        assert "detections" in body
        assert body["detections_count"] >= 0
    finally:
        conn = get_db_connection()
        conn.execute(
            "UPDATE ai_models SET status = 'NOT_TRAINED', weights_path = NULL, updated_at = ? WHERE id = 'model-water-leakage'",
            ("2026-11-21T00:00:00+00:00",),
        )
        conn.commit()
        conn.close()
        from inference_service import inference_service

        inference_service.invalidate_model("model-water-leakage")


def test_event_pipeline_accepts_real_image(client):
    image_path = _make_image(TEST_ROOT / "event.jpg")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    cameras = client.get("/api/cameras").json()
    assert cameras
    response = client.post(
        "/api/events/process",
        json={
            "camera_id": cameras[0]["id"],
            "image_base64": encoded,
            "model_id": "model-yolov8n",
            "conf": 0.25,
            "temporal": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "events" in body
    assert "detections_count" in body


def test_notification_failure_is_not_reported_as_success(client):
    response = client.post(
        "/api/notifications/test",
        json={"channels": ["SMS"]},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["status"] == "NOT_CONFIGURED"
    assert results[0]["success"] is False


def test_report_generation_and_download(client):
    response = client.post("/api/reports", json={"report_type": "daily", "format": "json"})
    assert response.status_code == 200
    report = response.json()
    assert report["report_id"]
    detail = client.get(f"/api/reports/{report['report_id']}")
    assert detail.status_code == 200
    download = client.get(f"/api/reports/{report['report_id']}/download")
    assert download.status_code == 200


def test_mock_video_adapter_is_test_only():
    adapter = MockAdapter("mock://integration-test", {"color": (10, 20, 30), "size": 240})
    frame = adapter.read()
    assert frame.shape == (240, 240, 3)
    adapter.release()


def test_evidence_upload(client):
    image_path = _make_image(TEST_ROOT / "evidence.jpg")
    response = client.post(
        "/api/uploads/evidence",
        files={"file": ("evidence.jpg", image_path.read_bytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    evidence_id = response.json()["evidence_id"]
    fetched = client.get(f"/api/evidence/{evidence_id}")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"].startswith("image/")


def test_model_reload_endpoint(client):
    response = client.post("/api/models/model-yolov8n/reload")
    assert response.status_code == 200
    body = response.json()
    assert body["model_id"] == "model-yolov8n"
    assert body["loaded"] is True


def test_video_evaluation_requires_available_model(client):
    video_path = TEST_ROOT / "test_video.mp4"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"mp4v"), 5.0, (128, 96))
    for index in range(5):
        frame = np.full((96, 128, 3), (30 * index, 60, 90), dtype=np.uint8)
        writer.write(frame)
    writer.release()
    response = client.post(
        "/api/models/model-water-leakage/evaluate-video",
        files={"file": ("test_video.mp4", video_path.read_bytes(), "video/mp4")},
        data={"conf": "0.25", "max_frames": "5"},
    )
    assert response.status_code == 409
    assert "not trained" in response.json()["detail"].lower()


def test_dataset_prepare_endpoint_requires_dataset_root(client):
    response = client.post("/api/datasets/prepare", json={})
    assert response.status_code == 422


def test_event_dedup_handles_utc_sqlite_timestamps(client):
    from datetime import datetime, timezone
    from database import get_db_connection
    from event_engine import event_engine

    camera_response = client.post(
        "/api/cameras",
        json={"name": "Dedup Test Camera", "source_type": "image", "source_url": str(TEST_ROOT / "camera.jpg")},
    )
    camera_id = camera_response.json()["camera"]["id"]
    dedup_key = f"{camera_id}:WATER_LEAKAGE::water accumulation"
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO detection_events
        (id, camera_id, event_type, severity, status, confidence, dedup_key, created_at)
        VALUES (?, ?, 'WATER_LEAKAGE', 'MEDIUM', 'DETECTED', 0.4, ?, ?)
        """,
        (f"EVT-{camera_id[-4:]}", camera_id, dedup_key, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    assert event_engine._duplicate(camera_id, "WATER_LEAKAGE", None, dedup_key, 60) is True


def teardown_module(module):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
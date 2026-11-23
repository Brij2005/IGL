"""Module integration tests: Water Leakage, Smart Entry & Exit, Thermal, Pipeline.

All run against the isolated test database. No fabricated detections: where a
model is unavailable the tests assert honest unavailability.
"""

import base64
import os
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np

TEST_ROOT = Path(tempfile.mkdtemp(prefix="igl-modules-test-"))
os.environ.setdefault("DATABASE_PATH", os.path.join(TEST_ROOT, "test.db"))
os.environ.setdefault("ENABLE_AUTH", "false")
os.environ.setdefault("EVIDENCE_DIR", os.path.join(TEST_ROOT, "evidence"))

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _blank_image_bytes():
    image = np.full((240, 320, 3), (60, 60, 60), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", image)
    return base64.b64encode(buf.tobytes()).decode()


# ----------------------------------------------------------- Water Leakage
def test_leakage_model_registry_and_versions(client):
    """Leakage model must be registered with a version history available."""
    models = {m["id"]: m for m in client.get("/api/models").json()}
    leakage = models.get("model-water-leakage")
    assert leakage, "Leakage model must be registered"
    assert "0" in leakage["classes"] or leakage["classes"], "Leakage classes must be declared"
    versions = client.get("/api/models/model-water-leakage/versions")
    # A freshly seeded test DB has no trained versions; that must be reported
    # honestly as 404 rather than fabricating versions.
    assert versions.status_code in (200, 404)


def test_leakage_activation_requires_registered_version(client):
    """Version activation must reject unknown versions instead of guessing."""
    response = client.post(
        "/api/models/model-water-leakage/activate-version",
        json={"version": "999"},
    )
    assert response.status_code == 404, "Unknown version must be rejected"
    missing = client.post("/api/models/model-water-leakage/activate-version", json={})
    assert missing.status_code == 422, "Missing version must be rejected"


def test_leakage_activation_rolls_back_when_versions_exist(client):
    """When versions are registered, activation switches weights_path.

    Seeds two synthetic version rows so the rollback mechanics can be verified
    without fabricating any detections or model output.
    """
    from database import get_db_connection

    conn = get_db_connection()
    conn.execute(
        "UPDATE ai_models SET weights_path = 'model-water-leakage/v3/best.pt', active_version = '3', status = 'AVAILABLE' WHERE id = 'model-water-leakage'"
    )
    for version in ("1", "2", "3"):
        conn.execute(
            """
            INSERT OR IGNORE INTO model_versions (id, model_id, version, weights_path, status)
            VALUES (?, 'model-water-leakage', ?, ?, 'COMPLETED')
            """,
            (f"MV-TEST-{version}", version, f"model-water-leakage/v{version}/best.pt"),
        )
    conn.commit()
    conn.close()

    response = client.post("/api/models/model-water-leakage/activate-version", json={"version": "1"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == "1"
    assert body["previous_version"] == "3", "Rollback must report the version it moved away from"
    assert body["weights_path"] == "model-water-leakage/v1/best.pt"

    # Persisted, not just echoed back.
    conn = get_db_connection()
    row = conn.execute("SELECT active_version, weights_path FROM ai_models WHERE id='model-water-leakage'").fetchone()
    conn.close()
    assert row["active_version"] == "1"
    assert row["weights_path"] == "model-water-leakage/v1/best.pt"


# --------------------------------------------------- Smart Entry & Exit
def test_entry_exit_tracking_creates_access_records_with_evidence(client):
    from inference_service import inference_service
    from security_monitoring import security_service

    # Direction classification requires configured gate zones; without a gate no
    # direction can be derived (honest behavior, not a fabricated record).
    from database import get_db_connection
    import json as _json

    gate = {
        "gate_id": "GATE-ENTRY",
        "camera_id": "CAM-ENTRY",
        "entry_zone": {"x": 0.0, "y": 0.6, "w": 1.0, "h": 0.4},
        "exit_zone": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.4},
    }
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        ("security_gates", _json.dumps([gate]), "2026-11-22T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    security_service._tracker._last_zone.clear()
    security_service._event_engine.tracker.tracks.clear()

    original = inference_service.detect_image
    calls = {"n": 0}

    def fake_detect(image_bytes, model_id="model-yolov8n", conf=0.25, imgsz=1280):
        step = min(calls["n"], 6)
        cy = 0.2 + (0.6 * step / 6.0)
        calls["n"] += 1
        return {"status": "SUCCESS", "detections": [
            {"class_name": "person", "confidence": 0.9, "bbox": [0.4, cy - 0.1, 0.2, 0.2]}
        ]}

    inference_service.detect_image = fake_detect
    try:
        frames = [np.full((200, 200, 3), (60 + 5 * i, 60, 60), dtype=np.uint8) for i in range(8)]
        result = security_service.analyze_recorded_video(
            filename="entry_exit.mp4", frames=frames, source_kind="cctv", camera_id="CAM-ENTRY"
        )
    finally:
        inference_service.detect_image = original

    assert result["frames_processed"] == 8
    assert result["entry_count"] + result["exit_count"] == result["events_created"]

    conn = get_db_connection()
    rows = conn.execute("SELECT id, evidence_id, verification_status FROM access_records WHERE camera_id='CAM-ENTRY'").fetchall()
    conn.close()
    assert rows, "Direction crossing must create an access record"
    assert all(r["evidence_id"] for r in rows), "Each access record must carry evidence"
    assert all(r["verification_status"] == "UNVERIFIED" for r in rows), \
        "AI-only records must be UNVERIFIED (never auto-approved)"


def test_access_record_human_review(client):
    created = client.post(
        "/api/security/access-records",
        json={"direction": "ENTRY", "camera_id": "CAM-REV", "gate_id": "GATE-1"},
    ).json()
    assert created["verification_status"] == "UNVERIFIED"

    reviewed = client.put(
        f"/api/security/access-records/{created['id']}/review",
        json={"decision": "VERIFIED", "notes": "Badge checked at gate"},
    ).json()
    assert reviewed["decision"] == "VERIFIED"

    conn_rows = client.get("/api/security/access-records").json()
    row = next(r for r in conn_rows if r["id"] == created["id"])
    assert row["verification_status"] == "VERIFIED"
    assert row["reviewed_by"], "Review must record the reviewer"


# ------------------------------------------------------------- Thermal
def test_thermal_model_is_not_trained_and_never_used_for_detections(client):
    status = client.get("/api/models/model-thermal-person/health").json()
    assert status["status"] == "NOT_TRAINED"
    assert status["loaded"] is False

    detect = client.post(
        "/api/models/model-thermal-person/detect", json={"image_base64": _blank_image_bytes()}
    ).json()
    assert detect["status"] == "MODEL_UNAVAILABLE"
    assert detect.get("detections") == []


def test_thermal_camera_uses_thermal_model_only(client):
    """RGB weights must never be auto-applied to a thermal source."""
    from camera_worker import CameraWorker

    thermal = {"id": "T1", "source_type": "thermal", "config_json": "{}"}
    worker = CameraWorker.__new__(CameraWorker)
    worker.camera = thermal
    assert worker._model_ids() == ["model-thermal-person"]

    # Even if someone configures RGB model ids, thermal must stay pinned.
    thermal_forced = {"id": "T2", "source_type": "thermal_rtsp",
                      "config_json": '{"model_ids": ["model-yolov8n"]}'}
    worker.camera = thermal_forced
    assert worker._model_ids() == ["model-thermal-person"], "Thermal must not fall back to RGB"


def test_thermal_temperature_requires_calibration(client):
    uncalibrated = client.post(
        "/api/security/thermal/readings",
        json={"camera_id": "T1", "temperature_c": 38.0, "threshold_c": 37.0, "calibrated": False},
    )
    assert uncalibrated.status_code == 422, "Uncalibrated temperature must be rejected"

    calibrated = client.post(
        "/api/security/thermal/readings",
        json={"camera_id": "T1", "temperature_c": 38.0, "threshold_c": 37.0, "calibrated": True},
    ).json()
    assert calibrated["threshold_exceeded"] is True

    readings = client.get("/api/security/thermal/readings").json()
    assert any(r["calibrated"] for r in readings)


# --------------------------------------------------- Pipeline inspection
def test_plant_and_pipeline_mapping(client):
    locations = client.get("/api/plant-locations").json()
    assert isinstance(locations, list)
    pipelines = client.get("/api/pipelines").json()
    assert isinstance(pipelines, list)
    # Camera-to-pipeline mapping is persisted through camera config.
    image_path = TEST_ROOT / "pipe.jpg"
    cv2.imwrite(str(image_path), np.full((120, 160, 3), (50, 60, 70), dtype=np.uint8))
    cam = client.post(
        "/api/cameras",
        json={"name": "Pipe Cam", "source_type": "image", "source_url": str(image_path),
              "plant_location_id": locations[0]["id"] if locations else None,
              "pipeline_id": pipelines[0]["id"] if pipelines else None},
    ).json()["camera"]["id"]
    detail = client.get(f"/api/cameras/{cam}").json()
    assert detail["pipeline_id"] == (pipelines[0]["id"] if pipelines else None)


def test_analytics_empty_state_is_honest(client):
    body = client.get("/api/analytics").json()
    assert "has_data" in body
    if not body.get("has_data"):
        assert body.get("event_breakdown") == []
        assert body.get("message"), "Empty analytics must explain itself"


def teardown_module(module):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
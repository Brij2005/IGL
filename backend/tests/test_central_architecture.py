"""Tests for the centralized AI architecture.

Covers:
- model_registry capability + compatibility rules
- scene_analyzer on real synthetic frames (water/rust/skin/general/thermal)
- mode_manager Automatic vs Manual selection, availability filtering,
  thermal safety, and scene-switch hysteresis
- orchestrator payloads (Model Hub, Camera Command Centre, Mapping)
- central API endpoints (/api/ai/*)
"""

import base64
import json
import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

TEST_ROOT = Path(tempfile.mkdtemp(prefix="igl-central-arch-test-"))
os.environ.setdefault("DATABASE_PATH", os.path.join(TEST_ROOT, "test.db"))
os.environ.setdefault("ENABLE_AUTH", "false")
os.environ.setdefault("EVIDENCE_DIR", os.path.join(TEST_ROOT, "evidence"))

from main import app  # noqa: E402


def _encode(image) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


def _water_image():
    # Realistic water: saturated blue with ripples/edges (not a flat fill).
    rng = np.random.default_rng(7)
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    image[:, :] = (200, 140, 50)
    for i in range(60):
        y = int(rng.integers(0, 240))
        x = int(rng.integers(0, 320))
        color = (220, 170, 70) if i % 2 else (180, 110, 30)
        cv2.ellipse(image, (x, y), (30, 8), 0, 0, 360, color, 2)
    cv2.rectangle(image, (0, 0), (320, 30), (90, 90, 90), -1)
    return image


def _rust_image():
    # Realistic rust: high-saturation warm with flaking texture.
    rng = np.random.default_rng(9)
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    image[:, :] = (60, 100, 170)
    for _ in range(80):
        x = int(rng.integers(0, 320))
        y = int(rng.integers(0, 240))
        shade = int(rng.integers(40, 200))
        cv2.circle(image, (x, y), int(rng.integers(2, 10)), (shade // 2, shade * 2 // 3, shade), -1)
    cv2.rectangle(image, (40, 40), (280, 200), (40, 70, 140), 3)
    return image


def _flat_image(bgr=(90, 90, 90)):
    # A flat painted surface -> must fall back to GENERAL (no false scene).
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    image[:, :] = bgr
    return image


def _general_image():
    image = np.full((240, 320, 3), (90, 90, 90), dtype=np.uint8)
    cv2.putText(image, "IGL", (120, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    return image


@pytest.fixture(scope="module")
def client():
    from database import init_db

    init_db()
    with TestClient(app) as test_client:
        yield test_client
    try:
        from camera_manager import camera_manager

        camera_manager.stop_all()
    except Exception:
        pass


# --------------------------------------------------------------- model_registry
def test_registry_capabilities_present():
    from model_registry import MODEL_CAPABILITIES, describe_registry, input_family_for_source

    assert "model-yolov8n" in MODEL_CAPABILITIES
    assert "model-water-leakage" in MODEL_CAPABILITIES
    assert "model-thermal-person" in MODEL_CAPABILITIES
    assert input_family_for_source("thermal_rtsp") == "THERMAL"
    assert input_family_for_source("rtsp") == "RGB"
    registry = describe_registry()
    assert len(registry) >= 4


def test_compatibility_rules():
    from model_registry import is_candidate_for_scene, supports_input

    # Thermal model only on thermal input.
    assert supports_input("model-thermal-person", "THERMAL")
    assert not supports_input("model-thermal-person", "RGB")
    # RGB models not on thermal input.
    assert not supports_input("model-yolov8n", "THERMAL")
    assert not supports_input("model-water-leakage", "THERMAL")
    # Water model is a candidate for WATER scene on RGB.
    assert is_candidate_for_scene("model-water-leakage", "WATER", "RGB")
    # General model is a candidate everywhere on RGB.
    assert is_candidate_for_scene("model-yolov8n", "CORROSION", "RGB")


# --------------------------------------------------------------- scene_analyzer
def test_scene_analyzer_water():
    from scene_analyzer import analyze_frame

    result = analyze_frame(_encode(_water_image()), "rtsp")
    assert result["scene"] == "WATER"
    assert result["input_family"] == "RGB"


def test_scene_analyzer_rust():
    from scene_analyzer import analyze_frame

    result = analyze_frame(_encode(_rust_image()), "rtsp")
    assert result["scene"] == "CORROSION"
    assert result["input_family"] == "RGB"


def test_scene_analyzer_ordinary_flat_surfaces_fall_back_to_general():
    """Bright walls, cupboards, tan walls and skin-toned flat fills must NOT be
    misclassified as CORROSION/FACE/WATER. They fall back to GENERAL."""
    from scene_analyzer import analyze_frame

    for bgr in [(245, 245, 245), (128, 128, 128), (170, 200, 230), (60, 110, 160), (150, 180, 210), (40, 210, 230)]:
        result = analyze_frame(_encode(_flat_image(bgr)), "rtsp")
        assert result["scene"] == "GENERAL", f"bgr={bgr} misclassified as {result['scene']}"


def test_scene_analyzer_sky_and_monitor_do_not_trigger_water():
    """A blue sky through a window and a flat blue monitor are not water."""
    from scene_analyzer import analyze_frame

    for bgr in [(230, 160, 60), (200, 120, 30)]:
        result = analyze_frame(_encode(_flat_image(bgr)), "rtsp")
        assert result["scene"] == "GENERAL", f"bgr={bgr} misclassified as {result['scene']}"


def test_scene_analyzer_thermal_source_is_authoritative():
    from scene_analyzer import analyze_frame

    result = analyze_frame(_encode(_general_image()), "thermal_rtsp")
    assert result["scene"] == "THERMAL"
    assert result["is_thermal"] is True
    assert result["input_family"] == "THERMAL"


def test_scene_analyzer_general_fallback():
    from scene_analyzer import analyze_frame

    result = analyze_frame(_encode(_general_image()), "rtsp")
    assert result["scene"] == "GENERAL"
    assert result["confidence"] <= 0.5


def test_scene_analyzer_handles_empty_and_bad_bytes():
    from scene_analyzer import analyze_frame

    empty = analyze_frame(b"", "rtsp")
    assert empty["scene"] == "GENERAL"
    bad = analyze_frame(b"not-an-image", "rtsp")
    assert bad["scene"] == "GENERAL"


def test_scene_classification_independent_of_camera_name():
    """Identical frames must classify identically regardless of any camera name.

    The analyzer takes only image bytes + source_type; it never sees a camera
    name. This proves scene classification is content-driven, not name-driven.
    """
    from scene_analyzer import analyze_frame

    frame = _encode(_water_image())
    a = analyze_frame(frame, "rtsp")
    b = analyze_frame(frame, "rtsp")
    assert a["scene"] == b["scene"] == "WATER"


# ----------------------------------------------------------------- mode_manager
def test_automatic_mode_is_default_and_selects_available(client):
    from database import get_db_connection
    from inference_service import inference_service
    from mode_manager import MODE_AUTOMATIC, mode_manager

    # Ensure at least one non-yolo AVAILABLE model so we can prove scene-based
    # selection picks up more than the general model. In the test database the
    # water-leakage weights are not present, so mark it AVAILABLE with the real
    # yolov8n weights to make it loadable and selectable.
    conn = get_db_connection()
    conn.execute(
        "UPDATE ai_models SET status='AVAILABLE', weights_path='yolov8n.pt' WHERE id='model-water-leakage'"
    )
    conn.execute(
        "INSERT OR REPLACE INTO cameras (id, name, source_type, source_url, status, config_json, created_at, updated_at) "
        "VALUES ('CAM-MODE-1','ModeCam','rtsp','','DISCONNECTED','{}','2026-01-01','2026-01-01')"
    )
    conn.commit()
    row = conn.execute("SELECT * FROM cameras WHERE id='CAM-MODE-1'").fetchone()
    conn.close()
    inference_service.invalidate_model("model-water-leakage")

    mode_manager.reset("CAM-MODE-1")
    camera = dict(row)
    assert mode_manager.get_mode(camera) == MODE_AUTOMATIC

    # Automatic on a WATER scene RGB camera should select AVAILABLE candidate
    # models: yolov8n (general, candidate everywhere) + water-leakage (water).
    # The untrained thermal/corrosion models must NOT be selected.
    decision = mode_manager.select_models(camera, "WATER", 0.9)
    assert "model-water-leakage" in decision["model_ids"]
    assert "model-yolov8n" in decision["model_ids"]
    assert "model-thermal-person" not in decision["model_ids"]  # untrained + input
    assert "model-pipe-corrosion" not in decision["model_ids"]  # untrained


def test_manual_mode_filters_unavailable_and_incompatible(client):
    from database import get_db_connection
    from inference_service import inference_service
    from mode_manager import mode_manager

    # Make water-leakage AVAILABLE+loadable for the test (real weights absent).
    conn = get_db_connection()
    conn.execute(
        "UPDATE ai_models SET status='AVAILABLE', weights_path='yolov8n.pt' WHERE id='model-water-leakage'"
    )
    conn.execute(
        "INSERT OR REPLACE INTO cameras (id, name, source_type, source_url, status, config_json, created_at, updated_at) "
        "VALUES ('CAM-MODE-2','ManualCam','rtsp','','DISCONNECTED','{}','2026-01-01','2026-01-01')"
    )
    # Configure a manual list that includes an untrained model + a thermal model
    # on an RGB camera (incompatible) + a valid available model.
    config = {
        "mode": "MANUAL",
        "model_ids": ["model-water-leakage", "model-thermal-person", "model-pipe-corrosion"],
    }
    conn.execute("UPDATE cameras SET config_json = ? WHERE id = 'CAM-MODE-2'", (json.dumps(config),))
    conn.commit()
    row = conn.execute("SELECT * FROM cameras WHERE id = 'CAM-MODE-2'").fetchone()
    conn.close()
    inference_service.invalidate_model("model-water-leakage")

    decision = mode_manager.select_models(dict(row), "WATER", 0.9)
    assert decision["mode"] == "MANUAL"
    assert "model-water-leakage" in decision["model_ids"]
    assert "model-thermal-person" not in decision["model_ids"]  # untrained + incompatible
    assert "model-pipe-corrosion" not in decision["model_ids"]  # untrained
    rejected_ids = {r["model_id"]: r["reason"] for r in decision["rejected"]}
    assert rejected_ids.get("model-thermal-person") in {"MODEL_UNAVAILABLE", "INPUT_INCOMPATIBLE"}
    assert rejected_ids.get("model-pipe-corrosion") == "MODEL_UNAVAILABLE"


def test_thermal_camera_never_gets_rgb_models_in_automatic(client):
    from mode_manager import mode_manager

    camera = {"id": "CAM-THERM-AUTO", "source_type": "thermal_rtsp", "config_json": "{}"}
    decision = mode_manager.select_models(camera, "THERMAL", 1.0)
    # Only the thermal model could ever be selected for a thermal input, and it
    # is untrained so nothing is selected at all.
    assert decision["model_ids"] == []
    assert decision["input_family"] == "THERMAL"


def test_scene_switch_hysteresis(client):
    from mode_manager import mode_manager

    camera = {"id": "CAM-HYS", "source_type": "rtsp", "config_json": "{}"}
    mode_manager.reset("CAM-HYS")

    # First evaluation establishes GENERAL.
    mode_manager.evaluate(camera, "GENERAL", 0.9, force=True)
    first = mode_manager.last_decision("CAM-HYS")
    assert first["scene"] == "GENERAL"

    # A single WATER frame must NOT switch away from GENERAL (hysteresis).
    mode_manager.evaluate(camera, "WATER", 0.9, force=True)
    still = mode_manager.last_decision("CAM-HYS")
    assert still["scene"] == "GENERAL"

    # After the required stable frames, the scene switches to WATER.
    from mode_manager import STABLE_SCENES_REQUIRED

    for _ in range(STABLE_SCENES_REQUIRED):
        mode_manager.evaluate(camera, "WATER", 0.9, force=True)
    switched = mode_manager.last_decision("CAM-HYS")
    assert switched["scene"] == "WATER"


# ------------------------------------------------------------------ orchestrator
def test_orchestrator_model_hub(client):
    from orchestrator import model_hub

    hub = model_hub()
    assert hub["summary"]["total"] >= 4
    ids = {m["model_id"] for m in hub["models"]}
    assert "model-yolov8n" in ids
    assert "model-water-leakage" in ids
    # yolov8n must be AVAILABLE and loaded-capable.
    yolo = next(m for m in hub["models"] if m["model_id"] == "model-yolov8n")
    assert yolo["status"] == "AVAILABLE"


def test_orchestrator_camera_command_center(client):
    from orchestrator import camera_command_center

    center = camera_command_center()
    assert "cameras" in center and "summary" in center
    assert center["summary"]["registered"] >= 1
    for cam in center["cameras"]:
        assert "scene" in cam
        assert "active_model_ids" in cam
        assert "mode" in cam


def test_orchestrator_mapping(client):
    from orchestrator import model_camera_mapping

    mapping = model_camera_mapping()
    assert mapping["summary"]["cameras"] >= 1
    for m in mapping["mappings"]:
        assert m["mode"] in {"AUTOMATIC", "MANUAL"}
        assert "active_models" in m


# ---------------------------------------------------------------- API endpoints
def test_api_model_hub(client):
    response = client.get("/api/ai/model-hub")
    assert response.status_code == 200
    body = response.json()
    assert "models" in body
    assert body["summary"]["total"] >= 4


def test_api_camera_command_center(client):
    response = client.get("/api/ai/cameras")
    assert response.status_code == 200
    body = response.json()
    assert "cameras" in body


def test_api_mapping_and_detections(client):
    assert client.get("/api/ai/model-camera-mapping").status_code == 200
    assert client.get("/api/ai/detections").status_code == 200


def test_api_mode_switch_roundtrip(client):
    # Create a camera via the legacy endpoint.
    reg = client.post(
        "/api/cameras",
        json={"name": "Mode Switch Cam", "source_type": "rtsp", "source_url": ""},
    )
    assert reg.status_code == 200
    camera_id = reg.json()["camera"]["id"]

    # Default mode is AUTOMATIC.
    mode_resp = client.get(f"/api/ai/cameras/{camera_id}/mode")
    assert mode_resp.status_code == 200
    assert mode_resp.json()["mode"] == "AUTOMATIC"

    # Switch to MANUAL.
    set_resp = client.put(f"/api/ai/cameras/{camera_id}/mode", json={"mode": "MANUAL"})
    assert set_resp.status_code == 200
    assert set_resp.json()["mode"] == "MANUAL"

    # Pin manual models (includes an untrained model that must be rejected at run).
    pin = client.put(
        f"/api/ai/cameras/{camera_id}/manual-models",
        json={"model_ids": ["model-water-leakage", "model-thermal-person"]},
    )
    assert pin.status_code == 200

    # Back to AUTOMATIC.
    auto = client.put(f"/api/ai/cameras/{camera_id}/mode", json={"mode": "AUTOMATIC"})
    assert auto.status_code == 200
    assert auto.json()["mode"] == "AUTOMATIC"

    # Invalid mode rejected.
    bad = client.put(f"/api/ai/cameras/{camera_id}/mode", json={"mode": "BOGUS"})
    assert bad.status_code == 422


def test_api_mode_404_for_missing_camera(client):
    resp = client.put("/api/ai/cameras/CAM-DOES-NOT-EXIST/mode", json={"mode": "AUTOMATIC"})
    assert resp.status_code == 404


def teardown_module(module):
    try:
        from camera_manager import camera_manager

        camera_manager.stop_all()
    except Exception:
        pass
    import shutil

    shutil.rmtree(TEST_ROOT, ignore_errors=True)
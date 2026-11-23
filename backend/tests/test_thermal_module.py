"""Thermal camera AI, radiometric ingestion and calibration tests.

Verifies honest behaviour:
- RGB images are never treated as thermal
- uncalibrated or malformed radiometric data is refused with a reason
- temperatures are computed from real grid values, never invented
- the thermal model stays NOT_TRAINED and produces no detections
- the thermal hardware adapter never fakes a connection
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np

TEST_ROOT = Path(tempfile.mkdtemp(prefix="igl-thermal-test-"))
os.environ.setdefault("DATABASE_PATH", os.path.join(TEST_ROOT, "test.db"))
os.environ.setdefault("ENABLE_AUTH", "false")

import pytest
from fastapi.testclient import TestClient

from hardware_adapters import ThermalCameraHardwareAdapter, create_hardware_adapter
from database import init_db

init_db()

from main import app
from thermal_ingestion import (
    ThermalDataError,
    inspect_thermal_file,
    load_radiometric_array,
    record_thermal_reading,
    validate_calibration,
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _calibrated_payload(celsius, **overrides):
    payload = {
        "sensor_model": "TestThermalCam",
        "calibration_date": "2026-01-01",
        "emissivity": 0.95,
        "width": len(celsius[0]),
        "height": len(celsius),
        "celsius": celsius,
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------- ingestion validation
def test_rgb_image_rejected_as_thermal():
    path = TEST_ROOT / "rgb.jpg"
    cv2.imwrite(str(path), np.full((64, 64, 3), (120, 60, 60), dtype=np.uint8))
    with pytest.raises(ThermalDataError) as exc:
        load_radiometric_array(str(path))
    assert "not a thermal source" in str(exc.value)


def test_unsupported_format_rejected():
    path = TEST_ROOT / "thermal.txt"
    path.write_text("36.5", encoding="utf-8")
    with pytest.raises(ThermalDataError):
        load_radiometric_array(str(path))


def test_ragged_grid_rejected():
    path = TEST_ROOT / "ragged.json"
    path.write_text(json.dumps({"celsius": [[36.0, 36.1], [36.2]]}), encoding="utf-8")
    with pytest.raises(ThermalDataError):
        load_radiometric_array(str(path))


def test_temperature_is_computed_not_invented():
    grid = [[36.0, 36.0], [38.0, 38.0]]
    path = TEST_ROOT / "grid.json"
    path.write_text(json.dumps(_calibrated_payload(grid)), encoding="utf-8")
    info = load_radiometric_array(str(path))
    assert info["mean_c"] == pytest.approx(37.0)
    assert info["min_c"] == 36.0
    assert info["max_c"] == 38.0
    assert info["width"] == 2 and info["height"] == 2


# ---------------------------------------------------------- calibration rules
def test_calibration_requires_complete_record():
    assert validate_calibration({})["calibrated"] is False
    assert validate_calibration({"sensor_model": "X"})["calibrated"] is False
    bad_emissivity = _calibrated_payload([[36.0]], emissivity=1.5)
    assert validate_calibration(bad_emissivity)["calibrated"] is False
    good = validate_calibration(_calibrated_payload([[36.0]]))
    assert good["calibrated"] is True
    assert good["emissivity"] == 0.95


def test_unusable_temperature_refuses_to_store():
    grid = [[36.0, 36.1]]
    path = TEST_ROOT / "uncal.json"
    path.write_text(json.dumps({"celsius": grid}), encoding="utf-8")
    with pytest.raises(ThermalDataError) as exc:
        record_thermal_reading(str(path), "THERM-1")
    assert "calibration" in str(exc.value).lower()


def test_implausible_temperatures_refused():
    grid = [[900.0, 900.0]]
    path = TEST_ROOT / "hot.json"
    path.write_text(json.dumps(_calibrated_payload(grid)), encoding="utf-8")
    with pytest.raises(ThermalDataError) as exc:
        record_thermal_reading(str(path), "THERM-1")
    assert "plausible" in str(exc.value).lower()


def test_calibrated_reading_persists_with_metadata():
    grid = [[36.4, 36.6], [37.2, 38.9]]
    path = TEST_ROOT / "ok.json"
    path.write_text(json.dumps(_calibrated_payload(grid)), encoding="utf-8")
    result = record_thermal_reading(str(path), "THERM-TEST", threshold_c=37.5)
    assert result["temperature_c"] == pytest.approx(37.27, abs=0.01)
    assert result["calibrated"] is True
    assert result["threshold_exceeded"] is False
    assert result["sensor_model"] == "TestThermalCam"

    from database import get_db_connection

    conn = get_db_connection()
    row = conn.execute(
        "SELECT temperature_c, calibrated, metadata_json FROM thermal_readings WHERE id = ?",
        (result["id"],),
    ).fetchone()
    conn.close()
    assert row["calibrated"] == 1
    assert "TestThermalCam" in row["metadata_json"]


def test_threshold_breach_is_flagged():
    grid = [[36.0, 36.0], [39.5, 39.5]]
    path = TEST_ROOT / "breach.json"
    path.write_text(json.dumps(_calibrated_payload(grid)), encoding="utf-8")
    result = record_thermal_reading(str(path), "THERM-TEST", threshold_c=37.5)
    assert result["threshold_exceeded"] is True


# ------------------------------------------------------------- API behaviour
def test_api_rejects_rgb_and_uncalibrated(client):
    ok, buf = cv2.imencode(".jpg", np.full((64, 64, 3), (100, 50, 50), dtype=np.uint8))
    rgb = client.post(
        "/api/security/thermal/ingest",
        files={"file": ("t.jpg", buf.tobytes(), "image/jpeg")},
        data={"camera_id": "THERM-API"},
    )
    assert rgb.status_code == 422

    csv_body = "30.0,30.1\n30.2,30.3\n"
    uncal = client.post(
        "/api/security/thermal/ingest",
        files={"file": ("t.csv", csv_body.encode(), "text/csv")},
        data={"camera_id": "THERM-API"},
    )
    assert uncal.status_code == 422
    assert "calibration" in uncal.json()["detail"].lower()


def test_api_accepts_calibrated_radiometric(client):
    grid = [[36.0, 36.2], [36.4, 36.6]]
    response = client.post(
        "/api/security/thermal/ingest",
        files={"file": ("t.json", json.dumps(_calibrated_payload(grid)).encode(), "application/json")},
        data={"camera_id": "THERM-API", "threshold_c": "36.3"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["temperature_c"] == pytest.approx(36.3, abs=0.01)
    assert body["calibrated"] is True
    assert body["threshold_exceeded"] is True


def test_thermal_status_is_honest(client):
    body = client.get("/api/security/thermal/status").json()
    assert body["device"]["connected"] is False, "No thermal device may be faked as connected"
    assert body["model"]["status"] == "NOT_TRAINED"
    assert body["model"]["available"] is False
    assert "note" in body


def test_thermal_model_produces_no_detections(client):
    ok, buf = cv2.imencode(".jpg", np.full((64, 64, 3), (90, 90, 90), dtype=np.uint8))
    import base64

    body = client.post(
        "/api/models/model-thermal-person/detect",
        json={"image_base64": base64.b64encode(buf.tobytes()).decode()},
    ).json()
    assert body["status"] == "MODEL_UNAVAILABLE"
    assert body.get("detections") == []


# --------------------------------------------------------- hardware adapter
def test_thermal_adapter_never_fakes_connection():
    adapter = create_hardware_adapter("thermal_camera", {})
    assert isinstance(adapter, ThermalCameraHardwareAdapter)
    result = adapter.connect()
    assert result["connected"] is False, "No endpoint must not produce a fake connection"
    assert adapter.status()["connected"] is False


def test_thermal_adapter_rejects_unsupported_protocol():
    adapter = create_hardware_adapter(
        "thermal_camera", {"endpoint": "rtsp://127.0.0.1:1/stream", "protocol": "carrier-pigeon"}
    )
    result = adapter.connect()
    assert result["connected"] is False
    assert "unsupported" in result["message"].lower()


def test_thermal_adapter_connection_failure_is_honest():
    adapter = create_hardware_adapter(
        "thermal_camera", {"endpoint": "rtsp://127.0.0.1:1/nope", "protocol": "rtsp"}
    )
    result = adapter.connect()
    assert result["connected"] is False
    assert adapter.status()["last_error"], "A failure reason must be reported"


def teardown_module(module):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
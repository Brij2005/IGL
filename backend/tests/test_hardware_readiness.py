"""Hardware integration readiness tests.

Verifies that all eight hardware categories exist, that every device reports an
honest NOT_CONNECTED state without a configured endpoint, and that the status
endpoint exposes real camera/worker counts rather than fabricated values.
"""

import os
import shutil
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="igl-hw-test-"))
os.environ.setdefault("DATABASE_PATH", os.path.join(TEST_ROOT, "test.db"))
os.environ.setdefault("ENABLE_AUTH", "false")

import pytest
from fastapi.testclient import TestClient

from hardware_adapters import create_hardware_adapter
from main import app

REQUIRED_CATEGORIES = [
    "thermal_camera",
    "cctv_camera",
    "rfid_reader",
    "access_control",
    "turnstile",
    "alarm",
    "central_server",
    "storage_backup",
]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_all_eight_hardware_categories_have_adapters():
    for category in REQUIRED_CATEGORIES:
        adapter = create_hardware_adapter(category, {})
        assert adapter is not None, f"No adapter for required category: {category}"


def test_unknown_device_type_returns_none():
    assert create_hardware_adapter("teleporter", {}) is None


def test_every_device_reports_not_connected_without_endpoint():
    for category in REQUIRED_CATEGORIES:
        adapter = create_hardware_adapter(category, {})
        status = adapter.status()
        assert status["connected"] is False, f"{category} must not fake a connection"
        assert status["configured"] is False


def test_connect_without_endpoint_does_not_fake_success():
    for category in REQUIRED_CATEGORIES:
        adapter = create_hardware_adapter(category, {})
        result = adapter.connect()
        assert result["connected"] is False
        assert result.get("message"), "A reason must be reported"


def test_connect_with_endpoint_still_not_implemented():
    """Configuring an endpoint must not pretend a transport exists."""
    adapter = create_hardware_adapter("rfid_reader", {"endpoint": "tcp://127.0.0.1:9000"})
    result = adapter.connect()
    assert result["connected"] is False
    assert adapter.status()["last_error"]


def test_status_endpoint_lists_all_categories_and_real_camera_counts(client):
    body = client.get("/api/security/hardware/status").json()
    kinds = {device["kind"] for device in body["devices"]}
    for category in REQUIRED_CATEGORIES:
        assert category in kinds, f"Status endpoint missing {category}"
    assert all(device["connected"] is False for device in body["devices"])
    assert "cameras" in body
    assert isinstance(body["cameras"]["registered"], int)
    assert isinstance(body["cameras"]["connected"], int)
    assert isinstance(body["cameras"]["workers_running"], int)


def test_camera_layer_status_reflects_real_cameras(client):
    """Registering and starting a camera must be visible in hardware status."""
    import cv2
    import numpy as np

    image_path = TEST_ROOT / "hw.jpg"
    cv2.imwrite(str(image_path), np.full((120, 160, 3), (60, 70, 80), dtype=np.uint8))
    camera = client.post(
        "/api/cameras",
        json={"name": "HW Cam", "source_type": "image", "source_url": str(image_path)},
    ).json()["camera"]["id"]
    client.post(f"/api/cameras/{camera}/start")

    import time

    deadline = time.time() + 8
    body = {}
    while time.time() < deadline:
        body = client.get("/api/security/hardware/status").json()
        if body["cameras"]["workers_running"] >= 1:
            break
        time.sleep(0.3)

    assert body["cameras"]["registered"] >= 1
    assert body["cameras"]["workers_running"] >= 1, "A started camera must have a running worker"

    client.post(f"/api/cameras/{camera}/stop")


def test_worker_status_endpoint_reports_own_worker_fields(client):
    body = client.get("/api/security/workers").json()
    assert "workers" in body
    for worker in body["workers"]:
        assert "camera_id" in worker
        assert "running" in worker
        assert "frames_processed" in worker
        assert "model_ids" in worker


def teardown_module(module):
    try:
        from camera_manager import camera_manager

        camera_manager.stop_all()
    except Exception:
        pass
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
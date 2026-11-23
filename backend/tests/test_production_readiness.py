"""Production readiness tests: monitoring endpoints, structured logging, redaction."""

import io
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="igl-mon-test-"))
os.environ.setdefault("DATABASE_PATH", os.path.join(TEST_ROOT, "test.db"))
os.environ.setdefault("ENABLE_AUTH", "false")

import pytest
from fastapi.testclient import TestClient

from database import init_db

init_db()

from logging_setup import JsonFormatter, _redact, get_logger, setup_logging
from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


# ------------------------------------------------------------- health probes
def test_monitoring_health_reports_database(client):
    body = client.get("/api/monitoring/health").json()
    assert body["status"] == "OK"
    assert body["checks"]["database"] == "UP"


def test_monitoring_summary_is_real_state(client):
    body = client.get("/api/monitoring/summary").json()
    assert body["application"]["status"] == "OK"
    assert isinstance(body["cameras"]["registered"], int)
    assert isinstance(body["models"]["registered"], int)
    assert "items" in body["models"]
    assert body["note"]


def test_summary_never_reports_unavailable_model_as_loaded(client):
    body = client.get("/api/monitoring/summary").json()
    for model in body["models"]["items"]:
        if model["status"] != "AVAILABLE":
            assert model["loaded"] is False, f"{model['id']} must not be loaded while unavailable"


def test_summary_never_reports_unconnected_camera_online(client):
    """An ONLINE camera must be backed by a real configured source.

    Other test modules start image-source cameras in the shared database, so the
    invariant is consistency rather than emptiness: no ONLINE camera may exist
    without a genuine source URL.
    """
    from database import get_db_connection

    body = client.get("/api/monitoring/summary").json()
    online_ids = [c["id"] for c in body["cameras"]["items"] if c["status"].upper() == "ONLINE"]

    conn = get_db_connection()
    rows = {
        row["id"]: row["source_url"]
        for row in conn.execute("SELECT id, source_url FROM cameras").fetchall()
    }
    conn.close()

    for camera_id in online_ids:
        assert rows.get(camera_id), f"Camera {camera_id} is ONLINE without a configured source"


def test_summary_counts_models_correctly(client):
    body = client.get("/api/monitoring/summary").json()
    registered = body["models"]["registered"]
    available = body["models"]["available"]
    unavailable = body["models"]["unavailable"]
    assert available + unavailable == registered


# --------------------------------------------------------- structured logging
def test_json_formatter_emits_valid_json():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="camera.started", args=(), exc_info=None,
    )
    record.camera_id = "CAM-1"
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "camera.started"
    assert payload["level"] == "INFO"
    assert payload["camera_id"] == "CAM-1"
    assert "timestamp" in payload


def test_sensitive_values_are_redacted():
    cleaned = _redact(
        {
            "password": "hunter2",
            "api_key": "abc",
            "nested": {"access_token": "xyz"},
            "safe": "visible",
        }
    )
    assert cleaned["password"] == "***REDACTED***"
    assert cleaned["api_key"] == "***REDACTED***"
    assert cleaned["nested"]["access_token"] == "***REDACTED***"
    assert cleaned["safe"] == "visible"


def test_redaction_handles_embedded_secrets_in_strings():
    cleaned = _redact({"note": "password=supersecret123 happened"})
    assert "supersecret123" not in cleaned["note"]


def test_setup_logging_is_idempotent():
    setup_logging("INFO")
    root = logging.getLogger()
    first = len(root.handlers)
    setup_logging("INFO")
    assert len(root.handlers) == first, "Repeated setup must not duplicate handlers"


def test_logger_writes_json_line():
    setup_logging("INFO")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = get_logger("igl.test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.info("worker.started", extra={"camera_id": "CAM-9", "password": "secret"})
    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "worker.started"
    assert payload["camera_id"] == "CAM-9"
    assert payload["password"] == "***REDACTED***"


# --------------------------------------------------------- production config
def test_production_config_guard_rejects_unsafe_settings():
    from config import validate_production_config
    import config as config_module

    original = (config_module.IS_PRODUCTION, config_module.ENABLE_AUTH,
                config_module.SECRET_KEY, config_module.ADMIN_PASSWORD)
    try:
        config_module.IS_PRODUCTION = True
        config_module.ENABLE_AUTH = False
        config_module.SECRET_KEY = "dev-only-igl-secret-change-in-production"
        config_module.ADMIN_PASSWORD = "ChangeMe-IGL-2026!"
        problems = validate_production_config()
        assert any("ENABLE_AUTH" in p for p in problems)
        assert any("SECRET_KEY" in p for p in problems)
        assert any("ADMIN_PASSWORD" in p for p in problems)

        config_module.ENABLE_AUTH = True
        config_module.SECRET_KEY = "x" * 40
        config_module.ADMIN_PASSWORD = "A-Long-Enough-Password-1"
        assert validate_production_config() == []
    finally:
        (
            config_module.IS_PRODUCTION,
            config_module.ENABLE_AUTH,
            config_module.SECRET_KEY,
            config_module.ADMIN_PASSWORD,
        ) = original


def test_development_config_is_not_guarded():
    from config import validate_production_config
    import config as config_module

    original = config_module.IS_PRODUCTION
    try:
        config_module.IS_PRODUCTION = False
        assert validate_production_config() == []
    finally:
        config_module.IS_PRODUCTION = original


def teardown_module(module):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
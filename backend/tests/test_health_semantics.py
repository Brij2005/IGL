"""Health-semantics tests.

Health output must report what was actually observed. The process being up is
not health, an unconfigured model is not a measured model, and no FPS, latency,
or validation claim may be produced without an observed frame.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base
from app.main import app
from app.models import Camera, CameraHealth, Plant
from app.services import system_health
from app.services.inference_pipeline import pipeline_manager
from app.services.system_health import (
    APPLICATION_UP,
    CAMERA_AVAILABLE,
    CAMERA_CONFIGURED_NOT_OBSERVED,
    CAMERA_NONE_ACTIVE,
    DATABASE_OK,
    DATABASE_UNAVAILABLE,
    MIGRATIONS_CURRENT,
    MIGRATIONS_PENDING,
    MODEL_CONFIGURED,
    MODEL_NOT_CONFIGURED,
    NO_CAMERA,
    collect_system_health,
)


@pytest.fixture
def isolated_database(monkeypatch):
    """Point the health collector at an isolated, fully migrated-shaped database."""
    engine = create_engine(
        "sqlite:///file:health_test_db?mode=memory&cache=shared&uri=true",
        connect_args={"check_same_thread": False, "uri": True},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(system_health, "engine", engine)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_health_reports_application_up_separately_from_subsystems(isolated_database):
    health = collect_system_health()
    assert health["application"] == APPLICATION_UP
    assert health["database"] == DATABASE_OK
    assert health["migrations"] == MIGRATIONS_CURRENT
    assert health["model_state"] == MODEL_NOT_CONFIGURED
    assert health["camera_state"] == NO_CAMERA
    assert health["validation_status"] == "NOT_VALIDATED"
    assert health["igl_validated"] is False
    assert health["overall_status"] == "DEGRADED"
    assert health["measured_performance"] == "NOT_MEASURED_WITHOUT_OBSERVED_FRAMES"


def test_health_never_reports_online_merely_because_the_process_started(isolated_database):
    health = collect_system_health()
    assert "ONLINE" not in {health["database"], health["migrations"], health["model_state"]}
    assert health["overall_status"] != "OPERATIONAL"
    assert MODEL_NOT_CONFIGURED in health["degraded_reasons"]
    assert NO_CAMERA in health["degraded_reasons"]


def test_database_unavailable_is_reported_distinctly(monkeypatch):
    class UnreachableEngine:
        def connect(self):
            raise RuntimeError("database is unavailable")

    monkeypatch.setattr(system_health, "engine", UnreachableEngine())
    health = collect_system_health()
    assert health["database"] == DATABASE_UNAVAILABLE
    assert health["camera_count"] is None
    assert health["active_camera_count"] is None
    assert health["igl_configuration_status"] == "UNKNOWN_DATABASE_UNAVAILABLE"
    assert DATABASE_UNAVAILABLE in health["degraded_reasons"]


def test_pending_migrations_are_reported_distinctly(monkeypatch):
    monkeypatch.setattr(system_health, "get_migration_state", lambda: ({"head"}, {"older"}))
    health = collect_system_health()
    assert health["migrations"] == MIGRATIONS_PENDING
    assert MIGRATIONS_PENDING in health["degraded_reasons"]


def test_migration_check_failure_is_reported_distinctly(monkeypatch):
    def raise_error():
        raise RuntimeError("alembic unavailable")

    monkeypatch.setattr(system_health, "get_migration_state", raise_error)
    assert collect_system_health()["migrations"] == MIGRATIONS_PENDING


def test_camera_states_distinguish_configured_from_absent(isolated_database, monkeypatch):
    inactive = Camera(name="Inactive camera", code="CAM-OFF", stream_url="rtsp://camera.invalid/live", is_active=False)
    isolated_database.add(inactive)
    isolated_database.commit()
    assert collect_system_health()["camera_state"] == CAMERA_NONE_ACTIVE

    active = Camera(name="Active camera", code="CAM-ON", stream_url="rtsp://camera.invalid/live2")
    isolated_database.add(active)
    isolated_database.commit()
    health = collect_system_health()
    assert health["camera_state"] == CAMERA_CONFIGURED_NOT_OBSERVED
    assert health["camera_count"] == 2
    assert health["active_camera_count"] == 1

    monkeypatch.setattr(
        pipeline_manager,
        "pipeline_status",
        lambda camera_id=None: [{
            "camera_id": active.id,
            "stream_status": "RUNNING",
            "frames_seen": 1,
            "inferences_completed": 0,
        }],
    )
    assert collect_system_health()["camera_state"] == CAMERA_AVAILABLE


def test_configured_camera_reports_no_measured_values_before_any_frame(isolated_database):
    camera = Camera(name="Configured camera", code="CAM-CFG", stream_url="rtsp://camera.invalid/live")
    isolated_database.add(camera)
    isolated_database.flush()
    isolated_database.add(CameraHealth(
        camera_id=camera.id,
        status="CONFIGURED",
        inference_status="NOT_RUNNING",
    ))
    isolated_database.commit()

    health = collect_system_health()
    assert health["camera_state"] == CAMERA_CONFIGURED_NOT_OBSERVED
    assert health["inference_pipeline"] == "NOT_RUNNING"
    assert health["running_pipeline_count"] == 0
    assert health["measured_performance"] == "NOT_MEASURED_WITHOUT_OBSERVED_FRAMES"


def test_pipeline_counts_as_running_only_after_completed_inference(monkeypatch):
    monkeypatch.setattr(
        pipeline_manager,
        "pipeline_status",
        lambda camera_id=None: [
            {"camera_id": "cam-1", "inferences_completed": 0},
            {"camera_id": "cam-2", "inferences_completed": 3},
        ],
    )
    health = collect_system_health()
    assert health["running_pipeline_count"] == 1
    assert health["inference_pipeline"] == "RUNNING"


def test_model_configured_state_follows_actual_model_availability(monkeypatch):
    monkeypatch.setattr(
        pipeline_manager,
        "model_health",
        lambda: {"status": "READY", "available": True, "model_name": "m", "model_version": "v"},
    )
    monkeypatch.setattr(pipeline_manager, "pipeline_status", lambda camera_id=None: [])
    assert collect_system_health()["model_state"] == MODEL_CONFIGURED


def test_igl_configuration_status_is_not_configured_without_plant_records(isolated_database):
    assert collect_system_health()["igl_configuration_status"] == "NOT_CONFIGURED"

    isolated_database.add(Plant(name="Test Plant", code="TP-X"))
    isolated_database.commit()
    # A submitted plant is still not a validated IGL configuration.
    assert collect_system_health()["igl_configuration_status"] == "CONFIGURED_NOT_VALIDATED"


def test_root_endpoint_exposes_explicit_unavailable_states():
    with TestClient(app) as client:
        payload = client.get("/").json()
    assert payload["application"] == APPLICATION_UP
    assert payload["database"] in {DATABASE_OK, DATABASE_UNAVAILABLE}
    assert payload["migrations"] in {MIGRATIONS_CURRENT, MIGRATIONS_PENDING}
    assert payload["model_state"] in {MODEL_CONFIGURED, MODEL_NOT_CONFIGURED}
    assert payload["camera_state"] in {NO_CAMERA, CAMERA_AVAILABLE, CAMERA_NONE_ACTIVE, CAMERA_CONFIGURED_NOT_OBSERVED}
    assert payload["validation_status"] == "NOT_VALIDATED"
    assert payload["igl_validated"] is False
    for value in payload.values():
        assert "ONLINE" != value

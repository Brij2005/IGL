"""Derived system health from actual DB, migration, worker, and model state."""
from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import select

try:
    from app.config import settings
    from app.database import engine
    from app.services.continuous_health import camera_health_worker
    from app.services.inference_pipeline import pipeline_manager
except ImportError:
    from backend.app.config import settings
    from backend.app.database import engine
    from backend.app.services.continuous_health import camera_health_worker
    from backend.app.services.inference_pipeline import pipeline_manager


def collect_system_health() -> dict:
    database_state = "ONLINE"
    try:
        with engine.connect() as connection:
            connection.execute(select(1))
    except Exception:
        database_state = "UNAVAILABLE"

    migration_state = "CURRENT"
    try:
        backend_dir = Path(__file__).resolve().parents[2]
        config = Config(str(backend_dir / "alembic.ini"))
        config.set_main_option("script_location", str(backend_dir / "migrations"))
        expected_heads = set(ScriptDirectory.from_config(config).get_heads())
        with engine.connect() as connection:
            current_heads = set(MigrationContext.configure(connection).get_current_heads())
        if current_heads != expected_heads:
            migration_state = "PENDING_OR_UNAVAILABLE"
    except Exception:
        migration_state = "PENDING_OR_UNAVAILABLE"

    model = pipeline_manager.model_health()
    pipelines = pipeline_manager.pipeline_status()
    api_state = "ONLINE"
    inference_state = "RUNNING" if any(item["inferences_completed"] for item in pipelines) else model["status"]
    evidence_path = Path(settings.EVIDENCE_DIR).expanduser()
    evidence_state = "AVAILABLE" if evidence_path.is_dir() else "CONFIGURED_NOT_INITIALIZED"
    notification_state = "IN_APP_QUEUE_AVAILABLE_EXTERNAL_NOT_CONFIGURED"
    frontend_state = "NOT_ASSESSED"
    states = [database_state, migration_state, camera_health_worker.status]
    overall = "ONLINE" if database_state == "ONLINE" and migration_state == "CURRENT" and camera_health_worker.status == "RUNNING" else "DEGRADED"
    if not model["available"]:
        overall = "DEGRADED"

    return {
        "overall_status": overall,
        "api": api_state,
        "database": database_state,
        "migrations": migration_state,
        "camera_health_worker": camera_health_worker.status,
        "inference_pipeline": inference_state,
        "model": model,
        "active_pipelines": len(pipelines),
        "evidence_subsystem": evidence_state,
        "notification_subsystem": notification_state,
        "frontend_connectivity": frontend_state,
        "validation_status": "NOT_VALIDATED",
        "igl_validated": False,
        "subsystem_states": states,
    }

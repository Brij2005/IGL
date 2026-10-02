"""Derived system health from actual DB, migration, worker, and model state.

Every field here is measured or read. Nothing is initialised with a
plausible-looking default, and an absent capability is reported as an
explicit unavailable state rather than as a healthy one.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select, literal

try:
    from app.config import settings
    from app.database import engine, get_migration_state
    from app.models import Camera, Plant
    from app.services.continuous_health import camera_health_worker
    from app.services.inference_pipeline import pipeline_manager
    from app.services.notification_worker import notification_delivery_worker
except ImportError:
    from backend.app.config import settings
    from backend.app.database import engine, get_migration_state
    from backend.app.models import Camera, Plant
    from backend.app.services.continuous_health import camera_health_worker
    from backend.app.services.inference_pipeline import pipeline_manager
    from backend.app.services.notification_worker import notification_delivery_worker


APPLICATION_UP = "APPLICATION_UP"
DATABASE_OK = "DATABASE_OK"
DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
MIGRATIONS_CURRENT = "MIGRATIONS_CURRENT"
MIGRATIONS_PENDING = "MIGRATIONS_PENDING_OR_UNAVAILABLE"
MODEL_CONFIGURED = "MODEL_CONFIGURED"
MODEL_NOT_CONFIGURED = "MODEL_NOT_CONFIGURED"
CAMERA_AVAILABLE = "CAMERA_AVAILABLE"
CAMERA_NONE_ACTIVE = "CAMERA_RECORDED_BUT_NONE_ACTIVE"
NO_CAMERA = "NO_CAMERA"
VALIDATION_NOT_VALIDATED = "NOT_VALIDATED"

_SELECT_ONE = select(literal(1))
_COUNT_PLANT = select(func.count()).select_from(Plant)
_COUNT_CAMERA = select(func.count()).select_from(Camera)
_COUNT_ACTIVE_CAMERA = select(func.count()).select_from(Camera).where(Camera.is_active.is_(True))


def _database_facts() -> tuple[str, int, int, int]:
    """Return the database state plus plant, camera, and active-camera counts."""
    try:
        with engine.connect() as connection:
            connection.execute(_SELECT_ONE)
            return (
                DATABASE_OK,
                connection.execute(_COUNT_PLANT).scalar_one(),
                connection.execute(_COUNT_CAMERA).scalar_one(),
                connection.execute(_COUNT_ACTIVE_CAMERA).scalar_one(),
            )
    except Exception:
        return DATABASE_UNAVAILABLE, 0, 0, 0


def _migration_state() -> str:
    try:
        expected_heads, current_heads = get_migration_state()
    except Exception:
        return MIGRATIONS_PENDING
    return MIGRATIONS_CURRENT if current_heads == expected_heads else MIGRATIONS_PENDING


def _camera_state(camera_count: int, active_camera_count: int) -> str:
    if not camera_count:
        return NO_CAMERA
    return CAMERA_AVAILABLE if active_camera_count else CAMERA_NONE_ACTIVE


def _igl_configuration_status(plant_count: int, database_state: str) -> str:
    if database_state != DATABASE_OK:
        return "UNKNOWN_DATABASE_UNAVAILABLE"
    return "NOT_CONFIGURED" if plant_count == 0 else "CONFIGURED_NOT_VALIDATED"


def collect_system_health() -> dict:
    database_state, plant_count, camera_count, active_camera_count = _database_facts()
    migration_state = _migration_state()
    model = pipeline_manager.model_health()
    model_state = MODEL_CONFIGURED if model["available"] else MODEL_NOT_CONFIGURED
    pipelines = pipeline_manager.pipeline_status()
    camera_state = _camera_state(camera_count, active_camera_count)

    # A pipeline counts as running only when a frame actually completed inference.
    running_pipelines = sum(1 for item in pipelines if item["inferences_completed"] > 0)
    inference_state = "RUNNING" if running_pipelines else "NOT_RUNNING"

    evidence_path = Path(settings.EVIDENCE_DIR).expanduser()
    evidence_state = "AVAILABLE" if evidence_path.is_dir() else "CONFIGURED_NOT_INITIALIZED"

    degraded_reasons = []
    if database_state != DATABASE_OK:
        degraded_reasons.append(database_state)
    if migration_state != MIGRATIONS_CURRENT:
        degraded_reasons.append(migration_state)
    if model_state == MODEL_NOT_CONFIGURED:
        degraded_reasons.append(model_state)
    if camera_state in (NO_CAMERA, CAMERA_NONE_ACTIVE):
        degraded_reasons.append(camera_state)
    if camera_health_worker.status != "RUNNING":
        degraded_reasons.append(f"CAMERA_HEALTH_WORKER_{camera_health_worker.status}")

    database_reachable = database_state == DATABASE_OK
    return {
        "overall_status": "DEGRADED" if degraded_reasons else "OPERATIONAL",
        "degraded_reasons": degraded_reasons,
        "application": APPLICATION_UP,
        "database": database_state,
        "migrations": migration_state,
        "camera_state": camera_state,
        "model_state": model_state,
        "camera_health_worker": camera_health_worker.status,
        "camera_health_worker_last_error": camera_health_worker.last_error_type,
        "inference_pipeline": inference_state,
        "running_pipeline_count": running_pipelines,
        "model": model,
        "camera_count": camera_count if database_reachable else None,
        "active_camera_count": active_camera_count if database_reachable else None,
        "active_pipelines": len(pipelines),
        "evidence_subsystem": evidence_state,
        "notification_subsystem": f"IN_APP_QUEUE_AVAILABLE_DELIVERY_WORKER_{notification_delivery_worker.status}",
        "notification_delivery_worker": notification_delivery_worker.status,
        "notification_delivery_last_error": notification_delivery_worker.last_error_type,
        # Reports the configured authentication mode without implying that a
        # particular request has been authenticated.
        "access_control": settings.authentication_state(),
        "frontend_connectivity": "NOT_ASSESSED",
        "validation_status": VALIDATION_NOT_VALIDATED,
        "igl_validated": False,
        "igl_configuration_status": _igl_configuration_status(plant_count, database_state),
        "measured_performance": "NOT_MEASURED_WITHOUT_OBSERVED_FRAMES",
    }

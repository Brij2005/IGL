"""Derived system health from actual DB, migration, worker, and model state.

Every field here is measured or read. Nothing is initialised with a
plausible-looking default, and an absent capability is reported as an
explicit unavailable state rather than as a healthy one.
"""
from __future__ import annotations

import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, literal

try:
    from app.config import settings
    from app.database import engine, get_migration_state
    from app.models import Camera, Plant
    from app.services.continuous_health import camera_health_worker
    from app.services.inference_pipeline import pipeline_manager
    from app.services.notification_worker import notification_delivery_worker
    from app.services.safety_event_worker import safety_event_worker
except ImportError:
    from backend.app.config import settings
    from backend.app.database import engine, get_migration_state
    from backend.app.models import Camera, Plant
    from backend.app.services.continuous_health import camera_health_worker
    from backend.app.services.inference_pipeline import pipeline_manager
    from backend.app.services.notification_worker import notification_delivery_worker
    from backend.app.services.safety_event_worker import safety_event_worker


APPLICATION_UP = "APPLICATION_UP"
DATABASE_OK = "DATABASE_OK"
DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
MIGRATIONS_CURRENT = "MIGRATIONS_CURRENT"
MIGRATIONS_PENDING = "MIGRATIONS_PENDING_OR_UNAVAILABLE"
MODEL_CONFIGURED = "MODEL_CONFIGURED"
MODEL_NOT_CONFIGURED = "MODEL_NOT_CONFIGURED"
CAMERA_AVAILABLE = "CAMERA_AVAILABLE"
CAMERA_NONE_ACTIVE = "CAMERA_RECORDED_BUT_NONE_ACTIVE"
CAMERA_CONFIGURED_NOT_OBSERVED = "CAMERA_CONFIGURED_NOT_OBSERVED"
NO_CAMERA = "NO_CAMERA"
VALIDATION_NOT_VALIDATED = "NOT_VALIDATED"
EMAIL_CONFIGURED = "EMAIL_CONFIGURED"
EMAIL_NOT_CONFIGURED = "EMAIL_NOT_CONFIGURED"
WHATSAPP_CONFIGURED = "WHATSAPP_CONFIGURED"
WHATSAPP_NOT_CONFIGURED = "WHATSAPP_NOT_CONFIGURED"
DISK_OK = "DISK_OK"
DISK_LOW = "DISK_FREE_SPACE_BELOW_THRESHOLD"
DISK_UNAVAILABLE = "DISK_UNAVAILABLE"
ALARM_DISABLED = "ALARM_DISABLED_BY_POLICY"
ALARM_READY = "ALARM_READY_EVENT_DRIVEN"

# Process start is recorded at import time. Uptime is a real measurement of this
# process, and it is reported as unavailable if the platform cannot read it.
_PROCESS_START_MONOTONIC = time.monotonic()
_PROCESS_START_WALL_CLOCK = datetime.now(timezone.utc)

_SELECT_ONE = select(literal(1))
_COUNT_PLANT = select(func.count()).select_from(Plant)
_COUNT_CAMERA = select(func.count()).select_from(Camera)
_ACTIVE_CAMERA_IDS = select(Camera.id).where(Camera.is_active.is_(True))


def _database_facts() -> tuple[str, int, int, int, set[str]]:
    """Return the database state plus plant, camera, and active-camera counts."""
    try:
        with engine.connect() as connection:
            connection.execute(_SELECT_ONE)
            active_camera_ids = set(connection.execute(_ACTIVE_CAMERA_IDS).scalars().all())
            return (
                DATABASE_OK,
                connection.execute(_COUNT_PLANT).scalar_one(),
                connection.execute(_COUNT_CAMERA).scalar_one(),
                len(active_camera_ids),
                active_camera_ids,
            )
    except Exception:
        return DATABASE_UNAVAILABLE, 0, 0, 0, set()


def _migration_state() -> str:
    try:
        expected_heads, current_heads = get_migration_state()
    except Exception:
        return MIGRATIONS_PENDING
    return MIGRATIONS_CURRENT if current_heads == expected_heads else MIGRATIONS_PENDING


def _camera_state(camera_count: int, active_camera_ids: set[str], pipelines: list[dict]) -> str:
    if not camera_count:
        return NO_CAMERA
    if not active_camera_ids:
        return CAMERA_NONE_ACTIVE
    has_observed_frames = any(
        item.get("camera_id") in active_camera_ids
        and item.get("stream_status") == "RUNNING"
        and item.get("frames_seen", 0) > 0
        for item in pipelines
    )
    return CAMERA_AVAILABLE if has_observed_frames else CAMERA_CONFIGURED_NOT_OBSERVED


def _measured_performance(pipelines: list[dict]) -> dict:
    """Report performance only from frames and inferences that really happened.

    With no observed frame there is no FPS to report, and this returns
    ``NOT_MEASURED_WITHOUT_OBSERVED_FRAMES`` rather than a zero.
    """
    observed = [item for item in pipelines if item.get("frames_seen", 0) > 0]
    if not observed:
        return {
            "measured_performance": "NOT_MEASURED_WITHOUT_OBSERVED_FRAMES",
            "camera_fps": None,
            "inference_fps": None,
        }
    try:
        from app.services.video_ingestion import ingestion_manager
    except ImportError:  # pragma: no cover - import shim
        from backend.app.services.video_ingestion import ingestion_manager
    camera_fps_values = []
    for item in observed:
        reader = ingestion_manager.get_reader(item.get("camera_id"))
        if reader is not None and reader.current_fps > 0:
            camera_fps_values.append(float(reader.current_fps))
    inference_values = []
    for item in observed:
        window = _inference_window(item)
        if window and window > 0:
            inference_values.append(item["inferences_completed"] / window)
    return {
        "measured_performance": "MEASURED_FROM_OBSERVED_FRAMES",
        "camera_fps": round(max(camera_fps_values), 2) if camera_fps_values else None,
        "inference_fps": round(max(inference_values), 2) if inference_values else None,
    }


def _inference_window(item: dict) -> float | None:
    """Seconds between the first and last observed frame for a pipeline."""
    first = item.get("last_frame_timestamp")
    last = item.get("last_inference_timestamp")
    if not first or not last:
        return None
    try:
        seconds = (last - first).total_seconds()
    except AttributeError:
        return None
    return seconds if seconds > 0 else None


def _igl_configuration_status(plant_count: int, database_state: str) -> str:
    if database_state != DATABASE_OK:
        return "UNKNOWN_DATABASE_UNAVAILABLE"
    return "NOT_CONFIGURED" if plant_count == 0 else "CONFIGURED_NOT_VALIDATED"


def _uptime() -> dict:
    """Real process uptime. Reported as unavailable rather than guessed."""
    try:
        seconds = max(0.0, time.monotonic() - _PROCESS_START_MONOTONIC)
    except Exception:  # pragma: no cover - defensive
        return {"uptime_seconds": None, "uptime_state": "UPTIME_UNAVAILABLE"}
    return {
        "uptime_seconds": round(seconds, 3),
        "uptime_human": _humanise_uptime(seconds),
        "process_started_at": _PROCESS_START_WALL_CLOCK.isoformat(),
        "uptime_state": "MEASURED",
    }


def _humanise_uptime(seconds: float) -> str:
    total = int(seconds)
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _disk_health() -> dict:
    """Measure free space on the volume holding the evidence/database path."""
    target = settings.HEALTH_DISK_PATH or settings.EVIDENCE_DIR
    try:
        path = Path(target).expanduser()
        probe = path if path.exists() else path.parent
        usage = shutil.disk_usage(probe)
    except Exception:
        return {"disk_state": DISK_UNAVAILABLE, "disk_path": str(target), "disk_free_bytes": None, "disk_free_percent": None}
    free_percent = (usage.free / usage.total * 100.0) if usage.total else 0.0
    low = usage.free < settings.HEALTH_DISK_MIN_FREE_BYTES or free_percent < settings.HEALTH_DISK_MIN_FREE_PERCENT
    return {
        "disk_state": DISK_LOW if low else DISK_OK,
        "disk_path": str(target),
        "disk_total_bytes": usage.total,
        "disk_free_bytes": usage.free,
        "disk_free_percent": round(free_percent, 2),
        "disk_min_free_bytes": settings.HEALTH_DISK_MIN_FREE_BYTES,
    }


def _email_state() -> str:
    """EMAIL_CONFIGURED only when a usable SMTP transport is configured.

    This reports configuration, never delivery. A message is only reported as
    delivered from a real SMTP server response.
    """
    password = settings.SMTP_PASSWORD.get_secret_value().strip() if settings.SMTP_PASSWORD else ""
    configured = (
        bool(settings.SMTP_HOST)
        and bool(settings.NOTIFICATION_FROM_ADDRESS)
        and (bool(settings.SMTP_USERNAME) == bool(password))
    )
    return EMAIL_CONFIGURED if configured else EMAIL_NOT_CONFIGURED


def _whatsapp_state() -> str:
    """WHATSAPP_CONFIGURED only when the Cloud API credentials are complete."""
    token = settings.WHATSAPP_ACCESS_TOKEN.get_secret_value().strip() if settings.WHATSAPP_ACCESS_TOKEN else ""
    configured = bool(token) and bool(settings.WHATSAPP_PHONE_NUMBER_ID and settings.WHATSAPP_API_VERSION)
    return WHATSAPP_CONFIGURED if configured else WHATSAPP_NOT_CONFIGURED


def _alarm_states() -> dict:
    from app.services import physical_alarm

    return {
        "alarm_subsystem": ALARM_READY if settings.ALARM_ENABLED else ALARM_DISABLED,
        "physical_alarm_state": physical_alarm.actuator_status()["state"],
        "physical_alarm_transports": physical_alarm.actuator_status()["configured_transports"],
    }


def collect_system_health() -> dict:
    database_state, plant_count, camera_count, active_camera_count, active_camera_ids = _database_facts()
    migration_state = _migration_state()
    model = pipeline_manager.model_health()
    model_state = MODEL_CONFIGURED if model["available"] else MODEL_NOT_CONFIGURED
    pipelines = pipeline_manager.pipeline_status()
    camera_state = _camera_state(camera_count, active_camera_ids, pipelines)

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
    if camera_state in (NO_CAMERA, CAMERA_NONE_ACTIVE, CAMERA_CONFIGURED_NOT_OBSERVED):
        degraded_reasons.append(camera_state)
    if camera_health_worker.status != "RUNNING":
        degraded_reasons.append(f"CAMERA_HEALTH_WORKER_{camera_health_worker.status}")

    database_reachable = database_state == DATABASE_OK
    disk = _disk_health()
    uptime = _uptime()
    email_state = _email_state()
    whatsapp_state = _whatsapp_state()
    alarm = _alarm_states()
    if disk["disk_state"] == DISK_LOW:
        degraded_reasons.append(DISK_LOW)
    return {
        "overall_status": "DEGRADED" if degraded_reasons else "OPERATIONAL",
        "degraded_reasons": degraded_reasons,
        "application": APPLICATION_UP,
        **uptime,
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
        "email_transport": email_state,
        "email_transport_note": "Configuration state only; delivery is reported per notification from the SMTP response.",
        "whatsapp_transport": whatsapp_state,
        "whatsapp_transport_note": "Configuration state only; delivery is reported per notification from the Cloud API response.",
        "safety_event_worker": safety_event_worker.status,
        "safety_event_worker_last_error": safety_event_worker.last_error_type,
        **alarm,
        # Reports the configured authentication mode without implying that a
        # particular request has been authenticated.
        "access_control": settings.authentication_state(),
        "frontend_connectivity": "NOT_ASSESSED",
        "validation_status": VALIDATION_NOT_VALIDATED,
        "igl_validated": False,
        "igl_configuration_status": _igl_configuration_status(plant_count, database_state),
        **_measured_performance(pipelines),
        **disk,
    }

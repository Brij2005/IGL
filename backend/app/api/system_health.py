"""Inference and pipeline health endpoints."""
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

try:
    from app.access_control import require_permission, resolve_actor
    from app.database import get_db
    from app.models import DetectorConfig, User
    from app.schemas_system import AIHealthOut, ModelHealthOut, PipelineStatusOut
    from app.services.inference_pipeline import pipeline_manager
    from app.services.safety_engine import detector_capabilities
    from app.services.system_health import collect_system_health
except ImportError:
    from backend.app.access_control import require_permission, resolve_actor
    from backend.app.database import get_db
    from backend.app.models import DetectorConfig, User
    from backend.app.schemas_system import AIHealthOut, ModelHealthOut, PipelineStatusOut
    from backend.app.services.inference_pipeline import pipeline_manager
    from backend.app.services.safety_engine import detector_capabilities
    from backend.app.services.system_health import collect_system_health


router = APIRouter()


@router.get("/health")
def get_system_health():
    """Derived subsystem state. The process being up is reported separately from
    database, model, camera, and validation state."""
    return collect_system_health()


@router.get("/ai-health", response_model=AIHealthOut)
def get_ai_health(actor: Optional[User] = Depends(resolve_actor)):
    return {
        "model": pipeline_manager.model_health(),
        "pipelines": pipeline_manager.pipeline_status(),
        "igl_validation_status": "NOT_VALIDATED",
    }


@router.get("/pipelines", response_model=list[PipelineStatusOut])
def get_pipeline_status(actor: Optional[User] = Depends(resolve_actor)):
    return pipeline_manager.pipeline_status()


@router.get("/detectors")
def get_detector_capabilities(
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("cameras:view")),
):
    """Report what each safety detector can actually do right now.

    ``implementation_state`` and ``availability_state`` are reported separately
    so an unimplemented detector is never displayed as merely unconfigured, and
    ``operational`` is true only when the build implements the detector, the
    loaded model supports it, and an operator enabled a configuration for it.
    """
    model_health = pipeline_manager.model_health()
    configs = db.query(DetectorConfig).all()
    capabilities = detector_capabilities(model_health, configs)
    return {
        "model_status": model_health.get("status"),
        "model_classes_available": len(model_health.get("classes") or ()),
        "detectors": [capability.as_dict() for capability in capabilities],
        "operational_detector_count": sum(1 for capability in capabilities if capability.operational),
        "igl_validation_status": "NOT_VALIDATED",
    }

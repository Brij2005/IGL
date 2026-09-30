"""Authenticated Phase 4 inference and pipeline health endpoints."""
from fastapi import APIRouter, Depends

try:
    from app.auth import get_current_active_user
    from app.models import User
    from app.services.inference_pipeline import pipeline_manager
    from app.services.system_health import collect_system_health
except ImportError:
    from backend.app.auth import get_current_active_user
    from backend.app.models import User
    from backend.app.services.inference_pipeline import pipeline_manager
    from backend.app.services.system_health import collect_system_health


router = APIRouter()


@router.get("/health")
def get_system_health(user: User = Depends(get_current_active_user)):
    return collect_system_health()


@router.get("/ai-health")
def get_ai_health(user: User = Depends(get_current_active_user)):
    return {
        "model": pipeline_manager.model_health(),
        "pipelines": pipeline_manager.pipeline_status(),
        "igl_validation_status": "NOT_VALIDATED",
    }


@router.get("/pipelines")
def get_pipeline_status(user: User = Depends(get_current_active_user)):
    return pipeline_manager.pipeline_status()
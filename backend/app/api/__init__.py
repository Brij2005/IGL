"""
API route package initializer.

There is no authentication router: this build has no login, no token issuance
and no token validation. The identity router exposes the operator directory and
the audit trail, which are operational data rather than authentication.
"""
from fastapi import APIRouter

try:
    from app.api.identity import router as identity_router
    from app.api.cameras import router as cameras_router
    from app.api.system_health import router as system_health_router
    from app.api.events import router as events_router
    from app.api.evidence import router as evidence_router
    from app.api.analytics import router as analytics_router
    from app.api.notifications import router as notifications_router
    from app.api.configuration import router as configuration_router
    from app.api.safety_config import router as safety_config_router
    from app.api.escalation import router as escalation_router
    from app.api.responses import router as responses_router
except ImportError:
    from backend.app.api.identity import router as identity_router
    from backend.app.api.cameras import router as cameras_router
    from backend.app.api.system_health import router as system_health_router
    from backend.app.api.events import router as events_router
    from backend.app.api.evidence import router as evidence_router
    from backend.app.api.analytics import router as analytics_router
    from backend.app.api.notifications import router as notifications_router
    from backend.app.api.configuration import router as configuration_router
    from backend.app.api.safety_config import router as safety_config_router
    from backend.app.api.escalation import router as escalation_router
    from backend.app.api.responses import router as responses_router

api_router = APIRouter()
api_router.include_router(identity_router, prefix="/identity", tags=["Operator Identity & Audit"])
api_router.include_router(cameras_router, prefix="/cameras", tags=["Camera Management & Health"])
api_router.include_router(system_health_router, prefix="/system", tags=["Inference Health"])
api_router.include_router(events_router, prefix="/events", tags=["Safety Events"])
api_router.include_router(responses_router, prefix="", tags=["Safety Response"])
api_router.include_router(evidence_router, prefix="", tags=["Event Evidence"])
api_router.include_router(analytics_router, prefix="/analytics", tags=["Database Analytics"])
api_router.include_router(notifications_router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(configuration_router, prefix="/configuration", tags=["Plant Configuration"])
api_router.include_router(safety_config_router, prefix="/configuration", tags=["Safety Policy Configuration"])
api_router.include_router(escalation_router, prefix="", tags=["Escalation & Correlation"])
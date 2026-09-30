"""
FastAPI Main Application Entry Point for IGL Industrial AI Safety & Incident Intelligence Platform.
"""
import sys
import os
from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

# Correlation identifier header. Incoming values are only accepted when they
# look like a bounded opaque token, so a caller cannot inject arbitrary text
# into logs or downstream headers.
REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LENGTH = 64
_ALLOWED_REQUEST_ID_CHARACTERS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")

# Ensure the project and backend packages resolve from either supported launch directory.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, BACKEND_DIR)

try:
    from app.config import settings
    from app.database import engine, SessionLocal, require_database_at_migration_head
    from app.models import Role
    from app.api import api_router
    from app.services.inference_pipeline import pipeline_manager
    from app.services.continuous_health import camera_health_worker
    from app.services.system_health import collect_system_health
except ImportError:
    from backend.app.config import settings
    from backend.app.database import engine, SessionLocal, require_database_at_migration_head
    from backend.app.models import Role
    from backend.app.api import api_router
    from backend.app.services.inference_pipeline import pipeline_manager
    from backend.app.services.continuous_health import camera_health_worker
    from backend.app.services.system_health import collect_system_health


DEFAULT_ROLES = [
    {
        "name": "ADMIN",
        "description": "System Administrator with full access",
        "permissions_json": ["*"]
    },
    {
        "name": "SAFETY_OFFICER",
        "description": "Safety officer managing events, incidents and corrective actions",
        "permissions_json": [
            "events:view", "events:acknowledge", "events:assign",
            "incidents:manage", "corrective_actions:manage", "analytics:view",
            "plants:view", "zones:view"
        ]
    },
    {
        "name": "PLANT_MANAGER",
        "description": "Plant manager overseeing operations, analytics and audit logs",
        "permissions_json": [
            "plants:view", "zones:view", "events:view",
            "incidents:view", "corrective_actions:view", "analytics:view", "audit_logs:view"
        ]
    },
    {
        "name": "OPERATOR",
        "description": "Plant floor operator monitoring live feeds and acknowledging alerts",
        "permissions_json": ["events:view", "events:acknowledge", "cameras:view_live"]
    }
]


def seed_default_roles(db: Session):
    """Seed baseline RBAC roles if not present in the database."""
    for role_data in DEFAULT_ROLES:
        existing = db.query(Role).filter(Role.name == role_data["name"]).first()
        if not existing:
            new_role = Role(
                name=role_data["name"],
                description=role_data["description"],
                permissions_json=role_data["permissions_json"]
            )
            db.add(new_role)
        else:
            existing.description = role_data["description"]
            existing.permissions_json = role_data["permissions_json"]
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events management."""
    require_database_at_migration_head()
    pipeline_manager.prepare_model()
    db = SessionLocal()
    try:
        seed_default_roles(db)
    finally:
        db.close()
    camera_health_worker.start()
    yield
    # Shutdown
    camera_health_worker.stop()
    pipeline_manager.stop_all()
    engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Router
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Attach a correlation ID to every request and response."""
    candidate = request.headers.get(REQUEST_ID_HEADER, "")
    request_id = (
        candidate
        if candidate
        and len(candidate) <= MAX_REQUEST_ID_LENGTH
        and set(candidate) <= _ALLOWED_REQUEST_ID_CHARACTERS
        else uuid4().hex
    )
    request.state.request_id = request_id
    response: Response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


@app.get("/")
def root():
    """Return derived status rather than implying health from API availability alone."""
    system_health = collect_system_health()
    return {
        "application": system_health["application"],
        "overall_status": system_health["overall_status"],
        "degraded_reasons": system_health["degraded_reasons"],
        "database": system_health["database"],
        "migrations": system_health["migrations"],
        "model_state": system_health["model_state"],
        "camera_state": system_health["camera_state"],
        "camera_health_worker": system_health["camera_health_worker"],
        "inference_pipeline": system_health["inference_pipeline"],
        "validation_status": system_health["validation_status"],
        "igl_validated": system_health["igl_validated"],
        "igl_configuration_status": system_health["igl_configuration_status"],
        "measured_performance": system_health["measured_performance"],
        "platform": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs_url": "/docs",
    }

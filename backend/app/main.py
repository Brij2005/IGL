"""
FastAPI Main Application Entry Point for IGL Industrial AI Safety & Incident Intelligence Platform.
"""
import sys
import os
import bcrypt
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
    from app.models import Role, User
    from app.api import api_router
    from app.services.inference_pipeline import pipeline_manager
    from app.services.continuous_health import camera_health_worker
    from app.services.safety_event_worker import safety_event_worker
    from app.services.notification_worker import notification_delivery_worker
    from app.services.system_health import collect_system_health
except ImportError:
    from backend.app.config import settings
    from backend.app.database import engine, SessionLocal, require_database_at_migration_head
    from backend.app.models import Role, User
    from backend.app.api import api_router
    from backend.app.services.inference_pipeline import pipeline_manager
    from backend.app.services.continuous_health import camera_health_worker
    from backend.app.services.safety_event_worker import safety_event_worker
    from backend.app.services.notification_worker import notification_delivery_worker
    from backend.app.services.system_health import collect_system_health


# Reference roles. They are needed because an
# incident reporter, an assignee and a notification audience are all named by
# role, and because escalation and notification policies validate their target
# role against this list.
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
            "incidents:manage", "incidents:view",
            "near_misses:view",
            "corrective_actions:manage", "corrective_actions:view",
            "analytics:view", "plants:view", "zones:view",
            "cameras:view", "evidence:view"
        ]
    },
    {
        "name": "PLANT_MANAGER",
        "description": "Plant manager overseeing operations, analytics and audit logs",
        "permissions_json": [
            "plants:view", "zones:view", "cameras:view",
            "events:view", "incidents:view", "near_misses:view",
            "corrective_actions:view", "analytics:view", "audit_logs:view",
            "evidence:view"
        ]
    },
    {
        "name": "OPERATOR",
        "description": "Plant floor operator monitoring live feeds and acknowledging alerts",
        "permissions_json": [
            "cameras:view", "cameras:view_live",
            "events:view", "events:acknowledge",
            "zones:view", "evidence:view"
        ]
    }
]


def seed_reference_roles(db: Session):
    """Seed the reference role records if they are missing. Idempotent.

    This is configuration data, not account creation: it creates no user, stores
    no credential and asks for no input.
    """
    added_role = False
    for role_data in DEFAULT_ROLES:
        existing = db.query(Role).filter(Role.name == role_data["name"]).first()
        if not existing:
            new_role = Role(
                name=role_data["name"],
                description=role_data["description"],
                permissions_json=role_data["permissions_json"]
            )
            db.add(new_role)
            added_role = True
    if added_role:
        db.commit()


def seed_initial_admin(db: Session):
    """Create the configured first administrator only when none exists."""
    if not settings.INITIAL_ADMIN_USERNAME:
        return
    admin_role = db.query(Role).filter(Role.name == "ADMIN").first()
    if admin_role is None:
        raise RuntimeError("ADMIN reference role is unavailable")
    if db.query(User.id).join(Role).filter(Role.name == "ADMIN").first():
        return
    password = settings.INITIAL_ADMIN_PASSWORD.get_secret_value()
    db.add(User(
        username=settings.INITIAL_ADMIN_USERNAME,
        email=settings.INITIAL_ADMIN_EMAIL,
        full_name=settings.INITIAL_ADMIN_FULL_NAME,
        role_id=admin_role.id,
        hashed_password=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii"),
        password_changed_at=datetime.now(timezone.utc),
        is_active=True,
    ))
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events management.

    Startup verifies the migration head, prepares configured model weights,
    seeds roles, and optionally creates the operator-configured first admin.
    """
    require_database_at_migration_head()
    pipeline_manager.prepare_model()
    db = SessionLocal()
    try:
        seed_reference_roles(db)
        seed_initial_admin(db)
        if not settings.ALLOW_ANONYMOUS_ACCESS and db.query(User.id).join(Role).filter(Role.name == "ADMIN", User.is_active.is_(True)).count() == 0:
            raise RuntimeError("Authentication requires an active ADMIN; configure INITIAL_ADMIN_* for first startup")
    finally:
        db.close()
    camera_health_worker.start()
    # Escalation evaluation is off unless an operator enables it; the worker
    # reports NOT_CONFIGURED in that case rather than silently doing nothing.
    safety_event_worker.start()
    notification_delivery_worker.start()
    yield
    # Shutdown
    safety_event_worker.stop()
    notification_delivery_worker.stop()
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
        "access_control": system_health["access_control"],
        "authentication": settings.authentication_state(),
        "validation_status": system_health["validation_status"],
        "igl_validated": system_health["igl_validated"],
        "igl_configuration_status": system_health["igl_configuration_status"],
        "measured_performance": system_health["measured_performance"],
        "platform": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs_url": "/docs",
    }

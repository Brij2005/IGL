"""
FastAPI Main Application Entry Point for IGL Industrial AI Safety & Incident Intelligence Platform.
"""
import sys
import os
from pathlib import Path
from contextlib import asynccontextmanager
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

# Ensure the project and backend packages resolve from either supported launch directory.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, BACKEND_DIR)

try:
    from app.config import settings
    from app.database import engine, SessionLocal
    from app.models import Role
    from app.api import api_router
    from app.services.inference_pipeline import pipeline_manager
    from app.services.continuous_health import camera_health_worker
    from app.services.system_health import collect_system_health
except ImportError:
    from backend.app.config import settings
    from backend.app.database import engine, SessionLocal
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


def require_database_at_migration_head() -> None:
    """Refuse startup when the configured schema has not been explicitly migrated."""
    backend_dir = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_dir / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_dir / "migrations"))
    expected_heads = set(ScriptDirectory.from_config(alembic_config).get_heads())
    with engine.connect() as connection:
        current_heads = set(MigrationContext.configure(connection).get_current_heads())
    if current_heads != expected_heads:
        raise RuntimeError(
            "Database migration is missing or out of date; run "
            "'alembic -c backend/alembic.ini upgrade head' before starting the API"
        )


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


@app.get("/")
def root():
    """Return derived status rather than implying health from API availability alone."""
    system_health = collect_system_health()
    return {
        "status": system_health["overall_status"],
        "api": system_health["api"],
        "database": system_health["database"],
        "migrations": system_health["migrations"],
        "model_status": system_health["model"]["status"],
        "igl_configuration_status": system_health["igl_configuration_status"],
        "platform": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs_url": "/docs"
    }

"""
FastAPI Main Application Entry Point for IGL Industrial AI Safety & Incident Intelligence Platform.
"""
import sys
import os
from contextlib import asynccontextmanager
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
    from app.database import init_db, SessionLocal
    from app.models import Role
    from app.api import api_router
    from app.services.inference_pipeline import pipeline_manager
except ImportError:
    from backend.app.config import settings
    from backend.app.database import init_db, SessionLocal
    from backend.app.models import Role
    from backend.app.api import api_router
    from backend.app.services.inference_pipeline import pipeline_manager


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
            "incidents:manage", "corrective_actions:manage", "analytics:view"
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
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events management."""
    # Startup: Initialize DB tables and seed roles
    engine = init_db()
    pipeline_manager.prepare_model()
    db = SessionLocal()
    try:
        seed_default_roles(db)
    finally:
        db.close()
    yield
    # Shutdown
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
    """Health check root endpoint."""
    return {
        "status": "ONLINE",
        "platform": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs_url": "/docs"
    }

"""
API route package initializer.
"""
from fastapi import APIRouter

try:
    from app.api.auth import router as auth_router
    from app.api.cameras import router as cameras_router
except ImportError:
    from backend.app.api.auth import router as auth_router
    from backend.app.api.cameras import router as cameras_router

api_router = APIRouter()
api_router.include_router(auth_router, prefix="/auth", tags=["Authentication & RBAC"])
api_router.include_router(cameras_router, prefix="/cameras", tags=["Camera Management & Health"])

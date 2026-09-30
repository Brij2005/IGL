"""
Camera Management and Health Telemetry API Routes.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

try:
    from app.database import get_db
    from app.models import User
    from app.schemas_camera import CameraCreate, CameraUpdate, CameraOut, CameraHealthOut
    from app.services.camera_manager import CameraManager
    from app.services.health_monitor import health_monitor
    from app.services.video_ingestion import ingestion_manager
    from app.auth import get_current_active_user, require_role
except ImportError:
    from backend.app.database import get_db
    from backend.app.models import User
    from backend.app.schemas_camera import CameraCreate, CameraUpdate, CameraOut, CameraHealthOut
    from backend.app.services.camera_manager import CameraManager
    from backend.app.services.health_monitor import health_monitor
    from backend.app.services.video_ingestion import ingestion_manager
    from backend.app.auth import get_current_active_user, require_role


router = APIRouter()


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
def register_camera(
    camera_in: CameraCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("ADMIN", "SAFETY_OFFICER"))
):
    """
    Register a new CCTV / RTSP / IP camera feed (Requires ADMIN or SAFETY_OFFICER role).
    """
    try:
        camera = CameraManager.create_camera(db, camera_in)
        # Start ingestion stream reader
        ingestion_manager.start_stream(camera.id, camera.stream_url, camera.fps)
        return camera
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("", response_model=List[CameraOut])
def list_cameras(
    zone_id: Optional[str] = None,
    active_only: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user)
):
    """
    List all cameras filtered by zone or active status.
    """
    cameras = CameraManager.list_cameras(db, zone_id=zone_id, active_only=active_only)
    return cameras


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(
    camera_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user)
):
    """
    Get camera details and attached health status.
    """
    camera = CameraManager.get_camera(db, camera_id)
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Camera not found"
        )
    return camera


@router.put("/{camera_id}", response_model=CameraOut)
def update_camera(
    camera_id: str,
    camera_in: CameraUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("ADMIN"))
):
    """
    Update camera configuration or RTSP stream URL (Requires ADMIN role).
    """
    camera = CameraManager.update_camera(db, camera_id, camera_in)
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Camera not found"
        )
        
    # Restart ingestion worker if active
    if camera.is_active:
        ingestion_manager.start_stream(camera.id, camera.stream_url, camera.fps)
    else:
        ingestion_manager.stop_stream(camera.id)
        
    return camera


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_camera(
    camera_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("ADMIN"))
):
    """
    Deactivate a camera feed (Requires ADMIN role).
    """
    success = CameraManager.delete_camera(db, camera_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Camera not found"
        )
    ingestion_manager.stop_stream(camera_id)


@router.get("/{camera_id}/health", response_model=CameraHealthOut)
def get_camera_health_telemetry(
    camera_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user)
):
    """
    Perform diagnostic health evaluation and return real-time telemetry.
    """
    camera = CameraManager.get_camera(db, camera_id)
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Camera not found"
        )
        
    health = health_monitor.evaluate_camera_health(db, camera)
    return health

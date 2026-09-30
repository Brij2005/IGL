"""
Camera Management Service handling CRUD operations and initial health tracking setup.
"""
from typing import List, Optional
from sqlalchemy.orm import Session

try:
    from app.models import Camera, CameraHealth, Zone
    from app.schemas_camera import CameraCreate, CameraUpdate
    from app.utils.encrypted_url import validate_camera_url_storage
except ImportError:
    from backend.app.models import Camera, CameraHealth, Zone
    from backend.app.schemas_camera import CameraCreate, CameraUpdate
    from backend.app.utils.encrypted_url import validate_camera_url_storage


class CameraManager:
    """Service layer managing industrial CCTV / RTSP cameras."""

    @staticmethod
    def create_camera(db: Session, camera_in: CameraCreate) -> Camera:
        """Register a new industrial camera and attach initial health record."""
        validate_camera_url_storage(camera_in.stream_url)
        # Check uniqueness of camera code
        existing = db.query(Camera).filter(Camera.code == camera_in.code).first()
        if existing:
            raise ValueError(f"Camera with code '{camera_in.code}' already exists")

        # Validate zone if specified
        if camera_in.zone_id:
            zone = db.query(Zone).filter(Zone.id == camera_in.zone_id).first()
            if not zone:
                raise ValueError(f"Zone with ID '{camera_in.zone_id}' does not exist")

        camera = Camera(
            name=camera_in.name,
            code=camera_in.code,
            stream_url=camera_in.stream_url,
            camera_type=camera_in.camera_type,
            fps=camera_in.fps,
            resolution=camera_in.resolution,
            location_description=camera_in.location_description,
            zone_id=camera_in.zone_id,
            is_active=True
        )
        db.add(camera)
        db.commit()
        db.refresh(camera)

        # Initialize CameraHealth telemetry record
        health = CameraHealth(
            camera_id=camera.id,
            status="CONFIGURED",
            fps=0.0,
            latency_ms=0.0,
            is_frozen=False,
            is_black=False,
            image_quality_score=None,
            inference_status="NOT_RUNNING",
            measured_fps=None,
            frame_latency_ms=None,
            observed_resolution=None,
            dropped_frames=None,
            brightness_score=None,
            sharpness_score=None,
        )
        db.add(health)
        db.commit()
        db.refresh(camera)

        return camera

    @staticmethod
    def get_camera(db: Session, camera_id: str) -> Optional[Camera]:
        """Fetch camera by ID."""
        return db.query(Camera).filter(Camera.id == camera_id).first()

    @staticmethod
    def get_camera_by_code(db: Session, code: str) -> Optional[Camera]:
        """Fetch camera by unique code."""
        return db.query(Camera).filter(Camera.code == code).first()

    @staticmethod
    def list_cameras(
        db: Session,
        zone_id: Optional[str] = None,
        active_only: bool = True
    ) -> List[Camera]:
        """List cameras filtered by zone or active status."""
        query = db.query(Camera)
        if active_only:
            query = query.filter(Camera.is_active.is_(True))
        if zone_id:
            query = query.filter(Camera.zone_id == zone_id)
        return query.all()

    @staticmethod
    def update_camera(db: Session, camera_id: str, camera_in: CameraUpdate) -> Optional[Camera]:
        """Update camera settings, stream URL, or zone assignment."""
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            return None

        update_data = camera_in.model_dump(exclude_unset=True)
        if "stream_url" in update_data:
            validate_camera_url_storage(update_data["stream_url"])
        for field, value in update_data.items():
            setattr(camera, field, value)

        db.commit()
        db.refresh(camera)
        return camera

    @staticmethod
    def delete_camera(db: Session, camera_id: str) -> bool:
        """Deactivate camera (soft delete)."""
        camera = db.query(Camera).filter(Camera.id == camera_id).first()
        if not camera:
            return False

        camera.is_active = False
        db.commit()
        return True

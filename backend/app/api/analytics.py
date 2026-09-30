"""Summary analytics derived exclusively from persisted records."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

try:
    from app.auth import require_permission
    from app.database import get_db
    from app.models import Camera, CameraHealth, CorrectiveAction, Event, Incident, NearMiss, User
except ImportError:
    from backend.app.auth import require_permission
    from backend.app.database import get_db
    from backend.app.models import Camera, CameraHealth, CorrectiveAction, Event, Incident, NearMiss, User


router = APIRouter()


@router.get("/summary")
def get_summary_analytics(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("analytics:view")),
):
    camera_count = db.query(Camera.id).count()
    return {
        "data_status": "AVAILABLE" if camera_count else "NO_DATA",
        "camera_count": camera_count,
        "online_camera_count": db.query(CameraHealth.camera_id).filter(CameraHealth.status == "ONLINE").count(),
        "event_count": db.query(Event.id).count(),
        "verified_event_count": db.query(Event.id).filter(Event.observation_state == "CONFIRMED").count(),
        "incident_count": db.query(Incident.id).count(),
        "near_miss_count": db.query(NearMiss.id).count(),
        "corrective_action_count": db.query(CorrectiveAction.id).count(),
        "accuracy_metrics_status": "NOT_MEASURED",
        "igl_validated": False,
    }
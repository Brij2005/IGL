"""Authenticated notifications; external channels remain queued as unconfigured."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

try:
    from app.auth import get_current_active_user, require_role
    from app.database import get_db
    from app.models import Notification, User
    from app.services.notification_engine import enqueue_notification
except ImportError:
    from backend.app.auth import get_current_active_user, require_role
    from backend.app.database import get_db
    from backend.app.models import Notification, User
    from backend.app.services.notification_engine import enqueue_notification


router = APIRouter()


class NotificationRequest(BaseModel):
    channel: Literal["DASHBOARD", "EMAIL", "WEBHOOK"] = "DASHBOARD"
    recipient: str | None = Field(default=None, max_length=255)


@router.get("")
def list_my_notifications(
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination")
    items = db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()
    return items


@router.post("/events/{event_id}", status_code=201)
def create_event_notification(
    event_id: str,
    request: NotificationRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("ADMIN", "SAFETY_OFFICER")),
):
    try:
        notification = enqueue_notification(
            db,
            event_id=event_id,
            user_id=user.id,
            channel=request.channel,
            recipient=request.recipient,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return notification

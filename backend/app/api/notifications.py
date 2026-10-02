"""Notifications; external channels remain unconfigured unless a transport exists."""
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

try:
    from app.access_control import log_audit_event, require_permission, require_role
    from app.database import get_db
    from app.models import Notification, User
    from app.schemas_system import NotificationChannelStatusOut, NotificationOut
    from app.services.notification_engine import channel_status, due_notifications, enqueue_notification
except ImportError:
    from backend.app.access_control import log_audit_event, require_permission, require_role
    from backend.app.database import get_db
    from backend.app.models import Notification, User
    from backend.app.schemas_system import NotificationChannelStatusOut, NotificationOut
    from backend.app.services.notification_engine import channel_status, due_notifications, enqueue_notification


router = APIRouter()


class NotificationRequest(BaseModel):
    channel: Literal["DASHBOARD", "EMAIL", "WHATSAPP", "WEBHOOK", "SMS", "TEAMS", "BUZZER"] = "DASHBOARD"
    recipient: str | None = Field(default=None, max_length=255)
    # The audience is a role or a label, not a signed-in user.
    recipient_role: str | None = Field(default=None, max_length=50)
    dedup_window_seconds: int = Field(default=300, ge=0, le=86400)


class TestDeliveryRequest(BaseModel):
    recipient: str = Field(..., min_length=3, max_length=255)


class TestDeliveryResult(BaseModel):
    channel: str
    status: str
    provider: str
    recipient: str
    message_id: str | None = None
    error: str | None = None


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    recipient_role: str | None = Query(default=None, max_length=50),
    event_id: str | None = Query(default=None, max_length=36),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    """List the notification queue.

    No request is authenticated, so this is the whole queue rather than one
    person's queue. It can be filtered by recipient role or by event.
    """
    query = db.query(Notification)
    if recipient_role:
        query = query.filter(Notification.recipient_role == recipient_role)
    if event_id:
        query = query.filter(Notification.event_id == event_id)
    return query.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()


@router.post("/events/{event_id}", response_model=NotificationOut, status_code=201)
def create_event_notification(
    event_id: str,
    payload: NotificationRequest,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER")),
):
    try:
        notification = enqueue_notification(
            db,
            event_id=event_id,
            user_id=actor.id if actor else None,
            recipient_role=payload.recipient_role,
            channel=payload.channel,
            recipient=payload.recipient,
            dedup_window_seconds=payload.dedup_window_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return notification


@router.get("/channels/status", response_model=list[NotificationChannelStatusOut])
def get_channel_status(
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    """Report real transport configuration and queue state per channel."""
    return channel_status(db)


@router.get("/delivery-queue", response_model=list[NotificationOut])
def get_due_delivery_queue(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("events:view")),
):
    """Notifications whose next delivery attempt is due.

    This is an operator view of work waiting to be sent, not a report of what
    has been delivered.
    """
    return due_notifications(db, limit=limit)


@router.post("/test/email", response_model=TestDeliveryResult)
def test_email_delivery(
    payload: TestDeliveryRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER")),
):
    """Send a real one-off SMTP test message; never reports simulated success."""
    try:
        from app.services.notification_delivery import send_email
    except ImportError:
        from backend.app.services.notification_delivery import send_email
    result = send_email([payload.recipient], "IGL Safety Intelligence test", "SMTP test requested by an operator.")
    log_audit_event(db, actor.id if actor else None, "EMAIL_TEST_ATTEMPTED", "NOTIFICATION_TRANSPORT", details_json={"status": result["status"]}, ip_address=request.client.host if request.client else None)
    return {"channel": "EMAIL", "recipient": payload.recipient, **result}


@router.post("/test/whatsapp", response_model=TestDeliveryResult)
def test_whatsapp_delivery(
    payload: TestDeliveryRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role("ADMIN", "SAFETY_OFFICER")),
):
    """Send a real one-off WhatsApp Cloud API test message."""
    try:
        from app.services.notification_delivery import send_whatsapp
    except ImportError:
        from backend.app.services.notification_delivery import send_whatsapp
    result = send_whatsapp(payload.recipient, "IGL Safety Intelligence test message.")
    log_audit_event(db, actor.id if actor else None, "WHATSAPP_TEST_ATTEMPTED", "NOTIFICATION_TRANSPORT", details_json={"status": result["status"]}, ip_address=request.client.host if request.client else None)
    return {"channel": "WHATSAPP", "recipient": payload.recipient, **result}

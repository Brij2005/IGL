"""Truthful database-backed in-app notification queue and adapter status."""
from __future__ import annotations

from sqlalchemy.orm import Session

try:
    from app.models import Event, Notification, User
except ImportError:
    from backend.app.models import Event, Notification, User


SUPPORTED_CHANNELS = {"DASHBOARD", "EMAIL", "WEBHOOK"}


class NotificationConfigurationError(ValueError):
    pass


def enqueue_notification(
    db: Session,
    *,
    event_id: str,
    user_id: str,
    channel: str = "DASHBOARD",
    recipient: str | None = None,
) -> Notification:
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise ValueError("Notification requires an existing event")
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if user is None:
        raise ValueError("Notification recipient must be an active user")
    if channel not in SUPPORTED_CHANNELS:
        raise NotificationConfigurationError("Notification channel is unsupported")

    status = "QUEUED" if channel == "DASHBOARD" else "NOT_CONFIGURED"
    notification = Notification(
        event_id=event_id,
        user_id=user_id,
        channel=channel,
        recipient=recipient,
        status=status,
        error_message=None if channel == "DASHBOARD" else "External sender is not configured",
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification

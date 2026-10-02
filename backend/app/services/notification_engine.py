"""Truthful database-backed in-app notification queue with delivery bookkeeping.

Delivery rules this module keeps:

* A notification is only ``SENT`` when a delivery attempt actually succeeded.
* A channel with no configured sender stays ``NOT_CONFIGURED``. Nothing is
  reported as delivered by an integration that was never configured or never
  reached.
* Notifications are addressed to an identity, a role or a label. This build has
  no authentication, so there is no signed-in recipient to fall back on.
* Retries are visible: ``retry_count``, ``last_attempt_at`` and
  ``next_attempt_at`` record the schedule, and a failure records why.
* Repeated alerts for one event inside a dedup window are suppressed instead of
  multiplying into an unmanageable alert storm.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.models import Event, Notification, User
except ImportError:
    from backend.app.config import settings
    from backend.app.models import Event, Notification, User


# Channels this build can actually deliver. Anything else stays
# NOT_CONFIGURED instead of pretending a sender exists.
SUPPORTED_CHANNELS = {"DASHBOARD", "EMAIL", "WEBHOOK", "SMS", "TEAMS", "BUZZER"}
# Channels that need a real, operator-configured transport.
EXTERNAL_CHANNELS = {"EMAIL", "WEBHOOK", "SMS", "TEAMS", "BUZZER"}

TERMINAL_STATES = {"SENT", "FAILED", "NOT_CONFIGURED", "NOT_IMPLEMENTED"}


class NotificationConfigurationError(ValueError):
    pass


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _external_transport_configured(channel: str) -> bool:
    """Whether an operator has supplied a transport for an external channel."""
    if channel == "EMAIL":
        return bool(settings.SMTP_HOST) and bool(settings.NOTIFICATION_FROM_ADDRESS)
    if channel == "WEBHOOK":
        # SecretStr is always truthy as an object, so the secret must be unwrapped
        # before deciding whether a URL was actually supplied.
        return settings.WEBHOOK_URL is not None and bool(
            settings.WEBHOOK_URL.get_secret_value().strip()
        )
    # SMS, TEAMS and BUZZER have no transport wiring in this build.
    return False


def build_dedup_key(event_id: str, channel: str, recipient: Optional[str], window_seconds: int) -> str:
    return f"{event_id}|{channel}|{recipient or 'role-default'}|{window_seconds}s"


def enqueue_notification(
    db: Session,
    *,
    event_id: str,
    user_id: str | None = None,
    recipient_role: str | None = None,
    channel: str = "DASHBOARD",
    recipient: str | None = None,
    dedup_window_seconds: int = 300,
    payload_summary: str | None = None,
) -> Notification:
    """Queue a notification for an event and an audience.

    The audience is a specific identity (``user_id``), a role
    (``recipient_role``) or a free-form label (``recipient``). At least one must
    be supplied: a notification with no audience has nobody to reach, and
    queuing one would imply a delivery that cannot happen.

    ``payload_summary`` is operator-visible context only; it never carries
    stream credentials or raw frame data.
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if event is None:
        raise ValueError("Notification requires an existing event")
    if not (user_id or recipient_role or recipient):
        raise ValueError("Notification requires a recipient identity, role or label")
    if user_id is not None:
        user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
        if user is None:
            raise ValueError("Notification recipient must be an active identity")
    if channel not in SUPPORTED_CHANNELS:
        raise NotificationConfigurationError("Notification channel is unsupported")

    dedup_key = build_dedup_key(
        event_id,
        channel,
        recipient or recipient_role or user_id,
        dedup_window_seconds,
    )
    if dedup_window_seconds > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=dedup_window_seconds)
        duplicate = (
            db.query(Notification)
            .filter(
                Notification.dedup_key == dedup_key,
                Notification.created_at >= cutoff,
                Notification.status.in_(["QUEUED", "SENT", "RETRYING"]),
            )
            .first()
        )
        if duplicate is not None:
            return duplicate

    if channel in EXTERNAL_CHANNELS:
        transport_configured = _external_transport_configured(channel)
        status = "NOT_IMPLEMENTED" if transport_configured else "NOT_CONFIGURED"
        error_message = (
            f"{channel} transport settings are present, but a delivery sender is not implemented"
            if transport_configured
            else f"No {channel} transport is configured for this deployment"
        )
    else:
        status = "QUEUED"
        error_message = None

    notification = Notification(
        event_id=event_id,
        user_id=user_id,
        recipient_role=recipient_role,
        channel=channel,
        recipient=recipient,
        status=status,
        error_message=error_message,
        dedup_key=dedup_key,
        provider=None,
        payload_summary=payload_summary,
        max_attempts=settings.NOTIFICATION_MAX_ATTEMPTS,
        next_attempt_at=None if status == "NOT_CONFIGURED" else datetime.now(timezone.utc),
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


def record_attempt(
    db: Session,
    notification: Notification,
    *,
    succeeded: bool,
    provider: str | None = None,
    error_message: str | None = None,
    now: datetime | None = None,
) -> Notification:
    """Record one delivery attempt outcome against the queue row.

    ``succeeded`` must reflect a real delivery confirmation from the transport.
    Callers must not pass ``succeeded=True`` without one.
    """
    reference = _as_utc(now) or datetime.now(timezone.utc)
    notification.last_attempt_at = reference
    notification.retry_count = (notification.retry_count or 0) + 1
    notification.provider = provider or notification.provider

    if succeeded:
        notification.status = "SENT"
        notification.sent_at = reference
        notification.next_attempt_at = None
        notification.error_message = None
    else:
        notification.error_message = error_message or "Delivery attempt failed"
        if notification.retry_count >= (notification.max_attempts or settings.NOTIFICATION_MAX_ATTEMPTS):
            notification.status = "FAILED"
            notification.next_attempt_at = None
        else:
            notification.status = "RETRYING"
            delay = settings.NOTIFICATION_RETRY_BASE_SECONDS * (2 ** (notification.retry_count - 1))
            delay = min(delay, settings.NOTIFICATION_RETRY_MAX_SECONDS)
            notification.next_attempt_at = reference + timedelta(seconds=delay)

    db.commit()
    db.refresh(notification)
    return notification


def due_notifications(db: Session, now: datetime | None = None, limit: int = 100) -> List[Notification]:
    """Queued or retrying notifications whose next attempt is due."""
    reference = _as_utc(now) or datetime.now(timezone.utc)
    return (
        db.query(Notification)
        .filter(Notification.status.in_(["QUEUED", "RETRYING"]))
        .filter(
            (Notification.next_attempt_at.is_(None))
            | (Notification.next_attempt_at <= reference)
        )
        .order_by(Notification.created_at)
        .limit(limit)
        .all()
    )


def channel_status(db: Session) -> List[dict]:
    """Report each channel's real configuration and queue state."""
    rows = db.query(Notification).all()
    report = []
    for channel in sorted(SUPPORTED_CHANNELS):
        channel_rows = [row for row in rows if row.channel == channel]
        if channel in EXTERNAL_CHANNELS:
            configured = _external_transport_configured(channel)
            state = "NOT_IMPLEMENTED" if configured else "NOT_CONFIGURED"
        else:
            state = "CONFIGURED"
        report.append(
            {
                "channel": channel,
                "configuration_state": state,
                "queued": sum(1 for row in channel_rows if row.status in ("QUEUED", "RETRYING")),
                "sent": sum(1 for row in channel_rows if row.status == "SENT"),
                "failed": sum(1 for row in channel_rows if row.status == "FAILED"),
                "not_configured": sum(1 for row in channel_rows if row.status == "NOT_CONFIGURED"),
            }
        )
    return report

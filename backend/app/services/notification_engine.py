"""Truthful database-backed in-app notification queue with delivery bookkeeping.

Delivery rules this module keeps:

* A notification is only ``SENT`` when a delivery attempt actually succeeded.
* A channel with no configured sender stays ``NOT_CONFIGURED``. Nothing is
  reported as delivered by an integration that was never configured or never
  reached.
* Notifications are addressed to an identity, a role or a label. Local
  anonymous mode may create unattributed notification records.
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
SUPPORTED_CHANNELS = {"DASHBOARD", "EMAIL", "WHATSAPP", "WEBHOOK", "SMS", "TEAMS", "BUZZER"}
# Channels that need a real, operator-configured transport.
EXTERNAL_CHANNELS = {"EMAIL", "WHATSAPP", "WEBHOOK", "SMS", "TEAMS", "BUZZER"}

TERMINAL_STATES = {"SENT", "FAILED", "NOT_CONFIGURED", "NOT_IMPLEMENTED"}

# External delivery vocabulary. The persisted Notification.status values above
# stay unchanged; this layer only translates them into the words an operator
# reads on a dashboard or in an API response.
DELIVERED = "DELIVERED"
DELIVERY_FAILED = "DELIVERY_FAILED"
NOT_CONFIGURED = "NOT_CONFIGURED"
NOT_ATTEMPTED = "NOT_ATTEMPTED"
PENDING = "PENDING"
STATUS_UNKNOWN = "STATUS_UNKNOWN"

_STATUS_GROUPS = {
    DELIVERED: {"SENT"},
    DELIVERY_FAILED: {"FAILED", "RETRYING", "DELIVERY_FAILED"},
    NOT_CONFIGURED: {"NOT_CONFIGURED", "NOT_IMPLEMENTED"},
    PENDING: {"QUEUED", "SENDING"},
}


class NotificationConfigurationError(ValueError):
    pass


def _status_group(status: str | None, channel: str) -> str:
    """Fold a persisted or transport status into one of the external groups."""
    normalized = (status or "").strip().upper()
    if not normalized:
        return NOT_ATTEMPTED
    # The transport layer reports channel-scoped names for the same outcomes.
    normalized = normalized.removeprefix(f"{channel.upper()}_") if channel else normalized
    for group, members in _STATUS_GROUPS.items():
        if normalized in members:
            return group
    return STATUS_UNKNOWN


def delivery_status_for(channel: str, status: str | None) -> str:
    """The operator-facing delivery status for a channel and a stored status.

    ``EMAIL_SENT`` maps to ``EMAIL_DELIVERED``, a failed or retrying attempt to
    ``EMAIL_DELIVERY_FAILED``, an unconfigured or unimplemented channel to
    ``EMAIL_NOT_CONFIGURED``. ``None`` maps to ``<CHANNEL>_NOT_ATTEMPTED``: no
    attempt exists, so no delivery may be claimed.
    """
    group = _status_group(status, channel)
    return f"{(channel or 'UNKNOWN').strip().upper()}_{group}"


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _external_transport_configured(channel: str) -> bool:
    """Whether an operator has supplied a transport for an external channel."""
    if channel == "EMAIL":
        password = settings.SMTP_PASSWORD.get_secret_value().strip() if settings.SMTP_PASSWORD else ""
        return (
            bool(settings.SMTP_HOST)
            and bool(settings.NOTIFICATION_FROM_ADDRESS)
            and (bool(settings.SMTP_USERNAME) == bool(password))
        )
    if channel == "WHATSAPP":
        return bool(settings.WHATSAPP_ACCESS_TOKEN and settings.WHATSAPP_ACCESS_TOKEN.get_secret_value().strip()) and bool(
            settings.WHATSAPP_PHONE_NUMBER_ID and settings.WHATSAPP_API_VERSION
            and settings.WHATSAPP_RECIPIENTS
        )
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
        implemented = channel in {"EMAIL", "WHATSAPP"}
        status = "QUEUED" if transport_configured and implemented else (
            "NOT_IMPLEMENTED" if transport_configured else "NOT_CONFIGURED"
        )
        error_message = (
            None if transport_configured and implemented else
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
        next_attempt_at=datetime.now(timezone.utc) if status == "QUEUED" else None,
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
    provider_message_id: str | None = None,
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
    notification.provider_message_id = provider_message_id or notification.provider_message_id

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
        if channel in {"EMAIL", "WHATSAPP"}:
            configured = _external_transport_configured(channel)
            state = "CONFIGURED" if configured else "NOT_CONFIGURED"
        elif channel in EXTERNAL_CHANNELS:
            configured = _external_transport_configured(channel)
            state = "NOT_IMPLEMENTED" if configured else "NOT_CONFIGURED"
        else:
            state = "CONFIGURED"
        report.append(
            {
                "channel": channel,
                "configuration_state": state,
                # delivery_status reflects the newest persisted row only, and
                # last_delivery_status the newest row that was actually
                # attempted. Both stay None or NOT_ATTEMPTED until a real
                # attempt exists, so a configured channel is never shown as
                # delivered on the strength of its settings.
                "delivery_status": _channel_delivery_status(channel, channel_rows, state),
                "last_delivery_status": _last_delivery_status(channel, channel_rows),
                "queued": sum(1 for row in channel_rows if row.status in ("QUEUED", "RETRYING", "SENDING")),
                "sent": sum(1 for row in channel_rows if row.status == "SENT"),
                "failed": sum(1 for row in channel_rows if row.status == "FAILED"),
                "not_configured": sum(1 for row in channel_rows if row.status == "NOT_CONFIGURED"),
                "retrying": sum(1 for row in channel_rows if row.status == "RETRYING"),
            }
        )
    return report


def _channel_delivery_status(channel: str, channel_rows: List[Notification], state: str) -> str:
    if channel_rows:
        newest = max(channel_rows, key=lambda row: (_sort_key(row.created_at), row.id))
        return delivery_status_for(channel, newest.status)
    # With no queue row there is nothing delivered: an unconfigured channel says
    # so, and a channel without any attempt stays pending rather than delivered.
    return delivery_status_for(channel, "NOT_CONFIGURED" if state in ("NOT_CONFIGURED", "NOT_IMPLEMENTED") else "QUEUED")


def _last_delivery_status(channel: str, channel_rows: List[Notification]) -> Optional[str]:
    attempted = [row for row in channel_rows if row.last_attempt_at is not None]
    if not attempted:
        return None
    newest = max(attempted, key=lambda row: (_sort_key(row.last_attempt_at), row.id))
    return delivery_status_for(channel, newest.status)


def _sort_key(value: Optional[datetime]) -> str:
    """Order timestamps without comparing mixed naive and aware values."""
    return value.isoformat() if isinstance(value, datetime) else ""

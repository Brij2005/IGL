"""Real SMTP and WhatsApp Cloud API delivery with secret-safe failures."""
from __future__ import annotations

import logging
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

import httpx
from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.models import Event, Notification, Role, User
    # delivery_status_for is re-exported here so callers can read the external
    # delivery vocabulary from the delivery module alone.
    from app.services.notification_engine import delivery_status_for, due_notifications, record_attempt
    from app.services.notification_templates import message_kind, render_email, render_whatsapp
except ImportError:  # pragma: no cover
    from backend.app.config import settings
    from backend.app.models import Event, Notification, Role, User
    from backend.app.services.notification_engine import delivery_status_for, due_notifications, record_attempt
    from backend.app.services.notification_templates import message_kind, render_email, render_whatsapp


logger = logging.getLogger("igl.notifications.delivery")
PHONE_RE = re.compile(r"^\+[1-9][0-9]{7,14}$")


def _result(status: str, provider: str, error: str | None = None, message_id: str | None = None) -> dict[str, Any]:
    return {"status": status, "provider": provider, "error": error, "message_id": message_id}


def send_email(recipients: list[str], subject: str, body: str) -> dict[str, Any]:
    """Send over configured SMTP. A success means SMTP accepted the message."""
    if not settings.SMTP_HOST or not settings.NOTIFICATION_FROM_ADDRESS:
        return _result("EMAIL_NOT_CONFIGURED", "SMTP", "SMTP host and sender address are required")
    sender = settings.NOTIFICATION_FROM_ADDRESS.strip()
    if "\r" in sender or "\n" in sender or parseaddr(sender)[1] != sender or "@" not in sender:
        return _result("EMAIL_NOT_CONFIGURED", "SMTP", "A valid sender email address is required")
    password = settings.SMTP_PASSWORD.get_secret_value().strip() if settings.SMTP_PASSWORD else ""
    if bool(settings.SMTP_USERNAME) != bool(password):
        return _result("EMAIL_NOT_CONFIGURED", "SMTP", "SMTP username and password must be configured together")
    clean_recipients = sorted({address.strip() for address in recipients if address.strip()})
    if not clean_recipients:
        return _result("EMAIL_NOT_CONFIGURED", "SMTP", "No email recipient is configured")
    for address in clean_recipients:
        if "\r" in address or "\n" in address or parseaddr(address)[1] != address or "@" not in address:
            return _result("DELIVERY_FAILED", "SMTP", "Invalid email recipient")

    message = EmailMessage()
    message["From"] = settings.NOTIFICATION_FROM_ADDRESS
    message["To"] = ", ".join(clean_recipients)
    message["Subject"] = subject.replace("\r", " ").replace("\n", " ")
    message.set_content(body)
    try:
        smtp_type = smtplib.SMTP_SSL if settings.SMTP_USE_SSL else smtplib.SMTP
        kwargs = {"context": ssl.create_default_context()} if settings.SMTP_USE_SSL else {}
        with smtp_type(settings.SMTP_HOST, settings.SMTP_PORT, timeout=settings.SMTP_TIMEOUT_SECONDS, **kwargs) as client:
            client.ehlo()
            if settings.SMTP_USE_TLS and not settings.SMTP_USE_SSL:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if settings.SMTP_USERNAME and password:
                client.login(settings.SMTP_USERNAME, password)
            refused = client.send_message(message, to_addrs=clean_recipients)
            if refused:
                return _result("DELIVERY_FAILED", "SMTP", "SMTP server refused one or more recipients")
        return _result("SENT", "SMTP")
    except (OSError, smtplib.SMTPException, ssl.SSLError) as exc:
        # Exception strings from SMTP libraries may echo server responses or addresses.
        logger.warning("SMTP delivery failed", extra={"component": "notification_delivery", "error_type": type(exc).__name__})
        return _result("DELIVERY_FAILED", "SMTP", f"SMTP_{type(exc).__name__.upper()}")


def send_whatsapp(recipient: str, body: str) -> dict[str, Any]:
    """Send one WhatsApp message through Meta's official Cloud API."""
    token = settings.WHATSAPP_ACCESS_TOKEN.get_secret_value().strip() if settings.WHATSAPP_ACCESS_TOKEN else ""
    if (not token or not settings.WHATSAPP_PHONE_NUMBER_ID or not settings.WHATSAPP_API_VERSION
            or not re.fullmatch(r"[0-9]{5,30}", settings.WHATSAPP_PHONE_NUMBER_ID)
            or not re.fullmatch(r"v[0-9]{1,3}\.0", settings.WHATSAPP_API_VERSION)):
        return _result("WHATSAPP_NOT_CONFIGURED", "WHATSAPP_CLOUD_API", "Cloud API token, phone number ID and API version are required")
    target = recipient.strip()
    if not PHONE_RE.fullmatch(target):
        return _result("DELIVERY_FAILED", "WHATSAPP_CLOUD_API", "Recipient must be an E.164 phone number")
    url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {"messaging_product": "whatsapp", "to": target, "type": "text", "text": {"body": body[:4000]}}
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=settings.WHATSAPP_TIMEOUT_SECONDS,
        )
        if not 200 <= response.status_code < 300:
            logger.warning("WhatsApp provider rejected a message", extra={"component": "notification_delivery", "http_status": response.status_code})
            return _result("DELIVERY_FAILED", "WHATSAPP_CLOUD_API", f"PROVIDER_HTTP_{response.status_code}")
        try:
            data = response.json()
            messages = data.get("messages") or []
            message_id = messages[0].get("id") if messages else None
        except (ValueError, AttributeError, TypeError):
            message_id = None
        return _result("SENT", "WHATSAPP_CLOUD_API", message_id=message_id)
    except httpx.TimeoutException:
        return _result("DELIVERY_FAILED", "WHATSAPP_CLOUD_API", "PROVIDER_TIMEOUT")
    except httpx.HTTPError as exc:
        logger.warning("WhatsApp request failed", extra={"component": "notification_delivery", "error_type": type(exc).__name__})
        return _result("DELIVERY_FAILED", "WHATSAPP_CLOUD_API", f"PROVIDER_{type(exc).__name__.upper()}")


def _log_unsubstituted(channel: str, names: list[str]) -> None:
    """Record template gaps by name only; values are never logged."""
    if names:
        logger.warning(
            "Notification template left placeholders unsubstituted",
            extra={"component": "notification_delivery", "channel": channel, "placeholders": ",".join(names)},
        )


def _event_for(db: Session, notification: Notification) -> Event | None:
    if not notification.event_id:
        return None
    return db.query(Event).filter(Event.id == notification.event_id).first()


def _event_message(db: Session, notification: Notification) -> tuple[str, str]:
    """Render the operator-visible message for a queued event notification.

    An event-backed row is rendered from that event's real facts through the
    configured template; a row without an event falls back to the queued payload
    summary. A placeholder with no fact stays visible and is logged by name.
    """
    event = _event_for(db, notification)
    if event is None:
        return "IGL Safety Intelligence notification", notification.payload_summary or "Operator test notification."
    message = render_email(message_kind(event), event)
    _log_unsubstituted(notification.channel, message.not_substituted)
    return message.subject, message.body


def test_message(channel: str) -> tuple[str, str]:
    """Render the operator test message for a channel from the templates."""
    if channel == "WHATSAPP":
        message = render_whatsapp("TEST")
        _log_unsubstituted("WHATSAPP", message.not_substituted)
        return message.body, message.body
    message = render_email("TEST")
    _log_unsubstituted("EMAIL", message.not_substituted)
    return message.subject, message.body


def _email_recipients(db: Session, notification: Notification) -> list[str]:
    result: list[str] = []
    if notification.recipient and not notification.recipient_role and not notification.user_id:
        result.append(notification.recipient)
    if notification.user_id:
        user = db.query(User).filter(User.id == notification.user_id, User.is_active.is_(True)).first()
        if user and user.email:
            result.append(user.email)
    if notification.recipient_role:
        result.extend(
            item[0] for item in db.query(User.email)
            .join(Role, User.role_id == Role.id)
            .filter(Role.name == notification.recipient_role, User.is_active.is_(True))
            .all()
        )
    specific = sorted({item.strip() for item in result if item and item.strip()})
    if specific:
        return specific
    return sorted({item.strip() for item in settings.NOTIFICATION_RECIPIENTS if item.strip()})


def _whatsapp_recipients(notification: Notification) -> list[str]:
    values = list(settings.WHATSAPP_RECIPIENTS)
    if notification.recipient and not notification.recipient_role and not notification.user_id:
        values.append(notification.recipient)
    return sorted({item.strip() for item in values if item and item.strip()})


def deliver_notification(db: Session, notification: Notification) -> dict[str, Any]:
    """Attempt delivery through a configured transport; returns provider facts."""
    if notification.channel == "EMAIL":
        subject, body = _event_message(db, notification)
        if notification.payload_summary:
            body += f"\n\nDetails: {notification.payload_summary}"
        recipients = _email_recipients(db, notification)
        if not recipients:
            return _result("EMAIL_NOT_CONFIGURED", "SMTP", "No email recipient is configured")
        return send_email(recipients, subject, body)
    if notification.channel == "WHATSAPP":
        recipients = _whatsapp_recipients(notification)
        if not recipients:
            return _result("WHATSAPP_NOT_CONFIGURED", "WHATSAPP_CLOUD_API", "No WhatsApp recipient is configured")
        body = _whatsapp_message(db, notification)
        outcomes = [send_whatsapp(recipient, body) for recipient in recipients]
        failed = [outcome for outcome in outcomes if outcome["status"] != "SENT"]
        if failed:
            return _result("DELIVERY_FAILED", "WHATSAPP_CLOUD_API", failed[0]["error"])
        ids = ",".join(outcome["message_id"] for outcome in outcomes if outcome.get("message_id")) or None
        return _result("SENT", "WHATSAPP_CLOUD_API", message_id=ids)
    return _result("NOT_IMPLEMENTED", notification.channel, "No sender is implemented for this channel")


def _whatsapp_message(db: Session, notification: Notification) -> str:
    """Render the WhatsApp text for a queued row from real event facts."""
    event = _event_for(db, notification)
    if event is None:
        message = render_whatsapp("INCIDENT", None)
        body = notification.payload_summary or "IGL Safety Intelligence notification"
    else:
        message = render_whatsapp(message_kind(event), event)
        body = message.body
    _log_unsubstituted("WHATSAPP", message.not_substituted)
    if notification.payload_summary:
        body += f"\n\nDetails: {notification.payload_summary}"
    return body


def deliver_due_notifications(db: Session, limit: int = 25) -> dict[str, int]:
    """Process queued messages with an atomic claim to reduce duplicate sends."""
    from sqlalchemy import or_, update

    rows = due_notifications(db, limit=limit)
    summary = {"claimed": 0, "sent": 0, "failed": 0, "not_configured": 0}
    now = datetime.now(timezone.utc)
    for row in rows:
        claim = db.execute(
            update(Notification)
            .where(Notification.id == row.id, Notification.status.in_(["QUEUED", "RETRYING"]))
            .where(or_(Notification.next_attempt_at.is_(None), Notification.next_attempt_at <= now))
            .values(status="SENDING", last_attempt_at=now)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        if claim.rowcount != 1:
            continue
        summary["claimed"] += 1
        db.refresh(row)
        try:
            outcome = deliver_notification(db, row)
        except Exception as exc:
            logger.error("Notification delivery attempt failed", extra={"component": "notification_delivery", "error_type": type(exc).__name__})
            outcome = _result("DELIVERY_FAILED", row.channel, f"DELIVERY_{type(exc).__name__.upper()}")
        if outcome["status"] in ("EMAIL_NOT_CONFIGURED", "WHATSAPP_NOT_CONFIGURED"):
            row.status = "NOT_CONFIGURED"
            row.error_message = outcome["error"]
            row.provider = outcome["provider"]
            row.next_attempt_at = None
            db.commit()
            summary["not_configured"] += 1
        else:
            record_attempt(
                db,
                row,
                succeeded=outcome["status"] == "SENT",
                provider=outcome["provider"],
                provider_message_id=outcome.get("message_id"),
                error_message=outcome["error"],
            )
            summary["sent" if outcome["status"] == "SENT" else "failed"] += 1
    return summary

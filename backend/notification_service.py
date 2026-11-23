import json
import os
import smtplib
import time
import urllib.parse
import urllib.request
import uuid
from email.message import EmailMessage

from database import get_db_connection, now_iso


def _get_setting(key, default=""):
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def _record_delivery(alert_id, incident_id, channel, recipient, status, provider_message_id="", error="", attempts=1):
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO notification_deliveries
        (alert_id, incident_id, channel, recipient, status, provider_message_id, attempts, error_message, sent_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (alert_id, incident_id, channel, recipient, status, provider_message_id, attempts, error, now_iso(), now_iso()),
    )
    conn.execute(
        """
        INSERT INTO notification_logs (event_id, channel, recipient, status, provider_message_id, message_text, error_message, attempts, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (alert_id or incident_id, channel, recipient, status, provider_message_id, "", error, attempts, now_iso()),
    )
    conn.commit()
    conn.close()


def _record_in_app(alert_id, incident_id, event_data):
    _record_delivery(alert_id, incident_id, "IN_APP", "dashboard", "SUCCESS", alert_id or incident_id or "", attempts=1)
    return {"success": True, "status": "SUCCESS", "message": "Alert stored in dashboard database"}


def _send_email(event_data):
    host = os.getenv("SMTP_HOST", "")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", username)
    recipient = os.getenv("ALERT_EMAIL_TO", "")
    if not (host and sender and recipient):
        return {"success": False, "status": "NOT_CONFIGURED", "message": "SMTP is not configured"}
    message = EmailMessage()
    message["Subject"] = f"IGL VisionGuard Alert: {event_data.get('event_type', 'Detection')}"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        f"Camera: {event_data.get('camera_name', event_data.get('camera_id', ''))}\n"
        f"Zone: {event_data.get('zone_name', 'General Area')}\n"
        f"Confidence: {event_data.get('confidence', 0)}\n"
        f"Time: {event_data.get('timestamp', now_iso())}\n"
    )
    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(message)
        return {"success": True, "status": "SUCCESS", "message": "Email accepted by SMTP server"}
    except Exception as exc:
        return {"success": False, "status": "FAILED", "message": str(exc)}


def _send_telegram(event_data):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not (token and chat_id):
        return {"success": False, "status": "NOT_CONFIGURED", "message": "Telegram is not configured"}
    text = (
        f"IGL VisionGuard alert\n"
        f"Type: {event_data.get('event_type', '')}\n"
        f"Camera: {event_data.get('camera_name', event_data.get('camera_id', ''))}\n"
        f"Zone: {event_data.get('zone_name', '')}\n"
        f"Confidence: {event_data.get('confidence', 0)}"
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=payload), timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
            if body.get("ok"):
                return {"success": True, "status": "SUCCESS", "message": "Telegram accepted message", "provider_message_id": str(body.get("result", {}).get("message_id", ""))}
            return {"success": False, "status": "FAILED", "message": body.get("description", "Telegram rejected message")}
    except Exception as exc:
        return {"success": False, "status": "FAILED", "message": str(exc)}


def _send_sms(event_data):
    endpoint = os.getenv("SMS_PROVIDER_URL", "")
    recipient = os.getenv("ALERT_SMS_TO", "")
    api_key = os.getenv("SMS_PROVIDER_API_KEY", "")
    if not (endpoint and recipient):
        return {"success": False, "status": "NOT_CONFIGURED", "message": "SMS provider is not configured"}
    payload = json.dumps(
        {
            "to": recipient,
            "from": os.getenv("SMS_PROVIDER_SENDER", "IGL"),
            "message": f"IGL alert {event_data.get('event_type', '')} at {event_data.get('camera_name', '')}",
            "api_key": api_key,
        }
    ).encode("utf-8")
    try:
        request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
            if str(body.get("status", "")).lower() in {"success", "ok", "accepted"} or body.get("id"):
                return {"success": True, "status": "SUCCESS", "message": "SMS provider accepted message", "provider_message_id": str(body.get("id", ""))}
            return {"success": False, "status": "FAILED", "message": str(body)}
    except Exception as exc:
        return {"success": False, "status": "FAILED", "message": str(exc)}


def _send_whatsapp(event_data):
    try:
        from whatsapp_service import send_whatsapp_alert

        return send_whatsapp_alert(event_data)
    except Exception as exc:
        return {"success": False, "status": "FAILED", "message": str(exc)}


PROVIDERS = {
    "IN_APP": _record_in_app,
    "EMAIL": _send_email,
    "SMS": _send_sms,
    "TELEGRAM": _send_telegram,
    "WHATSAPP": _send_whatsapp,
}


def notify(alert_id=None, incident_id=None, event_data=None, channels=None):
    event_data = event_data or {}
    requested = channels or ["IN_APP"]
    max_attempts = max(1, int(os.getenv("NOTIFICATION_MAX_ATTEMPTS", "3")))
    results = []
    for channel in requested:
        provider = PROVIDERS.get(channel.upper())
        if not provider:
            results.append({"channel": channel, "success": False, "status": "UNKNOWN_CHANNEL"})
            continue
        attempts = 0
        result = None
        while attempts < max_attempts:
            attempts += 1
            try:
                if channel.upper() == "IN_APP":
                    result = provider(alert_id, incident_id, event_data)
                else:
                    result = provider(event_data)
            except Exception as exc:
                result = {"success": False, "status": "FAILED", "message": str(exc)}
            status = result.get("status", "FAILED")
            if result.get("success") or status in {"NOT_CONFIGURED", "SIMULATED"}:
                break
            if attempts < max_attempts:
                time.sleep(min(2.0 * attempts, 5.0))
        if channel.upper() != "IN_APP":
            status = result.get("status", "FAILED")
            provider_message_id = result.get("provider_message_id", "")
            _record_delivery(
                alert_id,
                incident_id,
                channel.upper(),
                event_data.get("recipient", ""),
                status,
                provider_message_id,
                result.get("message", ""),
                attempts=attempts,
            )
        results.append({"channel": channel.upper(), **result, "attempts": attempts})
    return {"results": results}


def notification_status():
    return {
        "in_app": {"configured": True, "mode": "database"},
        "email": {"configured": bool(os.getenv("SMTP_HOST") and os.getenv("ALERT_EMAIL_TO"))},
        "sms": {"configured": bool(os.getenv("SMS_PROVIDER_URL") and os.getenv("ALERT_SMS_TO"))},
        "telegram": {"configured": bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))},
        "whatsapp": {"configured": bool(os.getenv("WHATSAPP_ACCESS_TOKEN") and os.getenv("WHATSAPP_PHONE_NUMBER_ID"))},
    }


def recent_deliveries(limit=100):
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM notification_deliveries ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]
"""Physical alarm actuation over configurable industrial transports.

This module contains the *architecture* for driving a relay, siren, GPIO line or
industrial controller from a confirmed safety alarm. It is deliberately strict
about what it reports:

* With no transport configured the state is ``PHYSICAL_ALARM_NOT_CONFIGURED``.
* With a transport configured but its driver library or hardware absent the state
  is ``ACTUATOR_NOT_CONNECTED`` and the reason names the missing dependency.
* ``ACTIVATED`` is written only after the real transport call returned success.

Nothing here fabricates activation. A siren that is not wired cannot be
reported as sounding, and an MQTT broker that is unreachable is reported as a
failed attempt rather than a successful alert.
"""
from __future__ import annotations

import logging
import time
from typing import Any

try:
    from app.config import settings
except ImportError:  # pragma: no cover - import shim for direct script use
    from backend.app.config import settings


logger = logging.getLogger("igl.alarm.physical")

PHYSICAL_NOT_CONFIGURED = "PHYSICAL_ALARM_NOT_CONFIGURED"
ACTUATOR_NOT_CONNECTED = "ACTUATOR_NOT_CONNECTED"
ACTIVATION_ATTEMPTED = "ACTIVATION_ATTEMPTED"
ACTIVATED = "ACTIVATED"
ACTIVATION_FAILED = "ACTIVATION_FAILED"
DEACTIVATED = "DEACTIVATED"
DEACTIVATION_FAILED = "DEACTIVATION_FAILED"
NOT_ATTEMPTED = "NOT_ATTEMPTED"

TRANSPORT_HTTP = "HTTP_ACTUATOR"
TRANSPORT_MQTT = "MQTT_ACTUATOR"
TRANSPORT_SERIAL = "SERIAL_RELAY"
TRANSPORT_GPIO = "GPIO_RELAY"


def _result(state: str, transport: str, reason: str | None = None) -> dict[str, Any]:
    return {"state": state, "transport": transport, "reason": reason}


def configured_transports() -> list[str]:
    """Return the transports an operator has configured, in evaluation order."""
    transports: list[str] = []
    if settings.PHYSICAL_ALARM_HTTP_URL:
        transports.append(TRANSPORT_HTTP)
    if settings.PHYSICAL_ALARM_MQTT_HOST and settings.PHYSICAL_ALARM_MQTT_TOPIC:
        transports.append(TRANSPORT_MQTT)
    if settings.PHYSICAL_ALARM_SERIAL_PORT:
        transports.append(TRANSPORT_SERIAL)
    if settings.PHYSICAL_ALARM_GPIO_PIN > 0:
        transports.append(TRANSPORT_GPIO)
    return transports


def actuator_status() -> dict[str, Any]:
    """Report what is configured without claiming anything is connected."""
    transports = configured_transports()
    if not settings.ALARM_PHYSICAL_ACTUATION or not transports:
        return {
            "state": PHYSICAL_NOT_CONFIGURED,
            "enabled": settings.ALARM_PHYSICAL_ACTUATION,
            "configured_transports": transports,
            "reason": (
                "ALARM_PHYSICAL_ACTUATION is disabled"
                if not settings.ALARM_PHYSICAL_ACTUATION
                else "No physical alarm transport is configured"
            ),
            "hardware_verified": False,
        }
    return {
        "state": ACTUATOR_NOT_CONNECTED,
        "enabled": True,
        "configured_transports": transports,
        "reason": "TRANSPORT_CONFIGURED_HARDWARE_NOT_VERIFIED",
        "hardware_verified": False,
    }


def _payload(alarm: dict[str, Any], action: str) -> dict[str, Any]:
    """Build the transport payload from real alarm facts only."""
    return {
        "action": action,
        "source": "igl-safety-intelligence",
        "alarm_id": alarm.get("alarm_id"),
        "event_id": alarm.get("event_id"),
        "incident_id": alarm.get("incident_id"),
        "camera_id": alarm.get("camera_id"),
        "zone_id": alarm.get("zone_id"),
        "severity": alarm.get("severity"),
        "detector_key": alarm.get("detector_key"),
        "confidence": alarm.get("confidence"),
        "model_name": alarm.get("model_name"),
        "model_version": alarm.get("model_version"),
        "raised_at": alarm.get("raised_at"),
    }


def _http_actuate(alarm: dict[str, Any], action: str) -> dict[str, Any]:
    import httpx

    url = settings.PHYSICAL_ALARM_HTTP_URL.get_secret_value()
    headers = {"Content-Type": "application/json"}
    token = settings.PHYSICAL_ALARM_HTTP_BEARER_TOKEN.get_secret_value() if settings.PHYSICAL_ALARM_HTTP_BEARER_TOKEN else ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    method = settings.PHYSICAL_ALARM_HTTP_METHOD
    try:
        with httpx.Client(timeout=settings.PHYSICAL_ALARM_HTTP_TIMEOUT_SECONDS) as client:
            response = client.request(method, url, json=_payload(alarm, action), headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Physical alarm HTTP call failed", extra={"component": "physical_alarm", "error_type": type(exc).__name__})
        return _result(ACTIVATION_FAILED, TRANSPORT_HTTP, f"HTTP_{type(exc).__name__.upper()}")
    if 200 <= response.status_code < 300:
        return _result(ACTIVATED, TRANSPORT_HTTP, f"HTTP_{response.status_code}")
    return _result(ACTIVATION_FAILED, TRANSPORT_HTTP, f"PROVIDER_HTTP_{response.status_code}")


def _mqtt_actuate(alarm: dict[str, Any], action: str) -> dict[str, Any]:
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return _result(ACTUATOR_NOT_CONNECTED, TRANSPORT_MQTT, "MQTT_CLIENT_LIBRARY_NOT_INSTALLED")
    import json

    password = settings.PHYSICAL_ALARM_MQTT_PASSWORD.get_secret_value() if settings.PHYSICAL_ALARM_MQTT_PASSWORD else ""
    try:
        client = mqtt.Client()
        if settings.PHYSICAL_ALARM_MQTT_USE_TLS:
            client.tls_set()
        if settings.PHYSICAL_ALARM_MQTT_USERNAME:
            client.username_pw_set(settings.PHYSICAL_ALARM_MQTT_USERNAME, password)
        client.connect(settings.PHYSICAL_ALARM_MQTT_HOST, settings.PHYSICAL_ALARM_MQTT_PORT, keepalive=10)
        client.loop_start()
        info = client.publish(settings.PHYSICAL_ALARM_MQTT_TOPIC, json.dumps(_payload(alarm, action)), qos=1)
        info.wait_for_publish(timeout=settings.PHYSICAL_ALARM_HTTP_TIMEOUT_SECONDS)
        delivered = info.is_published()
        client.loop_stop()
        client.disconnect()
    except Exception as exc:  # noqa: BLE001 - transport failures must never raise into the safety path
        logger.warning("Physical alarm MQTT publish failed", extra={"component": "physical_alarm", "error_type": type(exc).__name__})
        return _result(ACTIVATION_FAILED, TRANSPORT_MQTT, f"MQTT_{type(exc).__name__.upper()}")
    if not delivered:
        return _result(ACTIVATION_FAILED, TRANSPORT_MQTT, "BROKER_ACK_TIMEOUT")
    return _result(ACTIVATED, TRANSPORT_MQTT, "BROKER_ACKNOWLEDGED")


def _serial_actuate(alarm: dict[str, Any], action: str) -> dict[str, Any]:
    try:
        import serial
    except ImportError:
        return _result(ACTUATOR_NOT_CONNECTED, TRANSPORT_SERIAL, "PYSERIAL_NOT_INSTALLED")
    try:
        with serial.Serial(settings.PHYSICAL_ALARM_SERIAL_PORT, settings.PHYSICAL_ALARM_SERIAL_BAUD, timeout=2) as port:
            if action == "ACTIVATE":
                # Hold the relay asserted for the configured pulse so a latching
                # or momentary siren actually sounds, then release it.
                port.write(b"HIGH\n")
                port.flush()
                time.sleep(settings.PHYSICAL_ALARM_SERIAL_PULSE_SECONDS)
                port.write(b"LOW\n")
                port.flush()
            else:
                port.write(b"LOW\n")
                port.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Physical alarm serial write failed", extra={"component": "physical_alarm", "error_type": type(exc).__name__})
        return _result(ACTIVATION_FAILED, TRANSPORT_SERIAL, f"SERIAL_{type(exc).__name__.upper()}")
    return _result(ACTIVATED, TRANSPORT_SERIAL, "SERIAL_PULSE_COMPLETED")


def _gpio_actuate(alarm: dict[str, Any], action: str) -> dict[str, Any]:
    try:
        import RPi.GPIO as GPIO
    except ImportError:
        return _result(ACTUATOR_NOT_CONNECTED, TRANSPORT_GPIO, "RPI_GPIO_NOT_INSTALLED")
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(settings.PHYSICAL_ALARM_GPIO_PIN, GPIO.OUT)
        level = action == "ACTIVATE"
        if not settings.PHYSICAL_ALARM_GPIO_ACTIVE_HIGH:
            level = not level
        GPIO.output(settings.PHYSICAL_ALARM_GPIO_PIN, level)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Physical alarm GPIO write failed", extra={"component": "physical_alarm", "error_type": type(exc).__name__})
        return _result(ACTIVATION_FAILED, TRANSPORT_GPIO, f"GPIO_{type(exc).__name__.upper()}")
    return _result(ACTIVATED, TRANSPORT_GPIO, "GPIO_LINE_DRIVEN")


_TRANSPORTS = {
    TRANSPORT_HTTP: _http_actuate,
    TRANSPORT_MQTT: _mqtt_actuate,
    TRANSPORT_SERIAL: _serial_actuate,
    TRANSPORT_GPIO: _gpio_actuate,
}


def actuate(alarm: dict[str, Any], action: str = "ACTIVATE") -> dict[str, Any]:
    """Attempt real physical actuation for one alarm.

    Returns the first successful transport result, or the first explicit
    non-success result. ``ACTIVATED`` is only ever returned from a real call.
    """
    if action not in ("ACTIVATE", "DEACTIVATE"):
        return _result(NOT_ATTEMPTED, "NONE", "UNSUPPORTED_ACTION")
    if not settings.ALARM_PHYSICAL_ACTUATION:
        return _result(PHYSICAL_NOT_CONFIGURED, "NONE", "ALARM_PHYSICAL_ACTUATION_DISABLED")
    transports = configured_transports()
    if not transports:
        return _result(PHYSICAL_NOT_CONFIGURED, "NONE", "NO_TRANSPORT_CONFIGURED")
    failures: list[dict[str, Any]] = []
    for transport in transports:
        outcome = _TRANSPORTS[transport](alarm, action)
        if outcome["state"] == ACTIVATED:
            # A successful call reports ACTIVATED for an assertion and
            # DEACTIVATED for a release, so a caller can tell which happened.
            return _result(ACTIVATED if action == "ACTIVATE" else DEACTIVATED, transport, outcome["reason"])
        failures.append(outcome)
    if all(item["state"] == ACTUATOR_NOT_CONNECTED for item in failures):
        return _result(ACTUATOR_NOT_CONNECTED, failures[0]["transport"], failures[0]["reason"])
    if action == "DEACTIVATE" and any(item["state"] == ACTIVATION_FAILED for item in failures):
        return _result(DEACTIVATION_FAILED, failures[0]["transport"], failures[0]["reason"])
    return _result(ACTIVATION_FAILED, failures[0]["transport"], failures[0]["reason"])

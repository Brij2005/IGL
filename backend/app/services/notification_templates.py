"""Operator-reviewable plain-text templates for notification messages.

The rules this module keeps, because a notification is evidence that reaches a
human outside the plant network:

* Only the fixed placeholder set in :data:`PLACEHOLDERS` can ever be
  substituted. A name outside that set is never filled, even when a caller puts
  a matching key in the context mapping, so a template cannot reach a password,
  an access token or any other setting value.
* Every substituted value comes from the context mapping, which is built from a
  real ``Event`` row. Nothing is invented or guessed.
* An unknown ``{placeholder}`` is left visible in the output and reported in the
  ``not_substituted`` list so a missing fact stays visible instead of being
  silently blanked.
* A placeholder whose fact does not exist renders the documented sentinel for
  that placeholder (``NOT_MEASURED`` for confidence, ``NOT_AVAILABLE`` for the
  rest), never a plausible-looking substitute.

Placeholders:

======================  ==========================================================
``event_id``           Event row id (always present).
``event_type``         Event type, e.g. ``PPE_NON_COMPLIANCE``.
``severity``           LOW / MEDIUM / HIGH / CRITICAL.
``state``              Observation state, e.g. ``CONFIRMED`` or ``NOT_VALIDATED``.
``workflow_state``     NEW / UNACKNOWLEDGED / ... / CLOSED.
``camera_id``          Camera the event came from (always present).
``zone_id``            Zone id, ``NOT_AVAILABLE`` when the event has no zone.
``confidence``         Detector confidence, ``NOT_MEASURED`` when null.
``started_at``         ISO-8601 timestamp of the event start.
``detector_key``       Detector that produced the event.
``model_name``         Model that produced the event.
``model_version``      Model version string of the event.
``incident_id``        First incident linked to the event, if any.
``alarm_id``           First alarm linked to the event, if any.
``notes``              Operator note on the event's acknowledgement or
                       assignment, if any.
======================  ==========================================================
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Mapping, NamedTuple, Optional, Tuple

try:
    from app.config import settings
except ImportError:  # pragma: no cover
    from backend.app.config import settings


# The only names a template may substitute. Anything else is left in the text
# and reported as NOT_SUBSTITUTED.
PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "event_id",
        "event_type",
        "severity",
        "state",
        "workflow_state",
        "camera_id",
        "zone_id",
        "confidence",
        "started_at",
        "detector_key",
        "model_name",
        "model_version",
        "incident_id",
        "alarm_id",
        "notes",
    }
)

# Message kinds a template can be rendered for.
TEMPLATE_KINDS: Tuple[str, ...] = ("INCIDENT", "ESCALATION", "TEST")

# Honest sentinels for facts that do not exist. They are part of the contract:
# an operator reading a message can tell a missing fact from a real value.
NOT_MEASURED = "NOT_MEASURED"
NOT_AVAILABLE = "NOT_AVAILABLE"

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class RenderedMessage(NamedTuple):
    """A rendered message plus the placeholders that were left unsubstituted."""

    subject: str
    body: str
    not_substituted: list


def placeholders_in(template: Optional[str]) -> list:
    """Placeholder names used by a template, in first-appearance order."""
    if not template:
        return []
    names: list = []
    for match in PLACEHOLDER_RE.finditer(template):
        name = match.group(1)
        if name not in names:
            names.append(name)
    return names


def render(template: Optional[str], context: Mapping[str, Any]) -> Tuple[str, list]:
    """Substitute known placeholders from ``context``.

    Returns ``(text, not_substituted)``. A name outside :data:`PLACEHOLDERS`, or
    one with no usable value in ``context``, is left in the text exactly as
    written and named in ``not_substituted``.

    Substitution is a single pass, so a value that itself contains braces is
    never re-scanned as a placeholder.
    """
    if not template:
        return "", []
    not_substituted: list = []

    def substitute(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in PLACEHOLDERS:
            not_substituted.append(name)
            return match.group(0)
        value = context.get(name)
        if value is None:
            not_substituted.append(name)
            return match.group(0)
        return str(value)

    return PLACEHOLDER_RE.sub(substitute, template), not_substituted


def _confidence(value: Any) -> Optional[str]:
    """A measured confidence, or ``NOT_MEASURED`` when no measurement exists."""
    if value is None:
        return NOT_MEASURED
    return str(value)


def _related_id(related: Optional[Iterable[Any]]) -> Optional[str]:
    if not related:
        return None
    for item in related:
        identifier = getattr(item, "id", None)
        if identifier:
            return str(identifier)
    return None


def _event_notes(event: Any) -> str:
    for attribute in ("acknowledgements", "assignments"):
        records = getattr(event, attribute, None)
        if not records:
            continue
        for record in records:
            note = getattr(record, "notes", None)
            if note and note.strip():
                return str(note)
    return NOT_AVAILABLE


def event_context(event: Any) -> dict:
    """Build the placeholder context from one real ``Event`` row.

    Only persisted columns and relationships of that event are read. Nothing is
    read from the environment, so no setting value can reach a template.
    """
    if event is None:
        return {}
    started_at = getattr(event, "started_at", None)
    return {
        "event_id": getattr(event, "id", None),
        "event_type": getattr(event, "event_type", None),
        "severity": getattr(event, "severity", None),
        "state": getattr(event, "observation_state", None),
        "workflow_state": getattr(event, "workflow_state", None),
        "camera_id": getattr(event, "camera_id", None),
        "zone_id": getattr(event, "zone_id", None) or NOT_AVAILABLE,
        "confidence": _confidence(getattr(event, "confidence", None)),
        "started_at": started_at.isoformat() if isinstance(started_at, datetime) else NOT_AVAILABLE,
        "detector_key": getattr(event, "detector_key", None) or NOT_AVAILABLE,
        "model_name": getattr(event, "model_name", None) or NOT_AVAILABLE,
        "model_version": getattr(event, "model_version", None) or NOT_AVAILABLE,
        "incident_id": _related_id(getattr(event, "incidents", None)) or NOT_AVAILABLE,
        "alarm_id": _related_id(getattr(event, "alarms", None)) or NOT_AVAILABLE,
        "notes": _event_notes(event),
    }


# Defaults used when a template setting is None. They reproduce the plain-text
# messages the delivery layer sent before templates existed.
DEFAULT_INCIDENT_SUBJECT = "{severity} safety event: {event_type}"
DEFAULT_INCIDENT_BODY = (
    "Safety event: {event_type}\nSeverity: {severity}\n"
    "State: {workflow_state}\nEvent ID: {event_id}\n"
    "Started at: {started_at}\nCamera ID: {camera_id}\n"
    "Confidence: {confidence}"
)
DEFAULT_ESCALATION_SUBJECT = "ESCALATED {severity} safety event: {event_type}"
DEFAULT_EMAIL_TEST_BODY = "SMTP test requested by an operator."
DEFAULT_WHATSAPP_TEST_BODY = "IGL Safety Intelligence operator test message."


def default_incident_body(event: Any = None) -> str:
    """The default incident body, with the zone line only when a zone exists."""
    body = DEFAULT_INCIDENT_BODY
    zone_id = getattr(event, "zone_id", None) if event is not None else None
    if zone_id:
        body += "\nZone ID: {zone_id}"
    return body


def default_escalation_body(event: Any = None) -> str:
    return "ESCALATED safety event:\n\n" + default_incident_body(event)


def default_whatsapp_body(event: Any = None) -> str:
    return default_incident_body(event)


def render_email(kind: str, event: Any = None) -> RenderedMessage:
    """Render an operator email for ``INCIDENT``, ``ESCALATION`` or ``TEST``.

    A ``None`` template setting falls back to the plain-text default, so a
    deployment that configures no templates keeps the current behaviour.
    """
    normalized = (kind or "INCIDENT").strip().upper()
    context = event_context(event)
    if normalized == "TEST":
        subject = "IGL Safety Intelligence test"
        body = settings.EMAIL_TEST_BODY_TEMPLATE or DEFAULT_EMAIL_TEST_BODY
    elif normalized == "ESCALATION":
        subject = settings.EMAIL_ESCALATION_SUBJECT_TEMPLATE or DEFAULT_ESCALATION_SUBJECT
        body = settings.EMAIL_ESCALATION_BODY_TEMPLATE or default_escalation_body(event)
    else:
        subject = settings.EMAIL_INCIDENT_SUBJECT_TEMPLATE or DEFAULT_INCIDENT_SUBJECT
        body = settings.EMAIL_INCIDENT_BODY_TEMPLATE or default_incident_body(event)
    rendered_subject, subject_missing = render(subject, context)
    rendered_body, body_missing = render(body, context)
    return RenderedMessage(rendered_subject, rendered_body, subject_missing + body_missing)


def render_whatsapp(kind: str, event: Any = None) -> RenderedMessage:
    """Render the WhatsApp text for ``INCIDENT``, ``ESCALATION`` or ``TEST``."""
    normalized = (kind or "INCIDENT").strip().upper()
    context = event_context(event)
    if normalized == "TEST":
        body = settings.WHATSAPP_TEST_TEMPLATE or DEFAULT_WHATSAPP_TEST_BODY
    elif normalized == "ESCALATION":
        body = settings.WHATSAPP_ESCALATION_TEMPLATE or ("ESCALATED " + default_whatsapp_body(event))
    else:
        body = settings.WHATSAPP_INCIDENT_TEMPLATE or default_whatsapp_body(event)
    rendered_body, missing = render(body, context)
    return RenderedMessage("", rendered_body, missing)


def message_kind(event: Any) -> str:
    """Whether an event message is an incident message or an escalation.

    An event with a persisted escalation is escalated; anything else is an
    incident. No policy is evaluated here and nothing is inferred.
    """
    return "ESCALATION" if getattr(event, "escalations", None) else "INCIDENT"
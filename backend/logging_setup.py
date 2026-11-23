"""Structured JSON application logging.

Emits one JSON object per line so logs can be shipped to any log aggregator
(Docker json-file, Loki, CloudWatch, ...) without a parsing sidecar.

Usage:
    from logging_setup import setup_logging, get_logger
    setup_logging()
    log = get_logger(__name__)
    log.info("camera.started", extra={"camera_id": "CAM-1"})

Sensitive values (passwords, tokens, API keys, credentials) are redacted before
output so secrets never reach the logs.
"""

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credentials",
    "credentials_json",
    "access_token",
    "secret_key",
}

_REDACTED = "***REDACTED***"
_SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)(\"?\s*[:=]\s*\"?)([^\s,\"}]+)"),
]


def _redact(value):
    """Recursively redact sensitive keys from a structure."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS:
                cleaned[key] = _REDACTED
            else:
                cleaned[key] = _redact(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        text = value
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
        return text
    return value


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Merge structured extras (anything not a standard LogRecord attribute),
        # redacting sensitive keys before output.
        standard = set(
            vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
        ) | {"message", "asctime", "taskName"}
        extras = {
            key: value
            for key, value in vars(record).items()
            if key not in standard and not key.startswith("_")
        }
        payload.update(_redact(extras))
        return json.dumps(payload, default=str)


def setup_logging(level: str = None) -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    resolved = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    root = logging.getLogger()
    root.setLevel(resolved)

    # Replace any pre-existing handlers so output is not duplicated.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

    # Quiet noisy third-party loggers but keep warnings.
    for noisy in ("uvicorn.access", "httpx", "urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
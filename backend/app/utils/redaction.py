"""Redact sensitive values from validation and diagnostic output."""
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def safe_source_identifier(source: str) -> str:
    """Return a URL without credentials/query or a local filename without its path."""
    parsed = urlsplit(source)
    if parsed.scheme.lower() in {"rtsp", "rtsps", "http", "https"}:
        host = parsed.hostname or "redacted-host"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        userinfo = "***:***@" if parsed.username or parsed.password else ""
        return urlunsplit((parsed.scheme, f"{userinfo}{host}", parsed.path or "/", "", ""))
    return Path(source).name
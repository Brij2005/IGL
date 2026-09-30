"""Redact sensitive values from validation and diagnostic output."""
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

REDACTED = "REDACTED"
URL_SCHEMES = {"rtsp", "rtsps", "http", "https"}


def safe_source_identifier(source: str) -> str:
    """Return a redacted identifier that still shows that credentials existed.

    Any value that cannot be parsed as a URL is reduced to its final path
    segment, so malformed or hostile input can never leak a full source string
    into a report or log line.
    """
    return _redact(source, keep_credential_placeholder=True)


def display_source_identifier(source: str) -> str:
    """Return a display identifier with user information removed entirely.

    Used for API responses, where even a placeholder should not be published.
    """
    return _redact(source, keep_credential_placeholder=False)


def _redact(source: str, *, keep_credential_placeholder: bool) -> str:
    try:
        parsed = urlsplit(source)
        if parsed.scheme.lower() not in URL_SCHEMES:
            return Path(source).name or REDACTED
        host = parsed.hostname or REDACTED
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError:
            port = None
        if port:
            host = f"{host}:{port}"
        has_credentials = bool(parsed.username or parsed.password)
        if has_credentials and keep_credential_placeholder:
            userinfo = "***:***@"
        else:
            userinfo = ""
        return urlunsplit((parsed.scheme, f"{userinfo}{host}", parsed.path or "/", "", ""))
    except ValueError:
        return Path(source).name or REDACTED
"""Authenticated encryption for camera source URLs stored in the database."""
from __future__ import annotations

from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import String, TypeDecorator

try:
    from app.config import settings
except ImportError:
    from backend.app.config import settings


ENCRYPTED_PREFIX = "enc:v1:"


def _encryption_key() -> bytes | None:
    configured = settings.CAMERA_URL_ENCRYPTION_KEY
    return configured.get_secret_value().encode("ascii") if configured else None


def camera_url_contains_secrets(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(parsed.username or parsed.password or parsed.query)


def validate_camera_url_storage(value: str) -> None:
    if camera_url_contains_secrets(value) and _encryption_key() is None:
        raise ValueError("CAMERA_URL_ENCRYPTION_KEY is required for credential- or query-bearing camera URLs")
    if _encryption_key() is not None:
        try:
            Fernet(_encryption_key())
        except (ValueError, TypeError) as exc:
            raise ValueError("CAMERA_URL_ENCRYPTION_KEY is not a valid Fernet key") from exc


class EncryptedCameraURL(TypeDecorator):
    impl = String
    cache_ok = True

    def __init__(self, length: int = 500):
        super().__init__(length=length)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        validate_camera_url_storage(value)
        key = _encryption_key()
        if key is None:
            return value
        encrypted = Fernet(key).encrypt(value.encode("utf-8")).decode("ascii")
        return ENCRYPTED_PREFIX + encrypted

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if not value.startswith(ENCRYPTED_PREFIX):
            if camera_url_contains_secrets(value) and _encryption_key() is None:
                raise RuntimeError("Stored camera credentials cannot be read without CAMERA_URL_ENCRYPTION_KEY")
            return value
        key = _encryption_key()
        if key is None:
            raise RuntimeError("CAMERA_URL_ENCRYPTION_KEY is required to read this camera source")
        try:
            return Fernet(key).decrypt(value[len(ENCRYPTED_PREFIX):].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise RuntimeError("Camera URL decryption failed; verify CAMERA_URL_ENCRYPTION_KEY") from exc
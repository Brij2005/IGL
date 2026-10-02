"""Password login and short-lived signed bearer tokens."""
from datetime import datetime, timedelta, timezone
import threading
import time

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy.orm import Session

try:
    from app.access_control import log_audit_event, resolve_actor
    from app.config import settings
    from app.database import get_db
    from app.models import Role, User
    from app.schemas import IdentityOut
except ImportError:  # pragma: no cover
    from backend.app.access_control import log_audit_event, resolve_actor
    from backend.app.config import settings
    from backend.app.database import get_db
    from backend.app.models import Role, User
    from backend.app.schemas import IdentityOut


router = APIRouter()
bearer_scheme = HTTPBearer(auto_error=False)
_attempts: dict[str, list[float]] = {}
_attempt_lock = threading.Lock()
LOGIN_WINDOW_SECONDS = 60
LOGIN_MAX_ATTEMPTS = 10
DUMMY_PASSWORD_HASH = bcrypt.hashpw(b"igl-invalid-password-check", bcrypt.gensalt())


class LoginRequest(BaseModel):
    username_or_email: str = Field(..., min_length=3, max_length=255)
    password: SecretStr = Field(..., min_length=1, max_length=72)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: IdentityOut


class PasswordChangeRequest(BaseModel):
    current_password: SecretStr = Field(..., min_length=1, max_length=72)
    new_password: SecretStr = Field(..., min_length=12, max_length=72)

    @field_validator("new_password")
    @classmethod
    def bcrypt_byte_limit(cls, value):
        if len(value.get_secret_value().encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value


def _rate_limited(key: str, now: float | None = None) -> bool:
    reference = now if now is not None else time.monotonic()
    with _attempt_lock:
        recent = [item for item in _attempts.get(key, []) if reference - item < LOGIN_WINDOW_SECONDS]
        _attempts[key] = recent
        return len(recent) >= LOGIN_MAX_ATTEMPTS


def _record_failure(key: str, now: float | None = None) -> None:
    reference = now if now is not None else time.monotonic()
    with _attempt_lock:
        recent = [item for item in _attempts.get(key, []) if reference - item < LOGIN_WINDOW_SECONDS]
        recent.append(reference)
        # Bound process-local memory under arbitrary client keys.
        if len(_attempts) > 10_000:
            _attempts.clear()
        _attempts[key] = recent


def issue_token(user: User) -> tuple[str, int]:
    secret = settings.AUTH_JWT_SECRET_KEY.get_secret_value() if settings.AUTH_JWT_SECRET_KEY else ""
    if len(secret.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="Authentication signing key is not configured")
    lifetime = settings.AUTH_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    now = datetime.now(timezone.utc)
    changed = user.password_changed_at
    if changed and changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    payload = {
        "sub": user.id,
        "iss": settings.AUTH_JWT_ISSUER,
        "iat": now,
        "exp": now + timedelta(seconds=lifetime),
        "pwd": int(changed.timestamp() * 1_000_000) if changed else 0,
    }
    return jwt.encode(payload, secret, algorithm="HS256"), lifetime


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    if not settings.AUTH_JWT_SECRET_KEY or len(settings.AUTH_JWT_SECRET_KEY.get_secret_value().encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    remote = request.client.host if request.client else "unknown"
    throttle_key = remote[:64]
    if _rate_limited(throttle_key):
        raise HTTPException(status_code=429, detail="Too many login attempts; try again later")
    user = db.query(User).filter(
        (User.username == payload.username_or_email) | (User.email == payload.username_or_email)
    ).first()
    candidate_hash = user.hashed_password.encode("ascii") if user and user.hashed_password else DUMMY_PASSWORD_HASH
    try:
        password_matches = bcrypt.checkpw(payload.password.get_secret_value().encode("utf-8"), candidate_hash)
    except (ValueError, UnicodeEncodeError):
        password_matches = False
    valid = bool(user and user.is_active and user.hashed_password and password_matches)
    if not valid:
        _record_failure(throttle_key)
        raise HTTPException(status_code=401, detail="Invalid username/email or password")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    with _attempt_lock:
        _attempts.pop(throttle_key, None)
    token, lifetime = issue_token(user)
    log_audit_event(db, user.id, "AUTHENTICATED", "USER", user.id, {"method": "PASSWORD"}, remote)
    return {"access_token": token, "expires_in": lifetime, "user": user}


@router.get("/me", response_model=IdentityOut)
def me(actor: User | None = Depends(resolve_actor)):
    if actor is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return actor


@router.post("/password", status_code=204)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User | None = Depends(resolve_actor),
):
    if actor is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        valid = bool(actor.hashed_password) and bcrypt.checkpw(
            payload.current_password.get_secret_value().encode("utf-8"), actor.hashed_password.encode("ascii")
        )
    except (ValueError, UnicodeEncodeError):
        valid = False
    if not valid:
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    actor.hashed_password = bcrypt.hashpw(payload.new_password.get_secret_value().encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    actor.password_changed_at = datetime.now(timezone.utc)
    db.commit()
    log_audit_event(db, actor.id, "PASSWORD_CHANGED", "USER", actor.id, {"method": "SELF_SERVICE"}, request.client.host if request.client else None)
    return Response(status_code=204)

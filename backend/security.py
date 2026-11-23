import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import ACCESS_TOKEN_TTL_MINUTES, ADMIN_PASSWORD, ADMIN_USERNAME, ENABLE_AUTH, SECRET_KEY
from database import get_db_connection, now_iso

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception

security_scheme = HTTPBearer(auto_error=False)


def _password_hash(password: str, salt: str = None) -> str:
    salt = salt or secrets.token_hex(16)
    iterations = 260000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${base64.b64encode(digest).decode('ascii')}"


def hash_password(password: str) -> str:
    return _password_hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations, salt, digest = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        )
        return hmac.compare_digest(base64.b64encode(candidate).decode("ascii"), digest)
    except (ValueError, TypeError):
        return False


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_access_token(user_id: str, username: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_MINUTES * 60,
        "jti": uuid.uuid4().hex,
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return f"{raw}.{_sign(raw)}"


_revoked_tokens = set()
_revoked_lock = Lock()


def revoke_token(token: str) -> None:
    """Revoke a token so it can no longer be used (logout / server-side invalidation)."""
    if not token:
        return
    with _revoked_lock:
        _revoked_tokens.add(token)


def is_token_revoked(token: str) -> bool:
    with _revoked_lock:
        return token in _revoked_tokens


def decode_access_token(token: str) -> dict:
    try:
        raw, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _sign(raw)):
            raise ValueError("invalid signature")
        if is_token_revoked(token):
            raise ValueError("token revoked")
        payload = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
        if payload.get("exp", 0) < int(time.time()):
            raise ValueError("token expired")
        return payload
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc


def ensure_admin_user() -> None:
    conn = get_db_connection()
    row = conn.execute("SELECT id FROM users WHERE username = ?", (ADMIN_USERNAME,)).fetchone()
    if row:
        conn.close()
        return
    conn.execute(
        """
        INSERT INTO users (id, username, email, password_hash, role, is_active)
        VALUES (?, ?, ?, ?, 'admin', 1)
        """,
        (
            f"USR-{uuid.uuid4().hex[:8].upper()}",
            ADMIN_USERNAME,
            os.getenv("ADMIN_EMAIL", ""),
            hash_password(ADMIN_PASSWORD),
        ),
    )
    conn.commit()
    conn.close()


def get_optional_user(credentials: HTTPAuthorizationCredentials = Depends(security_scheme), token: str = None) -> dict:
    if not ENABLE_AUTH:
        return {
            "sub": "anonymous",
            "username": "anonymous",
            "role": "admin",
        }
    raw_token = token or (credentials.credentials if credentials else None)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    payload = decode_access_token(raw_token)
    conn = get_db_connection()
    row = conn.execute("SELECT id, username, role, is_active FROM users WHERE id = ?", (payload["sub"],)).fetchone()
    conn.close()
    if not row or not row["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account is inactive")
    return {"sub": row["id"], "username": row["username"], "role": row["role"]}


def get_current_user(user: dict = Depends(get_optional_user)) -> dict:
    return user


def require_roles(*roles: str):
    def dependency(user: dict = Depends(get_optional_user)) -> dict:
        if user.get("role") not in roles and user.get("role") != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return dependency


_rate_buckets = defaultdict(deque)
_rate_lock = Lock()


def rate_limit(limit: int = 120, window_seconds: int = 60):
    def dependency(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        key = f"{client}:{request.url.path}"
        now = time.time()
        with _rate_lock:
            bucket = _rate_buckets[key]
            while bucket and bucket[0] <= now - window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit exceeded")
            bucket.append(now)

    return dependency


def _fernet():
    if Fernet is None:
        raise RuntimeError("cryptography package is required for credential encryption")
    key = base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise ValueError("Stored camera credential could not be decrypted") from exc


def client_ip(request) -> str:
    return request.client.host if request and request.client else "unknown"


def audit(user: dict, action: str, resource_type: str = None, resource_id: str = None, details=None, request=None) -> None:
    from database import log_audit

    log_audit(
        actor=user.get("username", "anonymous") if user else "anonymous",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent", "") if request else "",
    )
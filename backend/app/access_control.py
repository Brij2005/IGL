"""Request access control and audit logging for anonymous or JWT deployments.

What is deliberately *kept* is the authorization structure:

* Every endpoint declares the permission or role it requires.
* ``require_permission`` / ``require_role`` enforce those requirements against
  the JWT-authenticated user's role when anonymous access is disabled.
* Every state-changing operation is still written to the audit log. With no
  verified identity the actor is recorded as NULL and the access state is
  recorded explicitly, so an audit row never attributes an action to a person
  who cannot be identified.

Anonymous access remains available only as a local-development mode. Shared
deployments must disable it and configure the JWT signing secret.
"""
from datetime import datetime, timezone
from typing import Callable, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.database import get_db
    from app.models import AuditLog, User
    from app.models import Role
except ImportError:  # pragma: no cover - import shim for direct script use
    from backend.app.config import settings
    from backend.app.database import get_db
    from backend.app.models import AuditLog, User
    from backend.app.models import Role


# Recorded on every audit row so a reader can tell which access model produced it.
ANONYMOUS_ACCESS_STATE = "NO_AUTHENTICATION_ANONYMOUS_ACCESS"
IDENTIFIED_ACCESS_STATE = "IDENTIFIED_ACCESS"
bearer_scheme = HTTPBearer(auto_error=False)


def resolve_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Resolve a live, active user from a signed token or local anonymous mode."""
    if credentials is None:
        if settings.ALLOW_ANONYMOUS_ACCESS:
            return None
        raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"})
    secret = settings.AUTH_JWT_SECRET_KEY.get_secret_value() if settings.AUTH_JWT_SECRET_KEY else ""
    try:
        claims = jwt.decode(credentials.credentials, secret, algorithms=["HS256"], issuer=settings.AUTH_JWT_ISSUER)
        user_id = claims.get("sub")
        if not isinstance(user_id, str):
            raise ValueError("invalid subject")
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid authentication credentials", headers={"WWW-Authenticate": "Bearer"}) from None
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials", headers={"WWW-Authenticate": "Bearer"})
    changed = user.password_changed_at
    if changed and changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    if claims.get("pwd", 0) != (int(changed.timestamp() * 1_000_000) if changed else 0):
        raise HTTPException(status_code=401, detail="Session is no longer valid", headers={"WWW-Authenticate": "Bearer"})
    return user


def access_state(actor: Optional[User]) -> str:
    return IDENTIFIED_ACCESS_STATE if actor is not None else ANONYMOUS_ACCESS_STATE


def require_permission(permission: str) -> Callable:
    """Declare the permission an endpoint requires.

    The declared permission is not evaluated against an identity, because no
    identity exists. It is enforced only as far as the deployment access mode
    allows, and it is kept in the dependency tree so the requirement is never
    silently dropped from the codebase.
    """

    def dependency(actor: Optional[User] = Depends(resolve_actor)) -> Optional[User]:
        if actor is None:
            _enforce_access_mode(permission)
            return None
        role = actor.role
        permissions = role.permissions_json if role and isinstance(role.permissions_json, list) else []
        if "*" not in permissions and permission not in permissions:
            raise HTTPException(status_code=403, detail="Insufficient permission")
        return actor

    dependency.declared_permission = permission  # type: ignore[attr-defined]
    return dependency


def require_role(*allowed_roles: str) -> Callable:
    """Declare the roles permitted to use an endpoint.

    Same contract as :func`require_permission`: the role requirement is declared
    and documented, and access is granted only while the deployment runs in
    anonymous-access mode.
    """

    def dependency(actor: Optional[User] = Depends(resolve_actor)) -> Optional[User]:
        requirement = "/".join(allowed_roles) or "ROLE_REQUIRES_AUTHENTICATION"
        if actor is None:
            _enforce_access_mode(requirement)
            return None
        if actor.role is None or actor.role.name not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return actor

    dependency.declared_roles = allowed_roles  # type: ignore[attr-defined]
    return dependency


def _enforce_access_mode(requirement: str) -> None:
    if settings.ALLOW_ANONYMOUS_ACCESS:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            f"This deployment requires an authenticated user. Unmet requirement: {requirement}."
        ),
    )


def log_audit_event(
    db: Session,
    user_id: Optional[str],
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    details_json: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    """Record an immutable audit row for a state-changing operation.

    ``user_id`` is NULL whenever the acting identity is unknown. The audit entry
    also carries the access state so an unattributed action is visibly
    unattributed rather than looking like an identified one.
    """
    details = dict(details_json or {})
    details.setdefault("access_state", ANONYMOUS_ACCESS_STATE if user_id is None else IDENTIFIED_ACCESS_STATE)
    log_entry = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details_json=details,
        ip_address=ip_address,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry

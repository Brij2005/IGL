"""Request access control and audit logging for a deployment with no login.

Authentication is deliberately absent from this build. There is no login
endpoint, no password verification, no token issuance and no token validation,
so there is no verified identity to resolve for a request.

What is deliberately *kept* is the authorization structure:

* Every endpoint still declares the permission or role it requires, so the
  intended access model stays visible in the code and in the OpenAPI document
  instead of being quietly deleted.
* ``require_permission`` / ``require_role`` are the single choke point where a
  deployment that does require authenticated access would enforce it. With
  ``ALLOW_ANONYMOUS_ACCESS`` enabled they resolve to an anonymous actor.
* Every state-changing operation is still written to the audit log. With no
  verified identity the actor is recorded as NULL and the access state is
  recorded explicitly, so an audit row never attributes an action to a person
  who cannot be identified.

SECURITY POSTURE: with anonymous access enabled, anybody who can reach this API
can acknowledge events, change safety thresholds, register cameras and edit
operator identities. Access must therefore be restricted at the network layer
or by a reverse proxy that terminates authentication in front of this service.
"""
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.database import get_db
    from app.models import AuditLog, User
except ImportError:  # pragma: no cover - import shim for direct script use
    from backend.app.config import settings
    from backend.app.database import get_db
    from backend.app.models import AuditLog, User


# Recorded on every audit row so a reader can tell which access model produced it.
ANONYMOUS_ACCESS_STATE = "NO_AUTHENTICATION_ANONYMOUS_ACCESS"
IDENTIFIED_ACCESS_STATE = "IDENTIFIED_ACCESS"


def resolve_actor() -> Optional[User]:
    """Return the acting identity for a request, or ``None``.

    There is no authentication in this build, so no request can prove who it is.
    The hook is kept as a single, obvious insertion point: a deployment that
    restores authentication implements it here and every endpoint inherits it,
    because every endpoint already depends on this function.
    """
    return None


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
        _enforce_access_mode(permission)
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
        _enforce_access_mode("/".join(allowed_roles) or "ROLE_REQUIRES_AUTHENTICATION")
        return actor

    dependency.declared_roles = allowed_roles  # type: ignore[attr-defined]
    return dependency


def _enforce_access_mode(requirement: str) -> None:
    if settings.ALLOW_ANONYMOUS_ACCESS:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            f"This deployment requires authenticated access, and this build has no "
            f"authentication. Unmet requirement: {requirement}."
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
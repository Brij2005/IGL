"""Operator account directory and audit log access."""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
import bcrypt
from sqlalchemy.orm import Session

try:
    from app.access_control import log_audit_event, require_permission, require_role
    from app.database import get_db
    from app.models import AuditLog, Role, User
    from app.schemas import (
        AuditLogOut,
        IdentityCreate,
        IdentityOut,
        IdentityRoleUpdate,
        IdentityStateUpdate,
    )
except ImportError:  # pragma: no cover
    from backend.app.access_control import log_audit_event, require_permission, require_role
    from backend.app.database import get_db
    from backend.app.models import AuditLog, Role, User
    from backend.app.schemas import (
        AuditLogOut,
        IdentityCreate,
        IdentityOut,
        IdentityRoleUpdate,
        IdentityStateUpdate,
    )


router = APIRouter()
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
DIRECTORY_READERS = ("users:view",)
DIRECTORY_WRITERS = ("ADMIN",)


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


@router.get("/users", response_model=List[IdentityOut])
def list_identities(
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission(*DIRECTORY_READERS)),
):
    """List operator identities. No credential material is returned."""
    return db.query(User).order_by(User.username).offset(offset).limit(limit).all()


@router.post("/users", response_model=IdentityOut, status_code=status.HTTP_201_CREATED)
def create_identity(
    identity_in: IdentityCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role(*DIRECTORY_WRITERS)),
):
    """Register an operator identity that events can be assigned to."""
    from app.config import settings
    if not settings.ALLOW_ANONYMOUS_ACCESS and not identity_in.password:
        raise HTTPException(status_code=422, detail="A password is required when authenticated access is enabled")
    if db.query(User.id).filter(User.username == identity_in.username).first():
        raise HTTPException(status_code=400, detail="Username already registered")
    if db.query(User.id).filter(User.email == identity_in.email).first():
        raise HTTPException(status_code=400, detail="Email address already registered")

    target_role = db.query(Role).filter(Role.name == identity_in.role_name).first()
    if target_role is None:
        raise HTTPException(status_code=404, detail=f"Role '{identity_in.role_name}' does not exist")

    identity = User(
        username=identity_in.username,
        email=identity_in.email,
        full_name=identity_in.full_name,
        employee_code=identity_in.employee_code,
        role_id=target_role.id,
        is_active=True,
        hashed_password=(bcrypt.hashpw(identity_in.password.get_secret_value().encode("utf-8"), bcrypt.gensalt()).decode("ascii") if identity_in.password else None),
        password_changed_at=datetime.now(timezone.utc) if identity_in.password else None,
    )
    db.add(identity)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username or email address already registered") from exc
    db.refresh(identity)

    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="IDENTITY_CREATED",
        resource_type="USER",
        resource_id=identity.id,
        details_json={"created_username": identity.username, "created_role": target_role.name},
        ip_address=_client_ip(request),
    )
    return identity


@router.put("/users/{user_id}/role", response_model=IdentityOut)
def update_identity_role(
    user_id: str,
    role_update: IdentityRoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role(*DIRECTORY_WRITERS)),
):
    """Change the role used to resolve an identity's responsibilities."""
    identity = db.query(User).filter(User.id == user_id).first()
    if identity is None:
        raise HTTPException(status_code=404, detail="Identity not found")
    target_role = db.query(Role).filter(Role.name == role_update.role_name).first()
    if target_role is None:
        raise HTTPException(status_code=404, detail=f"Role '{role_update.role_name}' does not exist")

    previous_role = identity.role.name if identity.role else None
    identity.role_id = target_role.id
    db.commit()
    db.refresh(identity)

    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="IDENTITY_ROLE_UPDATED",
        resource_type="USER",
        resource_id=identity.id,
        details_json={"target_username": identity.username, "old_role": previous_role, "new_role": target_role.name},
        ip_address=_client_ip(request),
    )
    return identity


@router.post("/users/{user_id}/deactivate", response_model=IdentityOut)
def set_identity_activation(
    user_id: str,
    payload: IdentityStateUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_role(*DIRECTORY_WRITERS)),
):
    """Deactivate or reactivate an identity.

    Deactivated identities are not assignable work, which is an operational state
    rather than an authentication one. The last active ADMIN identity is
    protected so the directory always retains an administrative owner.
    """
    identity = db.query(User).filter(User.id == user_id).first()
    if identity is None:
        raise HTTPException(status_code=404, detail="Identity not found")

    if not payload.is_active and identity.role is not None and identity.role.name == "ADMIN":
        remaining_admins = (
            db.query(User)
            .join(Role)
            .filter(Role.name == "ADMIN", User.is_active.is_(True), User.deactivated_at.is_(None))
            .count()
        )
        if remaining_admins <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot deactivate the last active ADMIN identity",
            )

    identity.is_active = payload.is_active
    identity.deactivated_at = None if payload.is_active else datetime.now(timezone.utc)
    if payload.is_active:
        identity.deactivated_by_user_id = None
    db.commit()
    db.refresh(identity)

    log_audit_event(
        db=db,
        user_id=actor.id if actor else None,
        action="IDENTITY_DEACTIVATED" if not payload.is_active else "IDENTITY_REACTIVATED",
        resource_type="USER",
        resource_id=identity.id,
        details_json={"target_username": identity.username, "reason": payload.reason},
        ip_address=_client_ip(request),
    )
    return identity


@router.get("/audit-logs", response_model=List[AuditLogOut])
def get_audit_logs(
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: Optional[User] = Depends(require_permission("AUDIT_LOGS_VIEW")),
):
    """Audit trail. Entries written without authentication carry a NULL actor."""
    return db.query(AuditLog).order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit).all()

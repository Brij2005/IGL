"""
Core authentication, password hashing, JWT token handling, security audit logging,
and Role-Based Access Control (RBAC) dependencies.
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable, List
import bcrypt
import jwt
from fastapi import Depends, HTTPException, status, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

try:
    from app.config import settings
    from app.database import get_db
    from app.models import User, Role, AuditLog
    from app.schemas import TokenData
except ImportError:
    from backend.app.config import settings
    from backend.app.database import get_db
    from backend.app.models import User, Role, AuditLog
    from backend.app.schemas import TokenData


security = HTTPBearer()


# ============================================================================
# PASSWORD HASHING
# ============================================================================

def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed_bytes = bcrypt.hashpw(password_bytes, salt)
    return hashed_bytes.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    plain_bytes = plain_password.encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")
    try:
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except Exception:
        return False


# ============================================================================
# JWT TOKEN MANAGEMENT
# ============================================================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a signed JWT access token."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp())
    })
    
    encoded_jwt = jwt.encode(
        to_encode,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        return payload
    except jwt.PyJWTError:
        return None


# ============================================================================
# AUDIT LOGGING HELPER
# ============================================================================

def log_audit_event(
    db: Session,
    user_id: Optional[str],
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    details_json: Optional[dict] = None,
    ip_address: Optional[str] = None
) -> AuditLog:
    """Record an immutable security audit event."""
    log_entry = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details_json=details_json,
        ip_address=ip_address,
        timestamp=datetime.now(timezone.utc)
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry


# ============================================================================
# AUTHENTICATION DEPENDENCIES
# ============================================================================

def get_current_user(
    auth: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(get_db)
) -> User:
    """FastAPI dependency to extract and validate the authenticated user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token = auth.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
    
    username: str = payload.get("sub")
    user_id: str = payload.get("user_id")
    if username is None or user_id is None:
        raise credentials_exception
        
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception
        
    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Ensure current user account is active."""
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account"
        )
    return current_user


# ============================================================================
# ROLE-BASED ACCESS CONTROL (RBAC) DEPENDENCIES
# ============================================================================

def require_role(*allowed_roles: str) -> Callable:
    """
    FastAPI dependency factory enforcing role authorization.
    Allowed roles e.g. ADMIN, SAFETY_OFFICER, PLANT_MANAGER, OPERATOR.
    """
    def role_checker(current_user: User = Depends(get_current_active_user)) -> User:
        user_role_name = current_user.role.name if current_user.role else "OPERATOR"
        if user_role_name not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User with role '{user_role_name}' does not have required permissions ({', '.join(allowed_roles)})"
            )
        return current_user
    return role_checker


def require_permission(permission: str) -> Callable:
    """
    FastAPI dependency factory enforcing specific granular permission authorization.
    """
    def permission_checker(current_user: User = Depends(get_current_active_user)) -> User:
        user_role = current_user.role
        if not user_role or not user_role.permissions_json:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' denied"
            )
        
        perms = user_role.permissions_json
        if isinstance(perms, list) and (permission in perms or "*" in perms):
            return current_user
        if isinstance(perms, dict) and perms.get(permission) is True:
            return current_user
            
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission '{permission}' denied"
        )
    return permission_checker

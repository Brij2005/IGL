"""
Authentication and Role-Based Access Control (RBAC) API Endpoints.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

try:
    from app.database import get_db
    from app.models import User, Role, AuditLog
    from app.schemas import (
        LoginRequest, Token, UserCreate, UserOut, UserRoleUpdate, AuditLogOut
    )
    from app.auth import (
        verify_password, hash_password, create_access_token,
        get_current_active_user, require_role, log_audit_event
    )
except ImportError:
    from backend.app.database import get_db
    from backend.app.models import User, Role, AuditLog
    from backend.app.schemas import (
        LoginRequest, Token, UserCreate, UserOut, UserRoleUpdate, AuditLogOut
    )
    from backend.app.auth import (
        verify_password, hash_password, create_access_token,
        get_current_active_user, require_role, log_audit_event
    )


router = APIRouter()


@router.post("/login", response_model=Token)
def login(
    login_req: LoginRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Authenticate user credentials, log security audit event, and return JWT access token.
    """
    client_ip = request.client.host if request.client else None
    user = db.query(User).filter(User.username == login_req.username).first()
    
    if not user or not verify_password(login_req.password, user.hashed_password):
        # Audit log failed attempt
        log_audit_event(
            db=db,
            user_id=user.id if user else None,
            action="LOGIN_FAILED",
            resource_type="USER",
            details_json={"username": login_req.username, "reason": "Invalid credentials"},
            ip_address=client_ip
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    if not user.is_active:
        log_audit_event(
            db=db,
            user_id=user.id,
            action="LOGIN_REJECTED",
            resource_type="USER",
            details_json={"username": user.username, "reason": "Inactive account"},
            ip_address=client_ip
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive"
        )
        
    role_name = user.role.name if user.role else "OPERATOR"
    
    # Audit log successful login
    log_audit_event(
        db=db,
        user_id=user.id,
        action="LOGIN_SUCCESS",
        resource_type="USER",
        resource_id=user.id,
        details_json={"username": user.username, "role": role_name},
        ip_address=client_ip
    )
    
    access_token = create_access_token(
        data={
            "sub": user.username,
            "user_id": user.id,
            "role": role_name
        }
    )
    
    return Token(
        access_token=access_token,
        token_type="bearer",
        user_id=user.id,
        username=user.username,
        role=role_name
    )


@router.get("/me", response_model=UserOut)
def get_current_user_profile(
    current_user: User = Depends(get_current_active_user)
):
    """
    Return currently authenticated user profile without sensitive credentials.
    """
    return current_user


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    user_in: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_role("ADMIN"))
):
    """
    Create new user account (Requires ADMIN role).
    """
    client_ip = request.client.host if request.client else None
    
    # Check username uniqueness
    existing_username = db.query(User).filter(User.username == user_in.username).first()
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
        
    # Check email uniqueness
    existing_email = db.query(User).filter(User.email == user_in.email).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email address already registered"
        )
        
    # Resolve target role
    target_role = db.query(Role).filter(Role.name == user_in.role_name).first()
    if not target_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{user_in.role_name}' does not exist"
        )
        
    hashed_pwd = hash_password(user_in.password)
    
    new_user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=hashed_pwd,
        full_name=user_in.full_name,
        employee_code=user_in.employee_code,
        role_id=target_role.id,
        is_active=True
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    log_audit_event(
        db=db,
        user_id=admin_user.id,
        action="USER_CREATED",
        resource_type="USER",
        resource_id=new_user.id,
        details_json={
            "created_username": new_user.username,
            "created_role": target_role.name
        },
        ip_address=client_ip
    )
    
    return new_user


@router.get("/users", response_model=List[UserOut])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("ADMIN", "PLANT_MANAGER"))
):
    """
    List all platform users (Requires ADMIN or PLANT_MANAGER role).
    """
    users = db.query(User).all()
    return users


@router.put("/users/{user_id}/role", response_model=UserOut)
def update_user_role(
    user_id: str,
    role_update: UserRoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_role("ADMIN"))
):
    """
    Update a user's assigned RBAC role (Requires ADMIN role).
    """
    client_ip = request.client.host if request.client else None
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
        
    target_role = db.query(Role).filter(Role.name == role_update.role_name).first()
    if not target_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Role '{role_update.role_name}' does not exist"
        )
        
    old_role_name = user.role.name if user.role else None
    user.role_id = target_role.id
    db.commit()
    db.refresh(user)
    
    log_audit_event(
        db=db,
        user_id=admin_user.id,
        action="USER_ROLE_UPDATED",
        resource_type="USER",
        resource_id=user.id,
        details_json={
            "target_username": user.username,
            "old_role": old_role_name,
            "new_role": target_role.name
        },
        ip_address=client_ip
    )
    
    return user


@router.get("/audit-logs", response_model=List[AuditLogOut])
def get_audit_logs(
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("ADMIN", "PLANT_MANAGER"))
):
    """
    Retrieve security and activity audit logs (Requires ADMIN or PLANT_MANAGER role).
    """
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit).all()
    return logs

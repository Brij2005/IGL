"""
Pydantic schemas for API request validation and response serialization.
Strictly excludes password hashes from all user response models.
"""
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator


# bcrypt only consumes the first 72 bytes and rejects longer input in current
# releases, so a longer password must be rejected rather than silently truncated.
BCRYPT_MAX_PASSWORD_BYTES = 72
MINIMUM_PASSWORD_LENGTH = 12


def validate_password_bytes(password: str) -> str:
    """Reject a password bcrypt cannot represent faithfully."""
    if len(password.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError("password must be at most 72 bytes when UTF-8 encoded")
    return password


# ============================================================================
# TOKEN & AUTH SCHEMAS
# ============================================================================

class Token(BaseModel):
    """JWT Token response schema."""
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    role: str


class TokenData(BaseModel):
    """Decoded JWT payload data."""
    username: Optional[str] = None
    user_id: Optional[str] = None
    role: Optional[str] = None


class LoginRequest(BaseModel):
    """Authentication login request payload."""
    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=4, max_length=128)

    @field_validator("password")
    @classmethod
    def check_password_size(cls, value: str) -> str:
        return validate_password_bytes(value)


# ============================================================================
# ROLE SCHEMAS
# ============================================================================

class RoleBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    description: Optional[str] = None
    permissions_json: Optional[Any] = None


class RoleCreate(RoleBase):
    pass


class RoleOut(RoleBase):
    id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# USER SCHEMAS
# ============================================================================

class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: str = Field(..., min_length=5, max_length=255)
    full_name: str = Field(..., min_length=2, max_length=150)
    employee_code: Optional[str] = Field(None, max_length=50)


class UserCreate(UserBase):
    password: str = Field(..., min_length=MINIMUM_PASSWORD_LENGTH, max_length=128)
    role_name: Optional[str] = "OPERATOR"

    @field_validator("password")
    @classmethod
    def check_password_size(cls, value: str) -> str:
        return validate_password_bytes(value)


class UserUpdate(BaseModel):
    email: Optional[str] = Field(None, min_length=5, max_length=255)
    full_name: Optional[str] = Field(None, min_length=2, max_length=150)
    password: Optional[str] = Field(None, min_length=MINIMUM_PASSWORD_LENGTH, max_length=128)
    employee_code: Optional[str] = Field(None, max_length=50)
    is_active: Optional[bool] = None

    @field_validator("password")
    @classmethod
    def check_password_size(cls, value: str | None) -> str | None:
        return validate_password_bytes(value) if value is not None else None


class UserRoleUpdate(BaseModel):
    role_name: str = Field(..., min_length=2, max_length=50)


class UserOut(UserBase):
    """
    Public User response model.
    STRICT SECURITY RULE: Never includes hashed_password!
    """
    id: str
    is_active: bool
    role: Optional[RoleOut] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# AUDIT LOG SCHEMAS
# ============================================================================

class AuditLogOut(BaseModel):
    id: str
    user_id: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    details_json: Optional[Any] = None
    ip_address: Optional[str] = None
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)

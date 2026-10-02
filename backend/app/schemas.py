"""Pydantic schemas for identity records and audit output.

There are no token, login or password schemas in this build: authentication was
removed. An identity record names an operator that work can be assigned to; it
cannot sign in, and no response contains credential material.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


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
# IDENTITY SCHEMAS
# ============================================================================

class IdentityBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: str = Field(..., min_length=5, max_length=255)
    full_name: str = Field(..., min_length=2, max_length=150)
    employee_code: Optional[str] = Field(None, max_length=50)


class IdentityCreate(IdentityBase):
    role_name: Optional[str] = "OPERATOR"


class IdentityRoleUpdate(BaseModel):
    role_name: str = Field(..., min_length=2, max_length=50)


class IdentityStateUpdate(BaseModel):
    """Activate or deactivate an identity. Requires an audit reason."""

    is_active: bool = True
    reason: str = Field(..., min_length=3, max_length=500)


class IdentityOut(IdentityBase):
    """Public identity response.

    STRICT RULE: never includes hashed_password, which is always NULL.
    """
    id: str
    is_active: bool
    role: Optional[RoleOut] = None
    created_at: datetime
    updated_at: datetime
    deactivated_at: Optional[datetime] = None

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
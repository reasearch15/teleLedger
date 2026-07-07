from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.auth.security import normalize_username
from app.models.user import UserRole


class LoginRequest(BaseModel):
    """Username and password submitted to create a local session."""

    username: str
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_login_username(cls, value: str) -> str:
        return normalize_username(value)


class CreateStaffRequest(BaseModel):
    """Admin-only request for a new staff account."""

    username: str
    password: SecretStr = Field(min_length=12, max_length=128)
    coadmin_id: int = Field(gt=0)

    @field_validator("username")
    @classmethod
    def normalize_staff_username(cls, value: str) -> str:
        return normalize_username(value)


class ResetPasswordRequest(BaseModel):
    """Admin-only password replacement request."""

    password: SecretStr = Field(min_length=12, max_length=128)


class CreateCoadminRequest(BaseModel):
    """Admin-only request for a new coadmin account."""

    username: str
    password: SecretStr = Field(min_length=12, max_length=128)
    is_active: bool = True

    @field_validator("username")
    @classmethod
    def normalize_coadmin_username(cls, value: str) -> str:
        return normalize_username(value)


class AssignStaffCoadminRequest(BaseModel):
    """Admin-only request to assign staff to a coadmin."""

    coadmin_id: int = Field(gt=0)


class UserResponse(BaseModel):
    """Safe account representation that never exposes password hashes."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: UserRole
    is_active: bool
    staff_color: str
    coadmin_id: int | None = None
    coadmin_username: str | None = None
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None

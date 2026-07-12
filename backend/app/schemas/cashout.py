from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.cashout import (
    CashoutAuditAction,
    CashoutStatus,
    CashoutTelegramStatus,
)


class CreateCashoutRequest(BaseModel):
    """Validated staff cashout submission."""

    player_tag: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    notes: str | None = Field(default=None, max_length=2000)
    idempotency_key: UUID

    @field_validator("player_tag")
    @classmethod
    def trim_player_tag(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Player Tag is required")
        return trimmed

    @field_validator("notes")
    @classmethod
    def trim_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class UpdateCashoutNotesRequest(BaseModel):
    """Editable notes for an unfinished cashout request."""

    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("notes")
    @classmethod
    def trim_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class CashoutStaffResponse(BaseModel):
    """Staff identity attached to a cashout."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    color: str


class CashoutResponse(BaseModel):
    """Public cashout workflow representation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    request_number: str
    player_tag: str
    amount: Decimal
    notes: str | None
    status: CashoutStatus
    telegram_status: CashoutTelegramStatus
    telegram_message_id: int | None
    telegram_chat_id: int | None = None
    telegram_attempts: int
    telegram_sent_at: datetime | None
    telegram_last_error: str | None
    created_by_staff_id: int
    completed_by_staff_id: int | None
    requested_by: CashoutStaffResponse | None = None
    completed_by: CashoutStaffResponse | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None


class CashoutListResponse(BaseModel):
    """Paginated cashout response."""

    items: list[CashoutResponse]
    limit: int
    offset: int
    has_more: bool


class CashoutAuditResponse(BaseModel):
    """One append-only cashout audit event."""

    id: int
    cashout_request_id: int
    action: CashoutAuditAction
    actor_user_id: int | None
    actor_username: str | None
    previous_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    created_at: datetime

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.staff_settlement import (
    StaffSettlementAuditAction,
    StaffSettlementStatus,
)


class LedgerItemResponse(BaseModel):
    staff_id: int
    staff_username: str
    staff_color: str
    coadmin_id: int | None
    coadmin_username: str
    payment_total: Decimal
    adjustment_total: Decimal
    total_in: Decimal
    total_out: Decimal
    settled_amount: Decimal
    net: Decimal
    payments_count: int
    cashouts_count: int
    settlements_count: int


class LedgerSummaryResponse(BaseModel):
    payment_total: Decimal
    adjustment_total: Decimal
    total_in: Decimal
    total_out: Decimal
    settled_amount: Decimal
    net: Decimal


class CoadminLedgerSummaryResponse(BaseModel):
    coadmin_id: int | None
    coadmin_username: str
    payment_total: Decimal
    adjustment_total: Decimal
    total_in: Decimal
    total_out: Decimal
    settled_amount: Decimal
    net: Decimal
    staff_count: int
    payments_count: int
    cashouts_count: int
    settlements_count: int


class LedgerResponse(BaseModel):
    items: list[LedgerItemResponse]
    coadmin_summaries: list[CoadminLedgerSummaryResponse] = Field(default_factory=list)
    summary: LedgerSummaryResponse
    calculation_type: str
    timezone: str
    period_start: datetime | None
    period_end: datetime | None
    includes_settled: bool
    rolling_hours: int | None = None
    generated_at: datetime | None = None


class LedgerPaymentDrilldownResponse(BaseModel):
    id: int
    staff_id: int
    staff_username: str
    amount: Decimal
    status: str
    completed_at: datetime | None
    settlement_id: int | None
    recipient_tag: str
    payment_sender_name: str


class LedgerCashoutDrilldownResponse(BaseModel):
    id: int
    staff_id: int
    staff_username: str
    amount: Decimal
    status: str
    created_at: datetime
    completed_at: datetime | None
    settlement_id: int | None
    player_tag: str
    request_number: str | None


class LedgerAdjustmentDrilldownResponse(BaseModel):
    id: int
    staff_id: int
    staff_username: str
    amount_delta: Decimal
    created_at: datetime
    settlement_id: int | None
    reason: str


class LedgerDrilldownResponse(BaseModel):
    payments: list[LedgerPaymentDrilldownResponse]
    cashouts: list[LedgerCashoutDrilldownResponse]
    adjustments: list[LedgerAdjustmentDrilldownResponse]
    calculation_type: str
    timezone: str
    period_start: datetime | None
    period_end: datetime | None
    includes_settled: bool
    rolling_hours: int | None = None
    generated_at: datetime | None = None


class CreateSettlementRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("notes")
    @classmethod
    def trim_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class CreateLedgerAdjustmentRequest(BaseModel):
    new_total_in: Decimal
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Reason is required.")
        return value


class LedgerAdjustmentResponse(BaseModel):
    id: int
    staff_id: int | None
    staff_username: str
    staff_color: str
    type: str
    amount_delta: Decimal
    previous_total_in: Decimal
    new_total_in: Decimal
    reason: str
    created_by_admin_id: int | None
    created_by_admin_username: str | None
    settlement_id: int | None
    created_at: datetime


class LedgerAdjustmentListResponse(BaseModel):
    items: list[LedgerAdjustmentResponse]
    rows: list[LedgerAdjustmentResponse] = Field(default_factory=list)
    limit: int
    offset: int
    has_more: bool
    hasMore: bool = False
    nextCursor: str | None = None


class SettlementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    staff_id: int | None
    staff_username: str
    staff_color: str
    coadmin_id: int | None
    coadmin_username: str | None
    scope: str
    amount: Decimal
    status: StaffSettlementStatus
    claimed_by_admin_id: int | None
    claimed_by_admin_username: str | None
    claimed_at: datetime | None
    completed_by_admin_id: int | None
    completed_by_admin_username: str | None
    completed_at: datetime | None
    created_by_admin_id: int
    created_by_admin_username: str
    created_at: datetime
    updated_at: datetime
    notes: str | None
    payment_ids: list[int]
    cashout_ids: list[int]
    adjustment_ids: list[int]


class SettlementListResponse(BaseModel):
    items: list[SettlementResponse]
    rows: list[SettlementResponse] = Field(default_factory=list)
    limit: int
    offset: int
    has_more: bool
    hasMore: bool = False
    nextCursor: str | None = None


class SettlementAuditResponse(BaseModel):
    id: int
    settlement_id: int
    actor_user_id: int
    action: StaffSettlementAuditAction
    previous_status: StaffSettlementStatus | None
    new_status: StaffSettlementStatus | None
    metadata: dict[str, Any] | None
    created_at: datetime

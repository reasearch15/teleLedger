from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.models.payment_audit import PaymentAuditAction
from app.models.payment_event import PaymentStatus

Money = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
OptionalTotal = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class ParsedPayment(BaseModel):
    """Validated output from a recognized payment notification."""

    model_config = ConfigDict(frozen=True)

    recipient_tag: str = Field(min_length=1, max_length=255)
    amount: Money
    payment_sender_name: str = Field(min_length=1, max_length=255)
    payment_datetime: datetime
    total_in: OptionalTotal
    total_out: OptionalTotal


class PaymentActionRequest(BaseModel):
    """Explicit actor identity used until authentication is introduced."""

    staff_id: int = Field(gt=0)


class PaymentEventResponse(BaseModel):
    """Public representation of a payment event."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_message_id: int
    recipient_tag: str
    amount: Decimal
    payment_sender_name: str
    payment_datetime: datetime | None
    total_in: Decimal | None
    total_out: Decimal | None
    raw_text: str
    status: PaymentStatus
    claimed_by_staff_id: int | None
    claimed_at: datetime | None
    completed_by_staff_id: int | None
    completed_at: datetime | None
    parser_confidence: int
    created_at: datetime
    updated_at: datetime


class StaffIdentityResponse(BaseModel):
    """Stable staff identity and color shown in workflow badges."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    color: str


class PaymentListItemResponse(BaseModel):
    """Lightweight payment representation used by collection endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    telegram_message_id: int
    recipient_tag: str
    amount: Decimal
    payment_sender_name: str
    payment_datetime: datetime | None
    total_in: Decimal | None
    total_out: Decimal | None
    status: PaymentStatus
    claimed_by_staff_id: int | None
    claimed_at: datetime | None
    completed_by_staff_id: int | None
    completed_at: datetime | None
    claimed_by_staff: StaffIdentityResponse | None = None
    completed_by_staff: StaffIdentityResponse | None = None
    parser_confidence: int
    created_at: datetime
    updated_at: datetime


class PaymentListResponse(BaseModel):
    """Paginated payment collection metadata and lightweight rows."""

    items: list[PaymentListItemResponse]
    total: int | None
    limit: int
    offset: int
    has_more: bool


class AssignPaymentRequest(BaseModel):
    """Administrator-selected active staff assignment."""

    staff_id: int = Field(gt=0)


class PaymentAuditResponse(BaseModel):
    """One immutable payment workflow history entry."""

    id: int
    payment_event_id: int
    actor_user_id: int | None
    actor_username: str | None
    subject_staff_id: int | None
    subject_username: str | None
    action: PaymentAuditAction
    from_status: PaymentStatus | None
    to_status: PaymentStatus
    created_at: datetime

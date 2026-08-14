from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VenmoConfirmationStatus(StrEnum):
    """Logical Venmo confirmation request state."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    NOT_RECEIVED = "not_received"
    CANCELLED = "cancelled"


class VenmoConfirmationAttemptStatus(StrEnum):
    """Telegram delivery attempt state for a Venmo confirmation request."""

    PENDING = "pending"
    POSTED = "posted"
    CONFIRMED = "confirmed"
    NOT_RECEIVED = "not_received"
    FAILED_TO_SEND = "failed_to_send"


class VenmoConfirmationInquiryStatus(StrEnum):
    """Atlas-side follow-up state for a not-received attempt."""

    OPEN = "open"
    DISMISSED = "dismissed"
    RESENT = "resent"


class VenmoConfirmationEventType(StrEnum):
    """Append-only Venmo confirmation workflow event types."""

    REQUEST_CREATED = "request_created"
    ATTEMPT_CREATED = "attempt_created"
    ATTEMPT_POSTED = "attempt_posted"
    CONFIRMED = "confirmed"
    NOT_RECEIVED = "not_received"
    INQUIRY_CREATED = "inquiry_created"
    INQUIRY_DISMISSED = "inquiry_dismissed"
    RESEND_REQUESTED = "resend_requested"
    RESEND_POSTED = "resend_posted"
    FAILURE = "failure"


class VenmoConfirmationRequest(Base):
    """One logical staff-submitted Venmo payment confirmation request."""

    __tablename__ = "venmo_confirmation_requests"
    __table_args__ = (
        Index("ix_venmo_confirmation_requests_coadmin_status", "coadmin_id", "status"),
        Index(
            "ix_venmo_confirmation_requests_staff_created",
            "requested_by_staff_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    coadmin_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    requested_by_staff_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    screenshot_media_asset_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("media_assets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[VenmoConfirmationStatus] = mapped_column(
        Enum(
            VenmoConfirmationStatus,
            name="venmo_confirmation_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=VenmoConfirmationStatus.PENDING,
        server_default=VenmoConfirmationStatus.PENDING.value,
    )
    payment_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    confirmed_by_telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_by_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class VenmoConfirmationAttempt(Base):
    """One Telegram send/resend attempt for a Venmo confirmation request."""

    __tablename__ = "venmo_confirmation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "request_id",
            "attempt_number",
            name="uq_venmo_confirmation_attempts_request_number",
        ),
        UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_venmo_confirmation_attempts_chat_message",
        ),
        Index("ix_venmo_confirmation_attempts_request_status", "request_id", "status"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    request_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("venmo_confirmation_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[VenmoConfirmationAttemptStatus] = mapped_column(
        Enum(
            VenmoConfirmationAttemptStatus,
            name="venmo_confirmation_attempt_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=VenmoConfirmationAttemptStatus.PENDING,
        server_default=VenmoConfirmationAttemptStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class VenmoConfirmationInquiry(Base):
    """Atlas inquiry created by a not-received Venmo confirmation attempt."""

    __tablename__ = "venmo_confirmation_inquiries"
    __table_args__ = (
        UniqueConstraint(
            "source_attempt_id",
            name="uq_venmo_confirmation_inquiries_source_attempt",
        ),
        Index("ix_venmo_confirmation_inquiries_request_status", "request_id", "status"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    request_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("venmo_confirmation_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_attempt_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("venmo_confirmation_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    resulting_attempt_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("venmo_confirmation_attempts.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[VenmoConfirmationInquiryStatus] = mapped_column(
        Enum(
            VenmoConfirmationInquiryStatus,
            name="venmo_confirmation_inquiry_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=VenmoConfirmationInquiryStatus.OPEN,
        server_default=VenmoConfirmationInquiryStatus.OPEN.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_by_staff_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resent_by_staff_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class VenmoConfirmationEvent(Base):
    """Append-only operational audit trail for Venmo confirmation workflows."""

    __tablename__ = "venmo_confirmation_events"
    __table_args__ = (
        Index("ix_venmo_confirmation_events_request_created", "request_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    request_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("venmo_confirmation_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("venmo_confirmation_attempts.id", ondelete="SET NULL"),
        nullable=True,
    )
    inquiry_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("venmo_confirmation_inquiries.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[VenmoConfirmationEventType] = mapped_column(
        Enum(
            VenmoConfirmationEventType,
            name="venmo_confirmation_event_type",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_identifier: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

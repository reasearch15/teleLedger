from __future__ import annotations

from datetime import datetime
from decimal import Decimal
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CashoutStatus(StrEnum):
    """Operational state of a cashout request."""

    PENDING = "pending"
    SENT = "sent"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED_TO_SEND = "failed_to_send"


class CashoutTelegramStatus(StrEnum):
    """Delivery state for the Telegram outbox."""

    PENDING = "pending"
    SENT = "sent"
    FAILED_TO_SEND = "failed_to_send"


class CashoutAuditAction(StrEnum):
    """Append-only cashout workflow actions."""

    CREATED = "created"
    TELEGRAM_SENT = "telegram_sent"
    TELEGRAM_RETRY = "telegram_retry"
    TELEGRAM_REACTION_COMPLETED = "telegram_reaction_completed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EDITED_NOTES = "edited_notes"


class CashoutRequest(Base):
    """Staff-created cashout request with durable Telegram delivery state."""

    __tablename__ = "cashout_requests"
    __table_args__ = (
        UniqueConstraint(
            "created_by_staff_id",
            "idempotency_key",
            name="uq_cashout_requests_staff_idempotency",
        ),
        Index("ix_cashout_requests_created", "created_at", "id"),
        Index(
            "ix_cashout_requests_delivery",
            "telegram_status",
            "telegram_next_attempt_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    request_number: Mapped[str | None] = mapped_column(
        String(24),
        nullable=True,
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    player_tag: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CashoutStatus] = mapped_column(
        Enum(
            CashoutStatus,
            name="cashout_status",
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=False,
        default=CashoutStatus.PENDING,
        server_default=CashoutStatus.PENDING.value,
    )
    telegram_status: Mapped[CashoutTelegramStatus] = mapped_column(
        Enum(
            CashoutTelegramStatus,
            name="cashout_telegram_status",
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=False,
        default=CashoutTelegramStatus.PENDING,
        server_default=CashoutTelegramStatus.PENDING.value,
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_random_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
    )
    telegram_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    telegram_next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=func.now(),
    )
    telegram_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    telegram_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_staff_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    completed_by_staff_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
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
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    settlement_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("staff_settlements.id", ondelete="SET NULL"),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class CashoutRequestAudit(Base):
    """Immutable audit record for one cashout transition."""

    __tablename__ = "cashout_request_audit"
    __table_args__ = (
        Index(
            "ix_cashout_request_audit_request_created",
            "cashout_request_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    cashout_request_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cashout_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[CashoutAuditAction] = mapped_column(
        Enum(
            CashoutAuditAction,
            name="cashout_audit_action",
            values_callable=lambda actions: [action.value for action in actions],
        ),
        nullable=False,
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    previous_value: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    new_value: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

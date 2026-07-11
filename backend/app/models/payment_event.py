from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.telegram_message import TelegramMessage


class PaymentStatus(StrEnum):
    """Supported payment operations workflow states."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class PaymentEvent(Base):
    """Structured payment data extracted from one raw Telegram message."""

    __tablename__ = "payment_events"
    __table_args__ = (
        CheckConstraint(
            "parser_confidence BETWEEN 0 AND 100",
            name="parser_confidence_range",
        ),
        UniqueConstraint(
            "telegram_message_id",
            name="uq_payment_events_telegram_message_id",
        ),
        Index("ix_payment_events_status", "status"),
        Index("ix_payment_events_payment_datetime", "payment_datetime"),
        Index("ix_payment_events_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    telegram_message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telegram_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient_tag: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    payment_sender_name: Mapped[str] = mapped_column(String(255), nullable=False)
    payment_datetime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False),
        nullable=True,
    )
    total_in: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_out: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_status",
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=False,
        default=PaymentStatus.PENDING,
        server_default=PaymentStatus.PENDING.value,
    )
    claimed_by_staff_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_staff_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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
    parser_confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    all_coadmins_declined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    declined_review_dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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

    telegram_message: Mapped[TelegramMessage] = relationship(back_populates="payment_events")

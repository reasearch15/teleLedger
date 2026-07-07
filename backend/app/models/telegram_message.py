from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.payment_event import PaymentEvent


class TelegramMessage(Base):
    """Raw Telegram message retained as the source of truth for parsing."""

    __tablename__ = "telegram_messages"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_telegram_messages_chat_message",
        ),
        Index(
            "ix_telegram_messages_received_at_message_id",
            "received_at",
            "telegram_message_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    payment_events: Mapped[list[PaymentEvent]] = relationship(
        back_populates="telegram_message",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CashoutPartialPendingInput(Base):
    """Short-lived Telegram partial-payment amount entry state."""

    __tablename__ = "cashout_partial_pending_inputs"
    __table_args__ = (
        UniqueConstraint("cashout_id", name="uq_cashout_partial_pending_cashout"),
        Index("ix_cashout_partial_pending_expires", "expires_at"),
        Index(
            "ix_cashout_partial_pending_chat_user",
            "telegram_chat_id",
            "telegram_user_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    cashout_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("cashout_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    coadmin_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prompt_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

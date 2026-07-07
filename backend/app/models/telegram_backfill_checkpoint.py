from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelegramBackfillCheckpoint(Base):
    """Durable high-water mark for Telegram startup backfill."""

    __tablename__ = "telegram_backfill_checkpoints"

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_scanned_message_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

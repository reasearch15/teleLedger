from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CoadminTelegramWorkflowSettings(Base):
    """Per-coadmin Telegram workflow group configuration."""

    __tablename__ = "coadmin_telegram_workflow_settings"
    __table_args__ = (
        UniqueConstraint("coadmin_id", name="uq_coadmin_telegram_workflow_settings_coadmin"),
        Index(
            "ix_coadmin_telegram_workflow_settings_cashout_group",
            "cashout_group_id",
        ),
        Index(
            "ix_coadmin_telegram_workflow_settings_venmo_group",
            "venmo_confirmation_group_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    coadmin_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    cashout_group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    venmo_confirmation_group_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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

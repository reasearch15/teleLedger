from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LedgerAdjustmentType(StrEnum):
    """Supported manual ledger adjustment types."""

    TOTAL_IN_ADJUSTMENT = "total_in_adjustment"


class LedgerAdjustment(Base):
    """Append-only admin adjustment to a staff operational ledger balance."""

    __tablename__ = "ledger_adjustments"
    __table_args__ = (
        Index("ix_ledger_adjustments_staff_id", "staff_id"),
        Index("ix_ledger_adjustments_settlement_id", "settlement_id"),
        Index("ix_ledger_adjustments_created_at", "created_at"),
        Index("ix_ledger_adjustments_created_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    staff_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    type: Mapped[LedgerAdjustmentType] = mapped_column(
        Enum(
            LedgerAdjustmentType,
            name="ledger_adjustment_type",
            values_callable=lambda types: [adjustment_type.value for adjustment_type in types],
        ),
        nullable=False,
    )
    amount_delta: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    previous_total_in: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    new_total_in: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_admin_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    settlement_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("staff_settlements.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

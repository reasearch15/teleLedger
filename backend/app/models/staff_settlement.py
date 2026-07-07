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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StaffSettlementStatus(StrEnum):
    """Workflow states for staff settlement withdrawals."""

    PENDING = "pending"
    CLAIMED = "claimed"
    DONE = "done"
    CANCELLED = "cancelled"


class StaffSettlementAuditAction(StrEnum):
    """Append-only settlement audit actions."""

    CREATED = "created"
    CLAIMED = "claimed"
    DONE = "done"
    CANCELLED = "cancelled"


class StaffSettlement(Base):
    """Admin-created withdrawal that reduces a staff ledger balance."""

    __tablename__ = "staff_settlements"
    __table_args__ = (
        Index("ix_staff_settlements_staff_completed", "staff_id", "completed_at"),
        Index("ix_staff_settlements_status_created", "status", "created_at"),
        Index("ix_staff_settlements_created_id", "created_at", "id"),
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
    coadmin_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    scope: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="staff",
        server_default="staff",
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[StaffSettlementStatus] = mapped_column(
        Enum(
            StaffSettlementStatus,
            name="staff_settlement_status",
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=False,
        default=StaffSettlementStatus.PENDING,
        server_default=StaffSettlementStatus.PENDING.value,
    )
    claimed_by_admin_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_by_admin_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_by_admin_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
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
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class StaffSettlementAuditLog(Base):
    """Immutable audit record for one settlement transition."""

    __tablename__ = "staff_settlement_audit_logs"
    __table_args__ = (
        Index(
            "ix_staff_settlement_audit_settlement_created",
            "settlement_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    settlement_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("staff_settlements.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[StaffSettlementAuditAction] = mapped_column(
        Enum(
            StaffSettlementAuditAction,
            name="staff_settlement_audit_action",
            values_callable=lambda actions: [action.value for action in actions],
        ),
        nullable=False,
    )
    previous_status: Mapped[StaffSettlementStatus | None] = mapped_column(
        Enum(
            StaffSettlementStatus,
            name="staff_settlement_status",
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=True,
    )
    new_status: Mapped[StaffSettlementStatus | None] = mapped_column(
        Enum(
            StaffSettlementStatus,
            name="staff_settlement_status",
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

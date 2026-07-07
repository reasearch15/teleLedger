from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.payment_event import PaymentStatus


class PaymentAuditAction(StrEnum):
    """Immutable workflow actions recorded for operational oversight."""

    CREATED = "created"
    CLAIMED = "claimed"
    UNCLAIMED = "unclaimed"
    DONE = "done"
    REOPENED = "reopened"
    REASSIGNED = "reassigned"


class PaymentAuditLog(Base):
    """Append-only history for one payment workflow."""

    __tablename__ = "payment_audit_logs"
    __table_args__ = (
        Index(
            "ix_payment_audit_logs_payment_created",
            "payment_event_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    payment_event_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("payment_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    subject_staff_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[PaymentAuditAction] = mapped_column(
        Enum(
            PaymentAuditAction,
            name="payment_audit_action",
            values_callable=lambda actions: [action.value for action in actions],
        ),
        nullable=False,
    )
    from_status: Mapped[PaymentStatus | None] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_status",
            values_callable=lambda statuses: [status.value for status in statuses],
            create_type=False,
        ),
        nullable=True,
    )
    to_status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_status",
            values_callable=lambda statuses: [status.value for status in statuses],
            create_type=False,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

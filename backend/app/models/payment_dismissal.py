from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaymentEventCoadminDismissal(Base):
    """Coadmin-scoped dismissal for pending payments marked Not Ours."""

    __tablename__ = "payment_event_coadmin_dismissals"
    __table_args__ = (
        UniqueConstraint(
            "payment_event_id",
            "coadmin_id",
            name="uq_payment_event_coadmin_dismissals_payment_coadmin",
        ),
        Index(
            "ix_payment_event_coadmin_dismissals_payment_event_id",
            "payment_event_id",
        ),
        Index("ix_payment_event_coadmin_dismissals_coadmin_id", "coadmin_id"),
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
    coadmin_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    dismissed_by_staff_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="not_ours",
        server_default="not_ours",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

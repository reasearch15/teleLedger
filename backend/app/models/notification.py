from __future__ import annotations

from datetime import datetime
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
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotificationType(StrEnum):
    """Persistent Atlas notification categories."""

    VENMO_CONFIRMATION_CONFIRMED = "venmo_confirmation_confirmed"


class PersistentNotification(Base):
    """Generic durable notification addressed to one Atlas user."""

    __tablename__ = "persistent_notifications"
    __table_args__ = (
        Index(
            "ix_persistent_notifications_recipient_unread",
            "recipient_user_id",
            "read_at",
            "created_at",
        ),
        Index("ix_persistent_notifications_related", "related_entity_type", "related_entity_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    recipient_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    coadmin_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    type: Mapped[NotificationType] = mapped_column(
        Enum(
            NotificationType,
            name="persistent_notification_type",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    related_entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    related_entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InquiryDirection(StrEnum):
    """Whether a message arrived from Telegram or was sent by TeleLedger."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class InquiryMessageSource(StrEnum):
    """TeleLedger classification for cashout-group chat messages."""

    TELEGRAM_EXTERNAL = "telegram_external"
    INQUIRY = "inquiry"
    CASHOUT_PANEL = "cashout_panel"


class InquiryMediaType(StrEnum):
    """Supported inquiry media categories."""

    NONE = "none"
    PHOTO = "photo"
    DOCUMENT = "document"


class InquiryMediaDownloadStatus(StrEnum):
    """Local media cache state for one inquiry message."""

    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class InquiryMessage(Base):
    """Cashout-group chat message retained for the Inquiry panel."""

    __tablename__ = "inquiry_messages"
    __table_args__ = (
        UniqueConstraint(
            "telegram_chat_id",
            "telegram_message_id",
            name="uq_inquiry_messages_chat_message",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_inquiry_messages_idempotency_key",
        ),
        Index(
            "ix_inquiry_messages_chat_date",
            "telegram_chat_id",
            "message_date",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sender_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    message_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    direction: Mapped[InquiryDirection] = mapped_column(
        Enum(
            InquiryDirection,
            name="inquiry_direction",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    message_source: Mapped[InquiryMessageSource] = mapped_column(
        Enum(
            InquiryMessageSource,
            name="inquiry_message_source",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
    )
    media_type: Mapped[InquiryMediaType] = mapped_column(
        Enum(
            InquiryMediaType,
            name="inquiry_media_type",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=InquiryMediaType.NONE,
        server_default=InquiryMediaType.NONE.value,
    )
    media_mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    media_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    media_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    media_download_status: Mapped[InquiryMediaDownloadStatus] = mapped_column(
        Enum(
            InquiryMediaDownloadStatus,
            name="inquiry_media_download_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=InquiryMediaDownloadStatus.NOT_APPLICABLE,
        server_default=InquiryMediaDownloadStatus.NOT_APPLICABLE.value,
    )
    sent_by_teleledger_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
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

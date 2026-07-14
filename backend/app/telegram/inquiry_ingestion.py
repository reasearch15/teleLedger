from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.cashout import CashoutRepository
from app.db.repositories.inquiry_message import InquiryMessageRepository
from app.db.session import SessionFactory
from app.models.cashout import CashoutRequest
from app.models.inquiry_message import (
    InquiryDirection,
    InquiryMediaDownloadStatus,
    InquiryMediaType,
    InquiryMessage,
    InquiryMessageSource,
)
from app.telegram.inquiry_media import (
    ALLOWED_IMAGE_MIME_TYPES,
    build_media_storage_key,
    media_path_for_key,
)
from app.telegram.inquiry_message_parser import (
    ParsedInquiryTelegramMessage,
    is_cashout_panel_message_text,
    parse_inquiry_telegram_message,
)
from app.websocket.events import LiveEventType, event_broker

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class InquiryIngestionResult:
    message_id: int | None
    inserted: bool
    message_source: str
    visible_in_inquiry: bool
    media_ready: bool


async def ingest_inquiry_telegram_message(
    message: Any,
    *,
    client: Any | None = None,
    forced_source: InquiryMessageSource | None = None,
    sent_by_teleledger_user_id: int | None = None,
    idempotency_key: str | None = None,
) -> InquiryIngestionResult:
    """Persist one cashout-group Telegram message for the Inquiry panel."""
    parsed = await parse_inquiry_telegram_message(message)
    settings = get_settings()
    source = forced_source or await resolve_message_source(
        telegram_chat_id=parsed.telegram_chat_id,
        telegram_message_id=parsed.telegram_message_id,
        text=parsed.text or parsed.caption,
        is_outbound=parsed.is_outbound,
        sent_by_teleledger_user_id=sent_by_teleledger_user_id,
    )
    row = _build_row(
        parsed,
        source=source,
        sent_by_teleledger_user_id=sent_by_teleledger_user_id,
        idempotency_key=idempotency_key,
    )

    async with SessionFactory() as session, session.begin():
        repository = InquiryMessageRepository(session)
        stored, inserted = await repository.upsert(
            row,
            preserve_source=True,
            preserve_outbound_metadata=True,
        )
        message_id = stored.id

    media_ready = stored.media_download_status == InquiryMediaDownloadStatus.READY
    if (
        client is not None
        and parsed.has_downloadable_media
        and stored.media_download_status
        in (
            InquiryMediaDownloadStatus.PENDING,
            InquiryMediaDownloadStatus.FAILED,
            InquiryMediaDownloadStatus.NOT_APPLICABLE,
        )
    ):
        media_ready = await download_inquiry_media(
            client,
            message,
            stored,
            settings=settings,
        )

    visible = stored.message_source != InquiryMessageSource.CASHOUT_PANEL
    if visible:
        await event_broker.publish(
            LiveEventType.INQUIRY_MESSAGE_CREATED if inserted else LiveEventType.INQUIRY_MESSAGE_UPDATED,
            inquiry_message_id=message_id,
            broadcast=True,
        )
        if media_ready and stored.media_type != InquiryMediaType.NONE:
            await event_broker.publish(
                LiveEventType.INQUIRY_MEDIA_READY,
                inquiry_message_id=message_id,
                broadcast=True,
            )

    return InquiryIngestionResult(
        message_id=message_id,
        inserted=inserted,
        message_source=stored.message_source.value,
        visible_in_inquiry=visible,
        media_ready=media_ready,
    )


async def register_cashout_panel_message(
    *,
    telegram_chat_id: int,
    telegram_message_id: int,
    text: str | None,
) -> None:
    """Mark one outbound cashout workflow message as hidden from Inquiry."""
    now = datetime.now(UTC)
    row = InquiryMessage(
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
        text=text,
        message_date=now,
        received_at=now,
        direction=InquiryDirection.OUTBOUND,
        message_source=InquiryMessageSource.CASHOUT_PANEL,
        media_type=InquiryMediaType.NONE,
        media_download_status=InquiryMediaDownloadStatus.NOT_APPLICABLE,
    )
    async with SessionFactory() as session, session.begin():
        repository = InquiryMessageRepository(session)
        await repository.upsert(
            row,
            preserve_source=True,
            preserve_outbound_metadata=True,
        )


async def resolve_message_source(
    *,
    telegram_chat_id: int,
    telegram_message_id: int,
    text: str | None,
    is_outbound: bool,
    sent_by_teleledger_user_id: int | None,
) -> InquiryMessageSource:
    """Determine or preserve the TeleLedger source for one Telegram message."""
    async with SessionFactory() as session:
        inquiry_repository = InquiryMessageRepository(session)
        existing = await inquiry_repository.get_by_telegram_identity(
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
        )
        if existing is not None:
            return existing.message_source

        cashout_repository = CashoutRepository(session)
        cashout = await cashout_repository.get_by_telegram_message_for_update(
            telegram_message_id=telegram_message_id,
            telegram_chat_id=telegram_chat_id,
            fallback_chat_id=telegram_chat_id,
        )
        if cashout is not None:
            return InquiryMessageSource.CASHOUT_PANEL

        if sent_by_teleledger_user_id is not None:
            return InquiryMessageSource.INQUIRY
        if is_cashout_panel_message_text(text):
            return InquiryMessageSource.CASHOUT_PANEL
        if is_outbound:
            return InquiryMessageSource.INQUIRY
        return InquiryMessageSource.TELEGRAM_EXTERNAL


async def download_inquiry_media(
    client: Any,
    telegram_message: Any,
    row: InquiryMessage,
    *,
    settings: Settings | None = None,
) -> bool:
    """Download one supported Telegram image into the inquiry media cache."""
    active_settings = settings or get_settings()
    if row.media_mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        return False

    storage_key = build_media_storage_key(
        telegram_chat_id=row.telegram_chat_id,
        telegram_message_id=row.telegram_message_id,
        mime_type=row.media_mime_type,
    )
    destination = media_path_for_key(active_settings, storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        downloaded = await client.download_media(telegram_message, file=str(destination))
        if downloaded is None and not destination.exists():
            raise RuntimeError("Telegram media download returned no file")
        size_bytes = destination.stat().st_size if destination.exists() else None
        if size_bytes is not None and size_bytes > active_settings.inquiry_media_max_bytes:
            destination.unlink(missing_ok=True)
            raise RuntimeError("Downloaded media exceeds configured size limit")
    except Exception:
        logger.exception(
            "inquiry_media_download_failed",
            extra={
                "inquiry_message_id": row.id,
                "telegram_message_id": row.telegram_message_id,
            },
        )
        async with SessionFactory() as session, session.begin():
            repository = InquiryMessageRepository(session)
            stored = await repository.get_by_telegram_identity(
                telegram_chat_id=row.telegram_chat_id,
                telegram_message_id=row.telegram_message_id,
                for_update=True,
            )
            if stored is not None:
                stored.media_download_status = InquiryMediaDownloadStatus.FAILED
        return False

    async with SessionFactory() as session, session.begin():
        repository = InquiryMessageRepository(session)
        stored = await repository.get_by_telegram_identity(
            telegram_chat_id=row.telegram_chat_id,
            telegram_message_id=row.telegram_message_id,
            for_update=True,
        )
        if stored is not None:
            stored.media_storage_key = storage_key
            stored.media_size_bytes = size_bytes
            stored.media_download_status = InquiryMediaDownloadStatus.READY

    await event_broker.publish(
        LiveEventType.INQUIRY_MEDIA_READY,
        inquiry_message_id=row.id,
        broadcast=True,
    )
    return True


def _build_row(
    parsed: ParsedInquiryTelegramMessage,
    *,
    source: InquiryMessageSource,
    sent_by_teleledger_user_id: int | None,
    idempotency_key: str | None,
) -> InquiryMessage:
    media_type = InquiryMediaType(parsed.media_type)
    media_status = (
        InquiryMediaDownloadStatus.PENDING
        if parsed.has_downloadable_media
        else InquiryMediaDownloadStatus.NOT_APPLICABLE
    )
    return InquiryMessage(
        telegram_chat_id=parsed.telegram_chat_id,
        telegram_message_id=parsed.telegram_message_id,
        telegram_sender_id=parsed.telegram_sender_id,
        sender_display_name=parsed.sender_display_name,
        sender_username=parsed.sender_username,
        text=parsed.text,
        caption=parsed.caption,
        message_date=parsed.message_date,
        received_at=datetime.now(UTC),
        edited_at=parsed.edited_at,
        direction=(
            InquiryDirection.OUTBOUND
            if parsed.is_outbound or source == InquiryMessageSource.INQUIRY
            else InquiryDirection.INBOUND
        ),
        message_source=source,
        media_type=media_type,
        media_mime_type=parsed.media_mime_type,
        media_filename=parsed.media_filename,
        media_download_status=media_status,
        sent_by_teleledger_user_id=sent_by_teleledger_user_id,
        idempotency_key=idempotency_key,
    )


async def cashout_message_exists(
    *,
    telegram_chat_id: int,
    telegram_message_id: int,
) -> bool:
    """Return True when a cashout request owns the Telegram message identity."""
    async with SessionFactory() as session:
        statement = select(CashoutRequest.id).where(
            CashoutRequest.telegram_chat_id == telegram_chat_id,
            CashoutRequest.telegram_message_id == telegram_message_id,
        )
        return (await session.execute(statement.limit(1))).scalar_one_or_none() is not None

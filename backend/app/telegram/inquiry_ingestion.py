from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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
    normalize_image_mime_type,
)
from app.telegram.inquiry_message_parser import (
    ParsedInquiryTelegramMessage,
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
    telegram_message = _telethon_message(message)
    parsed = await parse_inquiry_telegram_message(telegram_message)
    settings = get_settings()
    source = forced_source or await resolve_message_source(
        telegram_chat_id=parsed.telegram_chat_id,
        telegram_message_id=parsed.telegram_message_id,
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
        existing = await repository.get_by_telegram_identity(
            telegram_chat_id=parsed.telegram_chat_id,
            telegram_message_id=parsed.telegram_message_id,
            for_update=True,
        )
        if _media_needs_redownload(existing, parsed):
            row.media_download_status = InquiryMediaDownloadStatus.PENDING
            row.media_storage_key = None
            row.media_size_bytes = None
            row.media_hash = None
            row.media_error = None
        stored, inserted = await repository.upsert(
            row,
            preserve_source=True,
            preserve_outbound_metadata=True,
        )
        sender_alias = (
            await repository.ensure_sender_alias(stored.telegram_sender_id)
            if stored.telegram_sender_id is not None
            and stored.message_source == InquiryMessageSource.TELEGRAM_EXTERNAL
            else None
        )
        message_id = stored.id

    media_ready = stored.media_download_status == InquiryMediaDownloadStatus.READY
    visible = stored.message_source != InquiryMessageSource.CASHOUT_PANEL
    event_payload = _inquiry_event_payload(stored, sender_alias=sender_alias)
    media_download_attempted = False
    logger.info(
        "inquiry_message_committed",
        extra={
            **event_payload,
            "message_source": stored.message_source.value,
            "media_type": stored.media_type.value,
            "media_download_status": stored.media_download_status.value,
        },
    )
    if visible:
        event = (
            LiveEventType.INQUIRY_MESSAGE_CREATED
            if inserted
            else LiveEventType.INQUIRY_MESSAGE_UPDATED
        )
        await event_broker.publish(
            event,
            **event_payload,
            broadcast=True,
        )
        logger.info(
            "inquiry_event_published",
            extra={**event_payload, "sse_event": event.value},
        )

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
        media_download_attempted = True
        media_ready = await download_inquiry_media(
            client,
            telegram_message,
            stored,
            settings=settings,
        )

    if (
        visible
        and media_ready
        and not media_download_attempted
        and stored.media_type != InquiryMediaType.NONE
        and stored.media_download_status == InquiryMediaDownloadStatus.READY
    ):
        await event_broker.publish(
            LiveEventType.INQUIRY_MEDIA_READY,
            **event_payload,
            broadcast=True,
        )
        logger.info(
            "inquiry_event_published",
            extra={
                **event_payload,
                "sse_event": LiveEventType.INQUIRY_MEDIA_READY.value,
            },
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


async def mark_inquiry_message_deleted(
    *,
    telegram_chat_id: int,
    telegram_message_id: int,
) -> bool:
    """Mark one inquiry row deleted after Telegram removes the message."""
    async with SessionFactory() as session, session.begin():
        repository = InquiryMessageRepository(session)
        stored = await repository.get_by_telegram_identity(
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            for_update=True,
        )
        if stored is None:
            return False
        stored.is_deleted = True
        message_id = stored.id

    await event_broker.publish(
        LiveEventType.INQUIRY_MESSAGE_UPDATED,
        inquiry_message_id=message_id,
        broadcast=True,
    )
    logger.info(
        "inquiry_message_deleted",
        extra={
            "inquiry_message_id": message_id,
            "telegram_message_id": telegram_message_id,
            "telegram_chat_id": telegram_chat_id,
        },
    )
    return True


async def resolve_message_source(
    *,
    telegram_chat_id: int,
    telegram_message_id: int,
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
    log_extra = {
        "inquiry_message_id": row.id,
        "telegram_message_id": row.telegram_message_id,
        "telegram_chat_id": row.telegram_chat_id,
        "media_type": row.media_type.value,
        "media_mime_type": row.media_mime_type,
        "media_filename": row.media_filename,
    }
    logger.info("inquiry_media_download_started", extra=log_extra)
    try:
        mime_type = normalize_image_mime_type(
            row.media_mime_type,
            filename=row.media_filename,
        )
        if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
            raise RuntimeError(f"Unsupported media mime type: {row.media_mime_type}")

        storage_key = build_media_storage_key(
            telegram_chat_id=row.telegram_chat_id,
            telegram_message_id=row.telegram_message_id,
            mime_type=mime_type,
        )
        destination = media_path_for_key(active_settings, storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)

        downloaded = await client.download_media(telegram_message, file=str(destination))
        if downloaded is None and not destination.exists():
            raise RuntimeError("Telegram media download returned no file")
        size_bytes = destination.stat().st_size if destination.exists() else None
        if size_bytes is not None and size_bytes > active_settings.inquiry_media_max_bytes:
            destination.unlink(missing_ok=True)
            raise RuntimeError("Downloaded media exceeds configured size limit")
        media_hash = (
            hashlib.sha256(destination.read_bytes()).hexdigest()
            if destination.exists()
            else None
        )
    except Exception as error:
        logger.exception("inquiry_media_download_failed", extra=log_extra)
        await _mark_media_failed(
            telegram_chat_id=row.telegram_chat_id,
            telegram_message_id=row.telegram_message_id,
            error=str(error),
        )
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
            stored.media_mime_type = mime_type
            stored.media_download_status = InquiryMediaDownloadStatus.READY
            stored.media_hash = media_hash
            stored.media_error = None
            if stored.media_filename is None and row.media_filename is not None:
                stored.media_filename = row.media_filename

    await event_broker.publish(
        LiveEventType.INQUIRY_MEDIA_READY,
        inquiry_message_id=row.id,
        direction=row.direction.value,
        broadcast=True,
    )
    logger.info(
        "inquiry_media_download_ready",
        extra={
            **log_extra,
            "media_storage_key": storage_key,
            "media_download_status": InquiryMediaDownloadStatus.READY.value,
            "saved_file_path": str(destination),
            "media_size_bytes": size_bytes,
            "media_hash": media_hash,
            "matching_stage": "media_ready_no_ocr_pipeline",
        },
    )
    return True


def _telethon_message(message: Any) -> Any:
    """Return a Telethon Message when a live event wrapper was passed in."""
    inner_message = getattr(message, "message", None)
    if inner_message is not None and not isinstance(inner_message, str):
        return inner_message
    return message


def _inquiry_event_payload(
    message: InquiryMessage,
    *,
    sender_alias: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "inquiry_message_id": message.id,
        "direction": message.direction.value,
    }
    if sender_alias is not None:
        payload["sender_alias"] = sender_alias
    return payload


async def retry_pending_inquiry_media(
    client: Any,
    group: object,
    *,
    limit: int = 40,
) -> int:
    """Retry Telegram downloads for inquiry rows stuck in pending or failed."""
    async with SessionFactory() as session:
        repository = InquiryMessageRepository(session)
        pending_rows = await repository.list_pending_media(limit=limit)

    recovered = 0
    for row in pending_rows:
        try:
            message = await client.get_messages(group, ids=row.telegram_message_id)
            if message is None:
                logger.warning(
                    "inquiry_media_retry_message_missing",
                    extra={
                        "inquiry_message_id": row.id,
                        "telegram_message_id": row.telegram_message_id,
                        "telegram_chat_id": row.telegram_chat_id,
                    },
                )
                await _mark_media_failed(
                    telegram_chat_id=row.telegram_chat_id,
                    telegram_message_id=row.telegram_message_id,
                    error="Telegram message missing during media retry",
                )
                continue
            if await download_inquiry_media(client, message, row):
                recovered += 1
        except Exception:
            logger.exception(
                "inquiry_media_retry_failed",
                extra={
                    "inquiry_message_id": row.id,
                    "telegram_message_id": row.telegram_message_id,
                    "telegram_chat_id": row.telegram_chat_id,
                },
            )
            await _mark_media_failed(
                telegram_chat_id=row.telegram_chat_id,
                telegram_message_id=row.telegram_message_id,
                error="Media retry failed",
            )
    if pending_rows:
        logger.info(
            "inquiry_media_retry_finished",
            extra={
                "total": len(pending_rows),
                "recovered": recovered,
            },
        )
    return recovered


async def _mark_media_failed(
    *,
    telegram_chat_id: int,
    telegram_message_id: int,
    error: str,
) -> None:
    async with SessionFactory() as session, session.begin():
        repository = InquiryMessageRepository(session)
        stored = await repository.get_by_telegram_identity(
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            for_update=True,
        )
        if stored is not None:
            stored.media_download_status = InquiryMediaDownloadStatus.FAILED
            stored.media_error = error[:512]


def _media_needs_redownload(
    existing: InquiryMessage | None,
    parsed: ParsedInquiryTelegramMessage,
) -> bool:
    if existing is None or not parsed.has_downloadable_media:
        return False
    if existing.media_download_status != InquiryMediaDownloadStatus.READY:
        return False
    return (
        existing.media_type.value != parsed.media_type
        or existing.media_mime_type != parsed.media_mime_type
        or existing.media_filename != parsed.media_filename
        or parsed.edited_at is not None
    )


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
        telegram_grouped_id=parsed.telegram_grouped_id,
        reply_to_telegram_message_id=parsed.reply_to_telegram_message_id,
        forward_from_display_name=parsed.forward_from_display_name,
        is_deleted=False,
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

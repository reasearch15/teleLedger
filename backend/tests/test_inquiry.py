from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.db.repositories.inquiry_message import InquiryMessageRepository
from app.models.inquiry_message import (
    InquiryDirection,
    InquiryMediaDownloadStatus,
    InquiryMediaType,
    InquiryMessage,
    InquiryMessageSource,
)
from app.telegram import inquiry_ingestion
from app.telegram.inquiry_events import create_inquiry_message_handlers
from app.telegram.inquiry_media import (
    InvalidInquiryMediaStorageKeyError,
    build_media_storage_key,
    media_path_for_key,
)
from app.telegram.inquiry_message_parser import (
    InquiryMessageNotVisibleError,
    is_cashout_panel_message_text,
    parse_inquiry_telegram_message,
)

test_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionFactory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest_asyncio.fixture(autouse=True)
async def reset_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(inquiry_ingestion, "SessionFactory", TestSessionFactory)
    settings = get_settings()
    monkeypatch.setattr(settings, "inquiry_media_dir", str(tmp_path / "media"))
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


def _message(
    *,
    row_id: int,
    message_id: int,
    sender_id: int,
    source: InquiryMessageSource,
    message_date: datetime,
    direction: InquiryDirection = InquiryDirection.INBOUND,
) -> InquiryMessage:
    return InquiryMessage(
        id=row_id,
        telegram_chat_id=-1001,
        telegram_message_id=message_id,
        telegram_sender_id=sender_id,
        sender_display_name="Ayush",
        message_date=message_date,
        received_at=message_date,
        direction=direction,
        message_source=source,
        media_type=InquiryMediaType.NONE,
        media_download_status=InquiryMediaDownloadStatus.NOT_APPLICABLE,
    )


def _photo_row(
    *,
    row_id: int = 1,
    message_id: int = 8823,
    chat_id: int = -5467746352,
) -> InquiryMessage:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    return InquiryMessage(
        id=row_id,
        telegram_chat_id=chat_id,
        telegram_message_id=message_id,
        sender_display_name="Ayush",
        message_date=now,
        received_at=now,
        direction=InquiryDirection.INBOUND,
        message_source=InquiryMessageSource.TELEGRAM_EXTERNAL,
        media_type=InquiryMediaType.PHOTO,
        media_mime_type="image/jpeg",
        media_download_status=InquiryMediaDownloadStatus.PENDING,
    )


@pytest.mark.asyncio
async def test_upsert_preserves_cashout_panel_source() -> None:
    async with TestSessionFactory() as session, session.begin():
        repository = InquiryMessageRepository(session)
        original = InquiryMessage(
            telegram_chat_id=-1001,
            telegram_message_id=42,
            message_date=datetime(2026, 7, 14, tzinfo=UTC),
            received_at=datetime(2026, 7, 14, tzinfo=UTC),
            direction=InquiryDirection.OUTBOUND,
            message_source=InquiryMessageSource.CASHOUT_PANEL,
            media_type=InquiryMediaType.NONE,
            media_download_status=InquiryMediaDownloadStatus.NOT_APPLICABLE,
            text="🔴 CASHOUT REQUEST",
        )
        await repository.upsert(original)

    async with TestSessionFactory() as session, session.begin():
        repository = InquiryMessageRepository(session)
        echo = InquiryMessage(
            telegram_chat_id=-1001,
            telegram_message_id=42,
            message_date=datetime(2026, 7, 14, tzinfo=UTC),
            received_at=datetime(2026, 7, 14, tzinfo=UTC),
            direction=InquiryDirection.OUTBOUND,
            message_source=InquiryMessageSource.TELEGRAM_EXTERNAL,
            media_type=InquiryMediaType.NONE,
            media_download_status=InquiryMediaDownloadStatus.NOT_APPLICABLE,
            text="🔴 CASHOUT REQUEST",
        )
        stored, inserted = await repository.upsert(echo, preserve_source=True)
        assert inserted is False
        assert stored.message_source == InquiryMessageSource.CASHOUT_PANEL


@pytest.mark.asyncio
async def test_hidden_cashout_panel_does_not_break_sender_grouping() -> None:
    first = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
    middle = datetime(2026, 7, 14, 10, 1, tzinfo=UTC)
    last = datetime(2026, 7, 14, 10, 2, tzinfo=UTC)
    async with TestSessionFactory() as session, session.begin():
        repository = InquiryMessageRepository(session)
        session.add_all(
            [
                _message(
                    row_id=1,
                    message_id=1,
                    sender_id=99,
                    source=InquiryMessageSource.TELEGRAM_EXTERNAL,
                    message_date=first,
                ),
                InquiryMessage(
                    id=2,
                    telegram_chat_id=-1001,
                    telegram_message_id=2,
                    message_date=middle,
                    received_at=middle,
                    direction=InquiryDirection.OUTBOUND,
                    message_source=InquiryMessageSource.CASHOUT_PANEL,
                    media_type=InquiryMediaType.NONE,
                    media_download_status=InquiryMediaDownloadStatus.NOT_APPLICABLE,
                    text="🔴 CASHOUT REQUEST",
                ),
                _message(
                    row_id=3,
                    message_id=3,
                    sender_id=99,
                    source=InquiryMessageSource.TELEGRAM_EXTERNAL,
                    message_date=last,
                ),
            ]
        )
        await session.flush()
        previous = _message(
            row_id=1,
            message_id=1,
            sender_id=99,
            source=InquiryMessageSource.TELEGRAM_EXTERNAL,
            message_date=first,
        )
        current = _message(
            row_id=3,
            message_id=3,
            sender_id=99,
            source=InquiryMessageSource.TELEGRAM_EXTERNAL,
            message_date=last,
        )
        broken = await repository.has_visible_grouping_break(
            telegram_chat_id=-1001,
            previous=previous,
            current=current,
        )
        assert broken is False


@pytest.mark.asyncio
async def test_inquiry_message_between_same_sender_breaks_grouping() -> None:
    first = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
    middle = datetime(2026, 7, 14, 10, 1, tzinfo=UTC)
    last = datetime(2026, 7, 14, 10, 2, tzinfo=UTC)
    async with TestSessionFactory() as session, session.begin():
        repository = InquiryMessageRepository(session)
        session.add_all(
            [
                _message(
                    row_id=1,
                    message_id=1,
                    sender_id=99,
                    source=InquiryMessageSource.TELEGRAM_EXTERNAL,
                    message_date=first,
                ),
                _message(
                    row_id=2,
                    message_id=2,
                    sender_id=77,
                    source=InquiryMessageSource.INQUIRY,
                    message_date=middle,
                    direction=InquiryDirection.OUTBOUND,
                ),
                _message(
                    row_id=3,
                    message_id=3,
                    sender_id=99,
                    source=InquiryMessageSource.TELEGRAM_EXTERNAL,
                    message_date=last,
                ),
            ]
        )
        await session.flush()
        previous = _message(
            row_id=1,
            message_id=1,
            sender_id=99,
            source=InquiryMessageSource.TELEGRAM_EXTERNAL,
            message_date=first,
        )
        current = _message(
            row_id=3,
            message_id=3,
            sender_id=99,
            source=InquiryMessageSource.TELEGRAM_EXTERNAL,
            message_date=last,
        )
        broken = await repository.has_visible_grouping_break(
            telegram_chat_id=-1001,
            previous=previous,
            current=current,
        )
        assert broken is True


def test_cashout_panel_message_detection() -> None:
    assert is_cashout_panel_message_text("🔴 CASHOUT REQUEST\n\nTag:\nPlayer")
    assert not is_cashout_panel_message_text("Please help with this cashout")


def test_negative_chat_id_media_key_is_accepted(tmp_path: Path) -> None:
    key = build_media_storage_key(
        telegram_chat_id=-5467746352,
        telegram_message_id=8823,
        mime_type="image/jpeg",
    )
    assert key == "chat_-5467746352/8823.jpg"

    settings = get_settings()
    path = media_path_for_key(settings, key)
    assert path.name == "8823.jpg"
    assert path.parent.name == "chat_-5467746352"
    assert tmp_path / "media" in path.parents


def test_path_traversal_is_rejected() -> None:
    settings = get_settings()
    with pytest.raises(InvalidInquiryMediaStorageKeyError):
        media_path_for_key(settings, "../escape.jpg")
    with pytest.raises(InvalidInquiryMediaStorageKeyError):
        media_path_for_key(settings, "chat_1/../../8823.jpg")


def test_absolute_paths_are_rejected() -> None:
    settings = get_settings()
    with pytest.raises(InvalidInquiryMediaStorageKeyError):
        media_path_for_key(settings, "/etc/passwd")


@pytest.mark.asyncio
async def test_successful_photo_download_sets_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    row = _photo_row()
    async with TestSessionFactory() as session, session.begin():
        session.add(row)

    client = AsyncMock()
    client.download_media = AsyncMock(side_effect=lambda _message, file: Path(file).write_bytes(b"abc"))

    monkeypatch.setattr(
        "app.websocket.cross_process.notify_live_event",
        AsyncMock(return_value=None),
    )

    assert await inquiry_ingestion.download_inquiry_media(client, object(), row) is True

    async with TestSessionFactory() as session:
        stored = (
            await session.execute(select(InquiryMessage).where(InquiryMessage.id == 1))
        ).scalar_one()
    assert stored.media_download_status == InquiryMediaDownloadStatus.READY
    assert stored.media_storage_key == "chat_-5467746352/8823.jpg"
    assert stored.media_size_bytes == 3


@pytest.mark.asyncio
async def test_failed_photo_download_sets_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _photo_row()
    async with TestSessionFactory() as session, session.begin():
        session.add(row)

    client = AsyncMock()
    client.download_media = AsyncMock(side_effect=RuntimeError("network down"))

    assert await inquiry_ingestion.download_inquiry_media(client, object(), row) is False

    async with TestSessionFactory() as session:
        stored = (
            await session.execute(select(InquiryMessage).where(InquiryMessage.id == 1))
        ).scalar_one()
    assert stored.media_download_status == InquiryMediaDownloadStatus.FAILED
    assert stored.media_storage_key is None


@pytest.mark.asyncio
async def test_pending_media_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    row = _photo_row()
    async with TestSessionFactory() as session, session.begin():
        session.add(row)

    client = AsyncMock()
    client.get_messages = AsyncMock(return_value=object())
    client.download_media = AsyncMock(side_effect=lambda _message, file: Path(file).write_bytes(b"photo"))
    monkeypatch.setattr(
        "app.websocket.cross_process.notify_live_event",
        AsyncMock(return_value=None),
    )

    recovered = await inquiry_ingestion.retry_pending_inquiry_media(client, object(), limit=10)
    assert recovered == 1

    async with TestSessionFactory() as session:
        stored = (
            await session.execute(select(InquiryMessage).where(InquiryMessage.id == 1))
        ).scalar_one()
    assert stored.media_download_status == InquiryMediaDownloadStatus.READY
    assert stored.media_storage_key == "chat_-5467746352/8823.jpg"


@pytest.mark.asyncio
async def test_edit_without_visible_content_is_ignored() -> None:
    class EmptyEditEvent:
        id = 999
        chat_id = -5467746352

        async def get_sender(self) -> None:
            return None

    ingest = AsyncMock(
        side_effect=InquiryMessageNotVisibleError(
            "Telegram message has no inquiry-visible content"
        )
    )
    new_handler, edit_handler = create_inquiry_message_handlers(
        ingest_message=ingest,
        report=lambda _: None,
    )

    await edit_handler(EmptyEditEvent())
    ingest.assert_awaited_once()


@pytest.mark.asyncio
async def test_normal_edit_still_updates_existing_row() -> None:
    now = datetime(2026, 7, 14, tzinfo=UTC)
    async with TestSessionFactory() as session, session.begin():
        session.add(
            InquiryMessage(
                telegram_chat_id=-1001,
                telegram_message_id=77,
                sender_display_name="Ayush",
                text="old text",
                message_date=now,
                received_at=now,
                direction=InquiryDirection.INBOUND,
                message_source=InquiryMessageSource.TELEGRAM_EXTERNAL,
                media_type=InquiryMediaType.NONE,
                media_download_status=InquiryMediaDownloadStatus.NOT_APPLICABLE,
            )
        )

    class EditedEvent:
        id = 77
        chat_id = -1001
        sender_id = 42
        date = now
        edit_date = now
        message = "updated text"
        raw_text = "updated text"
        out = False
        media = None

        async def get_sender(self) -> object:
            return type(
                "Sender",
                (),
                {"first_name": "Ayush", "last_name": None, "username": "ayush"},
            )()

    monkeypatch_publish = AsyncMock(return_value=None)
    import app.telegram.inquiry_ingestion as ingestion_module

    original_publish = ingestion_module.event_broker.publish
    ingestion_module.event_broker.publish = monkeypatch_publish  # type: ignore[method-assign]
    try:
        await ingestion_module.ingest_inquiry_telegram_message(EditedEvent(), client=None)
    finally:
        ingestion_module.event_broker.publish = original_publish  # type: ignore[method-assign]

    async with TestSessionFactory() as session:
        stored = (
            await session.execute(
                select(InquiryMessage).where(InquiryMessage.telegram_message_id == 77)
            )
        ).scalar_one()
    assert stored.text == "updated text"
    assert stored.edited_at is not None
    assert stored.edited_at.replace(tzinfo=UTC) == now

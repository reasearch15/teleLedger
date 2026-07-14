from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.repositories.inquiry_message import InquiryMessageRepository
from app.models.inquiry_message import (
    InquiryDirection,
    InquiryMediaDownloadStatus,
    InquiryMediaType,
    InquiryMessage,
    InquiryMessageSource,
)
from app.telegram.inquiry_media import build_media_storage_key, media_path_for_key
from app.telegram.inquiry_message_parser import is_cashout_panel_message_text


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
async def reset_database() -> None:
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


def test_media_storage_key_is_deterministic_and_safe() -> None:
    key = build_media_storage_key(
        telegram_chat_id=-1001,
        telegram_message_id=55,
        mime_type="image/png",
    )
    assert key == "-1001/55.png"

    from app.core.config import get_settings

    settings = get_settings()
    path = media_path_for_key(settings, key)
    assert path.name == "55.png"
    with pytest.raises(ValueError):
        media_path_for_key(settings, "../escape.png")

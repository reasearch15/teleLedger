from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from telethon.tl import types  # type: ignore[import-untyped]

from app.core.config import Settings
from app.db.base import Base
from app.models.payment_event import PaymentEvent
from app.models.telegram_backfill_checkpoint import TelegramBackfillCheckpoint
from app.models.telegram_message import TelegramMessage
from app.schemas.telegram import IncomingTelegramMessage
from app.services.telegram_ingestion import (
    TelegramIngestionResult,
    TelegramIngestionService,
)
from app.telegram.backfill import (
    BackfillSummary,
    ManualBackfillOptions,
    backfill_messages,
    backfill_new_messages,
    parse_args,
)
from app.telegram.events import create_new_message_handler
from app.telegram.messages import TelegramMessageLike

PAYMENT_MESSAGE = """Hi Stephen_Mckinney_21,

You received $36.28 from Krista R.

03:08 PM - 29 Jun 2026
Total In: 5709.59$
Total Out: 1881.66$"""

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


class MockSender:
    first_name = "Krista"
    last_name = "R"


class MockHistoryMessage:
    def __init__(self, message_id: int, raw_text: str | None) -> None:
        self.id = message_id
        self.date = datetime(2026, 6, 29, 15, 8, tzinfo=UTC)
        self.chat_id: int | None = -1001234567890
        self.sender_id: int | None = 987654
        self.raw_text = raw_text

    async def get_sender(self) -> MockSender:
        return MockSender()


class MockBackfillClient:
    def __init__(self, messages: list[MockHistoryMessage]) -> None:
        self._messages = messages
        self.requested_limits: list[int | None] = []
        self.requested_min_ids: list[int] = []

    async def iter_messages(
        self,
        entity: object,
        *,
        limit: int | None,
        min_id: int = 0,
    ) -> AsyncIterator[TelegramMessageLike]:
        del entity
        self.requested_limits.append(limit)
        self.requested_min_ids.append(min_id)
        filtered = [message for message in self._messages if message.id > min_id]
        if limit is not None:
            filtered = filtered[:limit]
        for message in filtered:
            yield message


def make_group() -> types.Channel:
    return types.Channel(
        id=1234567890,
        title="Payment confirmation!",
        photo=types.ChatPhotoEmpty(),
        date=datetime(2026, 1, 1, tzinfo=UTC),
        megagroup=True,
        participants_count=4,
    )


@pytest_asyncio.fixture(autouse=True)
async def reset_database() -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


async def ingest_message(
    incoming: IncomingTelegramMessage,
) -> TelegramIngestionResult:
    async with TestSessionFactory() as session:
        return await TelegramIngestionService(session).ingest(incoming)


async def row_counts() -> tuple[int, int]:
    async with TestSessionFactory() as session:
        raw_count = await session.scalar(select(func.count(TelegramMessage.id)))
        payment_count = await session.scalar(select(func.count(PaymentEvent.id)))
    return int(raw_count or 0), int(payment_count or 0)


async def checkpoint_for(chat_id: int) -> TelegramBackfillCheckpoint | None:
    async with TestSessionFactory() as session:
        return await session.get(TelegramBackfillCheckpoint, chat_id)


@pytest.mark.asyncio
async def test_backfill_creates_a_missed_payment() -> None:
    client = MockBackfillClient([MockHistoryMessage(201, PAYMENT_MESSAGE)])
    reports: list[str] = []

    summary = await backfill_messages(
        client,
        make_group(),
        limit=500,
        report=reports.append,
        ingest_message=ingest_message,
    )

    assert summary == BackfillSummary(
        messages_scanned=1,
        raw_messages_inserted=1,
        payments_created=1,
        duplicates_skipped=0,
        ignored_messages=0,
        messages_fetched=1,
        highest_scanned_message_id=201,
    )
    assert await row_counts() == (1, 1)
    assert client.requested_limits == [500]
    assert reports[:3] == [
        "Backfill started",
        "Configured group: Payment confirmation! (-1001234567890)",
        "Limit: 500",
    ]
    assert reports[3:12] == [
        "------------------------------------------------",
        "Telegram message:",
        "Message ID: 201",
        "Existing raw message: no",
        "Existing payment_event: no",
        "Parser matched: yes",
        "Payment inserted: yes",
        "Reason skipped: none",
        "------------------------------------------------",
    ]
    assert reports[-8:] == [
        "Messages fetched: 1",
        "Messages scanned: 1",
        "Raw messages inserted: 1",
        "Payments created: 1",
        "Duplicates skipped: 0",
        "Ignored non-payment messages: 0",
        "Highest scanned message ID: 201",
        "Backfill completed",
    ]


@pytest.mark.asyncio
async def test_backfill_repairs_existing_raw_message_without_payment_event() -> None:
    historical = MockHistoryMessage(
        205,
        PAYMENT_MESSAGE.replace(
            "Hi Stephen_Mckinney_21,",
            "🟢 Hi Stephen_Mckinney_21,",
        )
        .replace("Total In:", "➕ Total In :")
        .replace("Total Out:", "➖ Total Out:"),
    )
    async with TestSessionFactory.begin() as session:
        session.add(
            TelegramMessage(
                telegram_chat_id=historical.chat_id,
                telegram_message_id=historical.id,
                sender_id=historical.sender_id,
                sender_name="Krista R",
                raw_text=historical.raw_text or "",
                received_at=historical.date,
            )
        )

    reports: list[str] = []
    summary = await backfill_messages(
        MockBackfillClient([historical]),
        make_group(),
        limit=500,
        report=reports.append,
        ingest_message=ingest_message,
    )

    assert await row_counts() == (1, 1)
    assert summary.raw_messages_inserted == 0
    assert summary.payments_created == 1
    assert summary.duplicates_skipped == 0
    assert "Existing raw message: yes" in reports
    assert "Existing payment_event: no" in reports
    assert "Payment inserted: yes" in reports


@pytest.mark.asyncio
async def test_existing_non_payment_is_rechecked_not_counted_as_duplicate() -> None:
    message = MockHistoryMessage(206, "Ordinary Telegram conversation")
    client = MockBackfillClient([message])

    await backfill_messages(
        client,
        make_group(),
        limit=500,
        report=lambda _: None,
        ingest_message=ingest_message,
    )
    summary = await backfill_messages(
        client,
        make_group(),
        limit=500,
        report=lambda _: None,
        ingest_message=ingest_message,
    )

    assert await row_counts() == (1, 0)
    assert summary.duplicates_skipped == 0
    assert summary.ignored_messages == 1


@pytest.mark.asyncio
async def test_repeated_backfill_creates_one_raw_message_and_payment() -> None:
    client = MockBackfillClient([MockHistoryMessage(202, PAYMENT_MESSAGE)])

    await backfill_messages(
        client,
        make_group(),
        limit=500,
        report=lambda _: None,
        ingest_message=ingest_message,
    )
    second_summary = await backfill_messages(
        client,
        make_group(),
        limit=500,
        report=lambda _: None,
        ingest_message=ingest_message,
    )

    assert await row_counts() == (1, 1)
    assert second_summary.raw_messages_inserted == 0
    assert second_summary.payments_created == 0
    assert second_summary.duplicates_skipped == 1


@pytest.mark.asyncio
async def test_live_listener_after_backfill_does_not_duplicate() -> None:
    message = MockHistoryMessage(203, PAYMENT_MESSAGE)
    client = MockBackfillClient([message])
    await backfill_messages(
        client,
        make_group(),
        limit=500,
        report=lambda _: None,
        ingest_message=ingest_message,
    )
    reports: list[str] = []
    handler = create_new_message_handler(ingest_message, reports.append)

    await handler(message)

    assert await row_counts() == (1, 1)
    assert reports[-1] == "Message 203: duplicate skipped"


@pytest.mark.asyncio
async def test_backfill_stores_non_payment_without_payment_event() -> None:
    client = MockBackfillClient(
        [MockHistoryMessage(204, "Ordinary Telegram conversation")]
    )

    summary = await backfill_messages(
        client,
        make_group(),
        limit=500,
        report=lambda _: None,
        ingest_message=ingest_message,
    )

    assert await row_counts() == (1, 0)
    assert summary.raw_messages_inserted == 1
    assert summary.ignored_messages == 1


@pytest.mark.asyncio
async def test_first_startup_scans_limit_and_writes_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.telegram.backfill.SessionFactory", TestSessionFactory)
    client = MockBackfillClient(
        [
            MockHistoryMessage(303, PAYMENT_MESSAGE),
            MockHistoryMessage(302, PAYMENT_MESSAGE),
            MockHistoryMessage(301, PAYMENT_MESSAGE),
        ]
    )
    reports: list[str] = []

    summary = await backfill_new_messages(
        client,
        make_group(),
        limit=2,
        report=reports.append,
        ingest_message=ingest_message,
    )

    checkpoint = await checkpoint_for(-1001234567890)
    assert checkpoint is not None
    assert checkpoint.last_scanned_message_id == 303
    assert summary.mode == "initial"
    assert summary.last_checkpoint is None
    assert summary.checkpoint_updated is True
    assert summary.backfill.messages_fetched == 2
    assert await row_counts() == (2, 2)
    assert client.requested_limits == [2]
    assert client.requested_min_ids == [0]
    assert "Backfill mode: initial" in reports
    assert "Last checkpoint: none" in reports
    assert "Messages fetched: 2" in reports
    assert "Highest scanned message ID: 303" in reports
    assert "Checkpoint updated: yes" in reports


@pytest.mark.asyncio
async def test_second_startup_scans_only_newer_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.telegram.backfill.SessionFactory", TestSessionFactory)

    await backfill_new_messages(
        MockBackfillClient([MockHistoryMessage(401, PAYMENT_MESSAGE)]),
        make_group(),
        limit=500,
        report=lambda _: None,
        ingest_message=ingest_message,
    )

    client = MockBackfillClient(
        [
            MockHistoryMessage(402, PAYMENT_MESSAGE),
            MockHistoryMessage(401, PAYMENT_MESSAGE),
        ]
    )
    summary = await backfill_new_messages(
        client,
        make_group(),
        limit=500,
        report=lambda _: None,
        ingest_message=ingest_message,
    )

    checkpoint = await checkpoint_for(-1001234567890)
    assert checkpoint is not None
    assert checkpoint.last_scanned_message_id == 402
    assert summary.mode == "incremental"
    assert summary.last_checkpoint == 401
    assert summary.backfill.messages_fetched == 1
    assert await row_counts() == (2, 2)
    assert client.requested_min_ids == [401]


@pytest.mark.asyncio
async def test_startup_checkpoint_does_not_advance_on_failed_ingestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.telegram.backfill.SessionFactory", TestSessionFactory)

    async def fail_on_second(
        incoming: IncomingTelegramMessage,
    ) -> TelegramIngestionResult:
        if incoming.telegram_message_id == 502:
            raise RuntimeError("database write failed")
        return await ingest_message(incoming)

    with pytest.raises(RuntimeError, match="database write failed"):
        await backfill_new_messages(
            MockBackfillClient(
                [
                    MockHistoryMessage(502, PAYMENT_MESSAGE),
                    MockHistoryMessage(501, PAYMENT_MESSAGE),
                ]
            ),
            make_group(),
            limit=500,
            report=lambda _: None,
            ingest_message=fail_on_second,
        )

    assert await checkpoint_for(-1001234567890) is None
    assert await row_counts() == (1, 1)


@pytest.mark.asyncio
async def test_startup_parser_miss_is_scanned_and_checkpointed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.telegram.backfill.SessionFactory", TestSessionFactory)

    summary = await backfill_new_messages(
        MockBackfillClient([MockHistoryMessage(601, "not a payment")]),
        make_group(),
        limit=500,
        report=lambda _: None,
        ingest_message=ingest_message,
    )

    checkpoint = await checkpoint_for(-1001234567890)
    assert checkpoint is not None
    assert checkpoint.last_scanned_message_id == 601
    assert summary.checkpoint_updated is True
    assert await row_counts() == (1, 0)


def test_manual_full_backfill_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["python -m app.telegram.backfill", "--full", "--since-message-id", "700"],
    )

    assert parse_args() == ManualBackfillOptions(
        limit=None,
        since_message_id=700,
    )


def test_telegram_backfill_limit_defaults_to_500(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("TELEGRAM_BACKFILL_LIMIT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert Settings().telegram_backfill_limit == 500

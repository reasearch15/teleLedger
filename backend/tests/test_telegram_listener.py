from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from pytest import LogCaptureFixture
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.payment_audit import PaymentAuditLog
from app.models.payment_event import PaymentEvent
from app.models.telegram_message import TelegramMessage
from app.schemas.telegram import IncomingTelegramMessage
from app.services.telegram_ingestion import (
    TelegramIngestionResult,
    TelegramIngestionService,
)
from app.telegram.events import create_new_message_handler

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


class MockTelethonEvent:
    def __init__(self, message_id: int, raw_text: str | None) -> None:
        self.id = message_id
        self.date = datetime(2026, 6, 29, 15, 8, tzinfo=UTC)
        self.chat_id: int | None = -100123456789
        self.sender_id: int | None = 987654
        self.raw_text = raw_text

    async def get_sender(self) -> MockSender:
        return MockSender()


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
        message_count = await session.scalar(select(func.count(TelegramMessage.id)))
        payment_count = await session.scalar(select(func.count(PaymentEvent.id)))
    return int(message_count or 0), int(payment_count or 0)


@pytest.mark.asyncio
async def test_payment_event_creates_message_and_payment_rows(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.telegram.events")
    reports: list[str] = []
    handler = create_new_message_handler(ingest_message, reports.append)

    await handler(MockTelethonEvent(101, PAYMENT_MESSAGE))

    assert await row_counts() == (1, 1)
    assert "telegram_message_received" in caplog.messages
    assert "telegram_payment_parsed" in caplog.messages
    assert reports[0].startswith("Message 101:")
    assert reports[-1] == (
        "Parsed payment: amount=$36.28 | sender=Krista R | "
        "recipient_tag=Stephen_Mckinney_21"
    )
    assert "Existing raw message: no" in reports
    assert "Payment inserted: yes" in reports
    async with TestSessionFactory() as session:
        raw_message = await session.scalar(select(TelegramMessage))
        payment = await session.scalar(select(PaymentEvent))
        assert raw_message is not None
        assert raw_message.telegram_chat_id == -100123456789
        assert raw_message.telegram_message_id == 101
        assert raw_message.sender_id == 987654
        assert raw_message.sender_name == "Krista R"
        assert raw_message.raw_text == PAYMENT_MESSAGE
        assert payment is not None
        assert payment.telegram_message_id == raw_message.id
        audit = await session.scalar(select(PaymentAuditLog))
        assert audit is not None
        assert audit.action.value == "created"


@pytest.mark.asyncio
async def test_non_payment_event_creates_message_only(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.telegram.events")
    reports: list[str] = []
    handler = create_new_message_handler(ingest_message, reports.append)

    await handler(MockTelethonEvent(102, "Normal group conversation"))

    assert await row_counts() == (1, 0)
    assert "telegram_message_ignored" in caplog.messages
    assert reports[-1] == "Message 102: ignored (not a payment)"


@pytest.mark.asyncio
async def test_duplicate_event_does_not_create_duplicate_rows(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.telegram.events")
    reports: list[str] = []
    handler = create_new_message_handler(ingest_message, reports.append)
    event = MockTelethonEvent(103, PAYMENT_MESSAGE)

    await handler(event)
    await handler(event)

    assert await row_counts() == (1, 1)
    assert "telegram_duplicate_skipped" in caplog.messages
    assert reports[-1] == "Message 103: duplicate skipped"


@pytest.mark.asyncio
async def test_non_text_event_is_not_stored(caplog: LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.telegram.events")
    reports: list[str] = []
    handler = create_new_message_handler(ingest_message, reports.append)

    await handler(MockTelethonEvent(104, None))

    assert await row_counts() == (0, 0)
    assert "telegram_message_ignored" in caplog.messages
    assert reports == ["Message 104: ignored (non-text message)"]

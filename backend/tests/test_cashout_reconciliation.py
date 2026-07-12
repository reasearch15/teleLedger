from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from telethon import errors  # type: ignore[import-untyped]
from telethon.tl import types  # type: ignore[import-untyped]

from app.db.base import Base
from app.models.cashout import (
    CashoutRequest,
    CashoutStatus,
    CashoutTelegramStatus,
)
from app.models.user import User, UserRole
from app.telegram import cashout_reactions, cashout_reconciliation
from app.telegram.cashout_reconciliation import reconcile_pending_cashout_reactions
from app.telegram.peer_ids import normalize_telegram_chat_id
from app.telegram.reaction_matching import parse_completion_reactions

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
) -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSessionFactory() as session:
        session.add(
            User(
                id=42,
                username="sarah",
                password_hash="not-used",
                role=UserRole.STAFF,
                is_active=True,
                staff_color="#2563EB",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        await session.commit()
    monkeypatch.setattr(cashout_reactions, "SessionFactory", TestSessionFactory)
    monkeypatch.setattr(cashout_reconciliation, "SessionFactory", TestSessionFactory)
    monkeypatch.setattr(
        "app.websocket.cross_process.notify_live_event",
        AsyncMock(return_value=None),
    )
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


async def seed_sent(cashout_id: int, message_id: int) -> None:
    timestamp = datetime(2026, 7, 6, 20, 35, tzinfo=UTC)
    async with TestSessionFactory() as session:
        session.add(
            CashoutRequest(
                id=cashout_id,
                request_number=f"CR-{cashout_id:06d}",
                idempotency_key=f"00000000-0000-0000-0000-{cashout_id:012d}",
                player_tag="ABC12345",
                amount=Decimal("250.00"),
                notes=None,
                status=CashoutStatus.SENT,
                telegram_status=CashoutTelegramStatus.SENT,
                telegram_message_id=message_id,
                telegram_chat_id=-1001234567890,
                telegram_random_id=10_000 + cashout_id,
                telegram_sent_at=timestamp,
                created_by_staff_id=42,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await session.commit()


def _reacted_message(emoji: str = "✅") -> SimpleNamespace:
    return SimpleNamespace(
        reactions=types.MessageReactions(
            results=[
                types.ReactionCount(
                    reaction=types.ReactionEmoji(emoji),
                    count=1,
                )
            ],
            recent_reactions=[],
            min=False,
            can_see_list=True,
        )
    )


@pytest.mark.asyncio
async def test_reconciliation_completes_missed_reaction() -> None:
    await seed_sent(1, 555)
    client = AsyncMock()
    client.get_messages = AsyncMock(return_value=_reacted_message("✅"))

    results = await reconcile_pending_cashout_reactions(
        client,
        group=object(),
        expected_chat_id=-1001234567890,
        allowed_reactions=frozenset({"✅", "👍"}),
    )

    assert len(results) == 1
    assert results[0].completed is True
    async with TestSessionFactory() as session:
        cashout = await session.get(CashoutRequest, 1)
        assert cashout is not None
        assert cashout.status == CashoutStatus.COMPLETED


@pytest.mark.asyncio
async def test_reconciliation_ignores_disallowed_emoji() -> None:
    await seed_sent(1, 555)
    client = AsyncMock()
    client.get_messages = AsyncMock(return_value=_reacted_message("🔥"))

    results = await reconcile_pending_cashout_reactions(
        client,
        group=object(),
        expected_chat_id=-1001234567890,
        allowed_reactions=frozenset({"✅", "👍"}),
    )

    assert results == []
    async with TestSessionFactory() as session:
        cashout = await session.get(CashoutRequest, 1)
        assert cashout is not None
        assert cashout.status == CashoutStatus.SENT


@pytest.mark.asyncio
async def test_reconciliation_only_batches_pending_cashouts() -> None:
    await seed_sent(1, 555)
    timestamp = datetime(2026, 7, 6, 20, 35, tzinfo=UTC)
    async with TestSessionFactory() as session:
        session.add(
            CashoutRequest(
                id=2,
                request_number="CR-000002",
                idempotency_key="00000000-0000-0000-0000-000000000002",
                player_tag="XYZ",
                amount=Decimal("10.00"),
                notes=None,
                status=CashoutStatus.COMPLETED,
                telegram_status=CashoutTelegramStatus.SENT,
                telegram_message_id=556,
                telegram_chat_id=-1001234567890,
                telegram_random_id=20_002,
                telegram_sent_at=timestamp,
                completed_at=timestamp,
                created_by_staff_id=42,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await session.commit()

    client = AsyncMock()
    client.get_messages = AsyncMock(return_value=_reacted_message())

    results = await reconcile_pending_cashout_reactions(
        client,
        group=object(),
        expected_chat_id=-1001234567890,
        allowed_reactions=frozenset({"✅"}),
        limit=40,
    )

    assert client.get_messages.await_count == 1
    assert results[0].cashout_id == 1


@pytest.mark.asyncio
async def test_reconciliation_flood_wait_propagates() -> None:
    await seed_sent(1, 555)
    client = AsyncMock()
    client.get_messages = AsyncMock(side_effect=errors.FloodWaitError(request=None, capture=5))

    with pytest.raises(errors.FloodWaitError):
        await reconcile_pending_cashout_reactions(
            client,
            group=object(),
            expected_chat_id=-1001234567890,
            allowed_reactions=frozenset({"✅"}),
        )


@pytest.mark.asyncio
async def test_reconciliation_fetch_failure_retries_safely() -> None:
    await seed_sent(1, 555)
    await seed_sent(2, 556)
    client = AsyncMock()
    client.get_messages = AsyncMock(
        side_effect=[RuntimeError("network"), _reacted_message("✅")]
    )

    results = await reconcile_pending_cashout_reactions(
        client,
        group=object(),
        expected_chat_id=-1001234567890,
        allowed_reactions=frozenset({"✅"}),
    )

    assert len(results) == 1
    assert results[0].completed is True
    assert client.get_messages.await_count == 2
    async with TestSessionFactory() as session:
        first = await session.get(CashoutRequest, 1)
        second = await session.get(CashoutRequest, 2)
        assert first is not None and first.status == CashoutStatus.COMPLETED
        # Newest-first batch order failed on id=2 first, then completed id=1.
        assert second is not None and second.status == CashoutStatus.SENT


def test_parse_completion_reactions_defaults_and_any() -> None:
    assert parse_completion_reactions(None) == frozenset({"✅", "👍"})
    assert parse_completion_reactions("*") is None
    assert parse_completion_reactions("any") is None
    assert parse_completion_reactions("✅, 🔥") == frozenset({"✅", "🔥"})


def test_normalize_telegram_chat_id() -> None:
    assert normalize_telegram_chat_id(1234567890) == -1001234567890
    assert normalize_telegram_chat_id(-1001234567890) == -1001234567890

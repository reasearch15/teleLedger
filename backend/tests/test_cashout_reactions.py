from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

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

from app.db.base import Base
from app.models.cashout import (
    CashoutAuditAction,
    CashoutRequest,
    CashoutRequestAudit,
    CashoutStatus,
    CashoutTelegramStatus,
)
from app.models.user import User, UserRole
from app.telegram import cashout_reactions
from app.telegram.cashout_reactions import complete_cashout_from_reaction
from app.telegram.events import create_reaction_handler

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


class FakeReactionEvent:
    def __init__(
        self,
        *,
        msg_id: int,
        peer: object,
        reactions: object | None,
        new_reactions: list[object] | None = None,
    ) -> None:
        self.msg_id = msg_id
        self.peer = peer
        self.reactions = reactions
        self.new_reactions = new_reactions


class FakeReactions:
    def __init__(self, results: list[object]) -> None:
        self.results = results


class FakeReactionCount:
    def __init__(self, count: int) -> None:
        self.count = count


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
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


async def seed_cashout(
    cashout_id: int,
    *,
    message_id: int,
    status: CashoutStatus = CashoutStatus.SENT,
) -> None:
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
                status=status,
                telegram_status=CashoutTelegramStatus.SENT,
                telegram_message_id=message_id,
                telegram_random_id=10_000 + cashout_id,
                telegram_sent_at=timestamp,
                created_by_staff_id=42,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        session.add(
            CashoutRequestAudit(
                cashout_request_id=cashout_id,
                action=CashoutAuditAction.TELEGRAM_SENT,
                actor_user_id=None,
                previous_value=None,
                new_value={"telegram_message_id": message_id},
            )
        )
        await session.commit()


async def audit_actions() -> list[CashoutAuditAction]:
    async with TestSessionFactory() as session:
        return list(
            await session.scalars(
                select(CashoutRequestAudit.action).order_by(CashoutRequestAudit.id)
            )
        )


@pytest.mark.asyncio
async def test_any_reaction_completes_cashout_and_writes_audit() -> None:
    await seed_cashout(1, message_id=555)

    result = await complete_cashout_from_reaction(
        555,
        -1001234567890,
        -1001234567890,
    )

    assert result.completed is True
    assert result.cashout_id == 1
    async with TestSessionFactory() as session:
        cashout = await session.get(CashoutRequest, 1)
        assert cashout is not None
        assert cashout.status == CashoutStatus.COMPLETED
        assert cashout.completed_at is not None
        assert cashout.telegram_status == CashoutTelegramStatus.SENT
    assert await audit_actions() == [
        CashoutAuditAction.TELEGRAM_SENT,
        CashoutAuditAction.TELEGRAM_REACTION_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_multiple_reactions_do_not_duplicate_completion() -> None:
    await seed_cashout(1, message_id=555)

    first = await complete_cashout_from_reaction(
        555,
        -1001234567890,
        -1001234567890,
    )
    second = await complete_cashout_from_reaction(
        555,
        -1001234567890,
        -1001234567890,
    )

    assert first.completed is True
    assert second.completed is False
    assert second.reason == "already_completed"
    assert await audit_actions() == [
        CashoutAuditAction.TELEGRAM_SENT,
        CashoutAuditAction.TELEGRAM_REACTION_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_reaction_removal_has_no_effect() -> None:
    calls: list[int] = []

    async def complete(
        message_id: int,
        chat_id: int,
        expected_chat_id: int,
    ) -> cashout_reactions.CashoutReactionCompletionResult:
        calls.append(message_id)
        assert chat_id == expected_chat_id
        return cashout_reactions.CashoutReactionCompletionResult(
            completed=True,
            cashout_id=1,
            reason="completed",
        )

    handler = create_reaction_handler(
        expected_chat_id=-1001234567890,
        complete_from_reaction=complete,
        report=lambda _: None,
    )
    await handler(
        FakeReactionEvent(
            msg_id=555,
            peer=types.PeerChannel(1234567890),
            reactions=FakeReactions(results=[]),
        )
    )

    assert calls == []


@pytest.mark.asyncio
async def test_active_reaction_update_invokes_completion() -> None:
    calls: list[int] = []

    async def complete(
        message_id: int,
        chat_id: int,
        expected_chat_id: int,
    ) -> cashout_reactions.CashoutReactionCompletionResult:
        calls.append(message_id)
        assert chat_id == expected_chat_id
        return cashout_reactions.CashoutReactionCompletionResult(
            completed=True,
            cashout_id=1,
            reason="completed",
        )

    reports: list[str] = []
    handler = create_reaction_handler(
        expected_chat_id=-1001234567890,
        complete_from_reaction=complete,
        report=reports.append,
    )
    await handler(
        FakeReactionEvent(
            msg_id=555,
            peer=types.PeerChannel(1234567890),
            reactions=FakeReactions(results=[FakeReactionCount(count=1)]),
        )
    )

    assert calls == [555]
    assert reports == ["Message 555: cashout completed by reaction"]


@pytest.mark.asyncio
async def test_new_reactions_update_invokes_completion() -> None:
    calls: list[int] = []

    async def complete(
        message_id: int,
        chat_id: int,
        expected_chat_id: int,
    ) -> cashout_reactions.CashoutReactionCompletionResult:
        calls.append(message_id)
        assert chat_id == expected_chat_id
        return cashout_reactions.CashoutReactionCompletionResult(
            completed=True,
            cashout_id=1,
            reason="completed",
            matched_cashout=True,
            previous_status="sent",
        )

    handler = create_reaction_handler(
        expected_chat_id=-1001234567890,
        complete_from_reaction=complete,
        report=lambda _: None,
    )
    await handler(
        FakeReactionEvent(
            msg_id=555,
            peer=types.PeerChannel(1234567890),
            reactions=None,
            new_reactions=[object()],
        )
    )

    assert calls == [555]


@pytest.mark.asyncio
async def test_bot_message_reactions_update_invokes_completion(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []

    async def complete(
        message_id: int,
        chat_id: int,
        expected_chat_id: int,
    ) -> cashout_reactions.CashoutReactionCompletionResult:
        calls.append(message_id)
        assert chat_id == expected_chat_id
        return cashout_reactions.CashoutReactionCompletionResult(
            completed=True,
            cashout_id=1,
            reason="completed",
            matched_cashout=True,
            previous_status="sent",
        )

    caplog.set_level(logging.INFO, logger="app.telegram.events")
    handler = create_reaction_handler(
        expected_chat_id=-1001234567890,
        complete_from_reaction=complete,
        report=lambda _: None,
    )
    await handler(
        types.UpdateBotMessageReactions(
            peer=types.PeerChannel(1234567890),
            msg_id=555,
            date=datetime(2026, 7, 7, tzinfo=UTC),
            reactions=[
                types.ReactionCount(
                    reaction=types.ReactionEmoji("👍"),
                    count=1,
                )
            ],
            qts=1,
        )
    )

    assert calls == [555]
    assert "telegram_reaction_raw_update_received" in caplog.messages
    assert "telegram_reaction_update_received" in caplog.messages
    assert "cashout_reaction_processed" in caplog.messages


@pytest.mark.asyncio
async def test_message_reactions_update_invokes_completion() -> None:
    calls: list[int] = []

    async def complete(
        message_id: int,
        chat_id: int,
        expected_chat_id: int,
    ) -> cashout_reactions.CashoutReactionCompletionResult:
        calls.append(message_id)
        assert chat_id == expected_chat_id
        return cashout_reactions.CashoutReactionCompletionResult(
            completed=True,
            cashout_id=1,
            reason="completed",
            matched_cashout=True,
            previous_status="sent",
        )

    handler = create_reaction_handler(
        expected_chat_id=-1001234567890,
        complete_from_reaction=complete,
        report=lambda _: None,
    )
    await handler(
        types.UpdateMessageReactions(
            peer=types.PeerChannel(1234567890),
            msg_id=555,
            reactions=types.MessageReactions(
                results=[
                    types.ReactionCount(
                        reaction=types.ReactionEmoji("✅"),
                        count=1,
                    )
                ],
                recent_reactions=[],
                min=False,
                can_see_list=True,
            ),
        )
    )

    assert calls == [555]


@pytest.mark.asyncio
async def test_unrelated_message_reactions_are_ignored() -> None:
    result = await complete_cashout_from_reaction(
        999,
        -1001234567890,
        -1001234567890,
    )

    assert result.completed is False
    assert result.cashout_id is None
    assert result.reason == "no_matching_cashout"
    assert await audit_actions() == []


@pytest.mark.asyncio
async def test_cancelled_cashouts_are_ignored() -> None:
    await seed_cashout(1, message_id=555, status=CashoutStatus.CANCELLED)

    result = await complete_cashout_from_reaction(
        555,
        -1001234567890,
        -1001234567890,
    )

    assert result.completed is False
    assert result.reason == "cancelled"
    async with TestSessionFactory() as session:
        cashout = await session.get(CashoutRequest, 1)
        assert cashout is not None
        assert cashout.status == CashoutStatus.CANCELLED
    assert await session_audit_count() == 1


@pytest.mark.asyncio
async def test_reaction_from_payment_group_does_not_complete_cashout() -> None:
    await seed_cashout(1, message_id=555)

    result = await complete_cashout_from_reaction(
        555,
        -1001111111111,
        -1001234567890,
    )

    assert result.completed is False
    assert result.reason == "different_chat"
    async with TestSessionFactory() as session:
        cashout = await session.get(CashoutRequest, 1)
        assert cashout is not None
        assert cashout.status == CashoutStatus.SENT
    assert await audit_actions() == [CashoutAuditAction.TELEGRAM_SENT]


async def session_audit_count() -> int:
    async with TestSessionFactory() as session:
        count = await session.scalar(select(func.count(CashoutRequestAudit.id)))
    return int(count or 0)

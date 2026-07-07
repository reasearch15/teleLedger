from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from telethon.errors import RandomIdDuplicateError  # type: ignore[import-untyped]

from app.db.base import Base
from app.models.cashout import (
    CashoutAuditAction,
    CashoutRequest,
    CashoutRequestAudit,
    CashoutStatus,
    CashoutTelegramStatus,
)
from app.models.user import User, UserRole
from app.telegram import cashout_delivery

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


class FakeTelegramClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[Any] = []

    async def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(id=555)


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
        session.add(
            CashoutRequest(
                id=1,
                request_number="CR-000001",
                idempotency_key="9b3e7304-f4df-44b6-b5d7-267defbe7813",
                player_tag="ABC12345",
                amount=Decimal("250.00"),
                notes="VIP Player",
                status=CashoutStatus.PENDING,
                telegram_status=CashoutTelegramStatus.PENDING,
                telegram_random_id=123456789,
                created_by_staff_id=42,
                created_at=datetime(2026, 7, 6, 20, 35, tzinfo=UTC),
                updated_at=datetime(2026, 7, 6, 20, 35, tzinfo=UTC),
            )
        )
        session.add(
            CashoutRequestAudit(
                cashout_request_id=1,
                action=CashoutAuditAction.CREATED,
                actor_user_id=42,
                previous_value=None,
                new_value={"status": "pending"},
            )
        )
        await session.commit()
    monkeypatch.setattr(cashout_delivery, "SessionFactory", TestSessionFactory)
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


@pytest.mark.asyncio
async def test_delivery_formats_message_and_marks_sent() -> None:
    client = FakeTelegramClient()

    processed = await cashout_delivery.deliver_next_cashout(client, "group")

    assert processed is True
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.random_id == 123456789
    assert "🔴 CASHOUT REQUEST" in request.message
    assert "ABC12345" in request.message
    assert "$250.00" in request.message
    assert "Requested By:\nsarah" in request.message
    assert "Request ID:\nCR-000001" in request.message
    assert "Optional Notes:\nVIP Player" in request.message

    async with TestSessionFactory() as session:
        cashout = await session.get(CashoutRequest, 1)
        assert cashout is not None
        assert cashout.status == CashoutStatus.SENT
        assert cashout.telegram_status == CashoutTelegramStatus.SENT
        assert cashout.telegram_message_id == 555
        assert cashout.telegram_attempts == 1
        actions = (
            await session.scalars(
                select(CashoutRequestAudit.action).order_by(
                    CashoutRequestAudit.id
                )
            )
        ).all()
        assert actions == [
            CashoutAuditAction.CREATED,
            CashoutAuditAction.TELEGRAM_SENT,
        ]


@pytest.mark.asyncio
async def test_crash_recovery_reuses_same_telegram_random_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeTelegramClient()
    original_record_success = cashout_delivery._record_success
    first = True

    async def crash_once(*args: Any, **kwargs: Any) -> None:
        nonlocal first
        if first:
            first = False
            raise ConnectionError("crashed after Telegram accepted the message")
        await original_record_success(*args, **kwargs)

    monkeypatch.setattr(cashout_delivery, "_record_success", crash_once)
    await cashout_delivery.deliver_next_cashout(client, "group")
    async with TestSessionFactory() as session:
        cashout = await session.get(CashoutRequest, 1)
        assert cashout is not None
        cashout.telegram_next_attempt_at = datetime.now(UTC)
        await session.commit()

    client.error = RandomIdDuplicateError(None)
    await cashout_delivery.deliver_next_cashout(client, "group")

    assert [request.random_id for request in client.requests] == [
        123456789,
        123456789,
    ]
    async with TestSessionFactory() as session:
        cashout = await session.get(CashoutRequest, 1)
        assert cashout is not None
        assert cashout.telegram_status == CashoutTelegramStatus.SENT
        assert cashout.telegram_attempts == 2
        actions = (
            await session.scalars(
                select(CashoutRequestAudit.action).order_by(
                    CashoutRequestAudit.id
                )
            )
        ).all()
        assert CashoutAuditAction.TELEGRAM_RETRY in actions

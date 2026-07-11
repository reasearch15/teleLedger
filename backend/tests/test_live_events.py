from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.cashout import CashoutRequest, CashoutStatus, CashoutTelegramStatus
from app.models.payment_event import PaymentEvent, PaymentStatus
from app.models.telegram_message import TelegramMessage
from app.models.user import User, UserRole
from app.services.cashout import CashoutService
from app.services.ledger import LedgerService
from app.services.payment import PaymentService
from app.websocket.events import LiveEventType, event_broker

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
async def reset_database() -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def seed_users() -> tuple[User, User]:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    staff_user = User(
        id=7,
        username="ledger_staff",
        password_hash="not-used",
        role=UserRole.STAFF,
        is_active=True,
        staff_color="#2563EB",
        created_at=timestamp,
        updated_at=timestamp,
    )
    admin_user = User(
        id=9,
        username="ledger_admin",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        staff_color="#7C3AED",
        created_at=timestamp,
        updated_at=timestamp,
    )
    async with TestSessionFactory() as session:
        session.add(staff_user)
        session.add(admin_user)
        await session.commit()
    return staff_user, admin_user


@pytest_asyncio.fixture
async def pending_payment(seed_users: tuple[User, User]) -> PaymentEvent:
    async with TestSessionFactory() as session:
        message = TelegramMessage(
            id=1,
            telegram_chat_id=100,
            telegram_message_id=200,
            sender_id=1,
            sender_name="sender",
            raw_text="payment",
            received_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.add(message)
        await session.flush()
        payment = PaymentEvent(
            telegram_message_id=message.id,
            recipient_tag="#player",
            amount=Decimal("25.00"),
            payment_sender_name="Alice",
            payment_datetime=datetime(2026, 1, 1, tzinfo=UTC),
            total_in=Decimal("25.00"),
            total_out=Decimal("0.00"),
            raw_text="payment",
            status=PaymentStatus.PENDING,
            parser_confidence=100,
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        return payment


@pytest_asyncio.fixture
async def pending_cashout(seed_users: tuple[User, User]) -> CashoutRequest:
    staff_user, _ = seed_users
    async with TestSessionFactory() as session:
        cashout = CashoutRequest(
            request_number="CR-000001",
            idempotency_key="key-1",
            player_tag="#player",
            amount=Decimal("10.00"),
            notes=None,
            status=CashoutStatus.PENDING,
            telegram_status=CashoutTelegramStatus.PENDING,
            telegram_random_id=123,
            created_by_staff_id=staff_user.id,
        )
        session.add(cashout)
        await session.commit()
        await session.refresh(cashout)
        return cashout


@pytest.mark.asyncio
async def test_payment_claim_publishes_event(
    seed_users: tuple[User, User],
    pending_payment: PaymentEvent,
) -> None:
    staff_user, _ = seed_users
    async with event_broker.subscribe() as queue, TestSessionFactory() as session:
        await PaymentService(session).claim(pending_payment.id, staff_user)
        payload = json.loads(await queue.get())
    assert payload == {
        "event": LiveEventType.PAYMENT_CLAIMED,
        "payment_id": pending_payment.id,
    }


@pytest.mark.asyncio
async def test_not_ours_publishes_all_coadmins_declined_event() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    async with TestSessionFactory() as session:
        coadmin = User(
            id=10,
            username="coadmin_one",
            password_hash="not-used",
            role=UserRole.COADMIN,
            is_active=True,
            staff_color="#111111",
            created_at=timestamp,
            updated_at=timestamp,
        )
        staff_user = User(
            id=42,
            username="staff_one",
            password_hash="not-used",
            role=UserRole.STAFF,
            is_active=True,
            staff_color="#2563EB",
            coadmin_id=10,
            created_at=timestamp,
            updated_at=timestamp,
        )
        message = TelegramMessage(
            id=1,
            telegram_chat_id=100,
            telegram_message_id=200,
            sender_id=1,
            sender_name="sender",
            raw_text="payment",
            received_at=timestamp,
        )
        session.add(coadmin)
        session.add(staff_user)
        session.add(message)
        await session.flush()
        payment = PaymentEvent(
            telegram_message_id=message.id,
            recipient_tag="#player",
            amount=Decimal("25.00"),
            payment_sender_name="Alice",
            payment_datetime=timestamp,
            total_in=Decimal("25.00"),
            total_out=Decimal("0.00"),
            raw_text="payment",
            status=PaymentStatus.PENDING,
            parser_confidence=100,
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)

    async with event_broker.subscribe() as queue, TestSessionFactory() as session:
        await PaymentService(session).dismiss_not_ours(payment.id, staff_user)
        dismissed = json.loads(await queue.get())
        all_declined = json.loads(await queue.get())

    assert dismissed == {
        "event": LiveEventType.PAYMENT_DISMISSED,
        "payment_id": payment.id,
        "coadmin_id": 10,
    }
    assert all_declined == {
        "event": LiveEventType.PAYMENT_ALL_COADMINS_DECLINED,
        "payment_id": payment.id,
    }


@pytest.mark.asyncio
async def test_cashout_complete_publishes_event(
    seed_users: tuple[User, User],
    pending_cashout: CashoutRequest,
) -> None:
    _, admin_user = seed_users
    async with event_broker.subscribe() as queue, TestSessionFactory() as session:
        await CashoutService(session).complete(pending_cashout.id, admin_user)
        completed = json.loads(await queue.get())
        ledger = json.loads(await queue.get())
    assert completed == {
        "event": LiveEventType.CASHOUT_COMPLETED,
        "cashout_id": pending_cashout.id,
    }
    assert ledger == {"event": LiveEventType.LEDGER_CHANGED}


@pytest.mark.asyncio
async def test_settlement_publishes_events(
    seed_users: tuple[User, User],
) -> None:
    staff_user, admin_user = seed_users
    async with TestSessionFactory() as session:
        message = TelegramMessage(
            id=2,
            telegram_chat_id=100,
            telegram_message_id=201,
            sender_id=1,
            sender_name="sender",
            raw_text="payment",
            received_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.add(message)
        await session.flush()
        payment = PaymentEvent(
            telegram_message_id=message.id,
            recipient_tag="#player",
            amount=Decimal("50.00"),
            payment_sender_name="Alice",
            payment_datetime=datetime(2026, 1, 1, tzinfo=UTC),
            total_in=Decimal("50.00"),
            total_out=Decimal("0.00"),
            raw_text="payment",
            status=PaymentStatus.DONE,
            parser_confidence=100,
            completed_by_staff_id=staff_user.id,
            completed_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        session.add(payment)
        await session.commit()

    async with event_broker.subscribe() as queue, TestSessionFactory() as session:
        settlement = await LedgerService(session).settle_staff(
            staff_id=staff_user.id,
            date_from=None,
            date_to=None,
            notes=None,
            actor=admin_user,
        )
        created = json.loads(await queue.get())
        done = json.loads(await queue.get())
        ledger = json.loads(await queue.get())

    assert created == {
        "event": LiveEventType.SETTLEMENT_CREATED,
        "settlement_id": settlement.id,
    }
    assert done == {
        "event": LiveEventType.SETTLEMENT_DONE,
        "settlement_id": settlement.id,
    }
    assert ledger == {"event": LiveEventType.LEDGER_CHANGED}

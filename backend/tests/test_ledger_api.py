from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.cashout import CashoutRequest, CashoutStatus, CashoutTelegramStatus
from app.models.ledger_adjustment import LedgerAdjustment, LedgerAdjustmentType
from app.models.payment_event import PaymentEvent, PaymentStatus
from app.models.staff_settlement import (
    StaffSettlement,
    StaffSettlementAuditLog,
    StaffSettlementStatus,
)
from app.models.telegram_message import TelegramMessage
from app.models.user import User, UserRole
from app.services.ledger import LedgerService

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


def make_user(user_id: int, username: str, role: UserRole) -> User:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    return User(
        id=user_id,
        username=username,
        password_hash="not-used",
        role=role,
        is_active=True,
        staff_color="#2563EB" if role == UserRole.STAFF else "#111827",
        created_at=timestamp,
        updated_at=timestamp,
    )


ADMIN = make_user(1, "admin", UserRole.ADMIN)
STAFF = make_user(42, "Sarah", UserRole.STAFF)
DEFAULT_COADMIN = make_user(10, "default_coadmin", UserRole.COADMIN)


@pytest_asyncio.fixture(autouse=True)
async def reset_database() -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSessionFactory() as session:
        session.add_all(
            [
                make_user(1, "admin", UserRole.ADMIN),
                make_user(10, "default_coadmin", UserRole.COADMIN),
                make_user(42, "Sarah", UserRole.STAFF),
            ]
        )
        staff = await session.get(User, 42)
        assert staff is not None
        staff.coadmin_id = 10
        await session.commit()
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


@asynccontextmanager
async def api_client_for(user: User) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    async def override_current_user() -> User:
        return user

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_current_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def seed_done_payment(
    payment_id: int,
    *,
    staff_id: int | None = 42,
    amount: str = "100.00",
    completed_at: datetime = datetime(2026, 7, 1, 12, tzinfo=UTC),
    created_at: datetime | None = None,
    settlement_id: int | None = None,
    status: PaymentStatus = PaymentStatus.DONE,
) -> None:
    async with TestSessionFactory() as session:
        session.add(
            TelegramMessage(
                id=payment_id,
                telegram_chat_id=100,
                telegram_message_id=payment_id,
                sender_id=None,
                sender_name=None,
                raw_text="payment",
                received_at=completed_at,
            )
        )
        session.add(
            PaymentEvent(
                id=payment_id,
                telegram_message_id=payment_id,
                recipient_tag="ABC",
                amount=Decimal(amount),
                payment_sender_name="Customer",
                payment_datetime=None,
                total_in=None,
                total_out=None,
                raw_text="payment",
                status=status,
                completed_by_staff_id=staff_id,
                completed_at=completed_at if status == PaymentStatus.DONE else None,
                settlement_id=settlement_id,
                parser_confidence=100,
                created_at=created_at or completed_at,
            )
        )
        await session.commit()


async def seed_cashout(
    cashout_id: int,
    *,
    amount: str = "30.00",
    status: CashoutStatus = CashoutStatus.COMPLETED,
    completed_at: datetime = datetime(2026, 7, 2, 12, tzinfo=UTC),
    staff_id: int | None = 42,
    created_at: datetime | None = None,
    settlement_id: int | None = None,
) -> None:
    async with TestSessionFactory() as session:
        session.add(
            CashoutRequest(
                id=cashout_id,
                request_number=f"CR-{cashout_id:06d}",
                idempotency_key=f"00000000-0000-0000-0000-{cashout_id:012d}",
                player_tag="ABC",
                amount=Decimal(amount),
                notes=None,
                status=status,
                telegram_status=CashoutTelegramStatus.SENT,
                telegram_random_id=10_000 + cashout_id,
                created_by_staff_id=staff_id,
                created_at=created_at or completed_at,
                completed_at=completed_at if status == CashoutStatus.COMPLETED else None,
                settlement_id=settlement_id,
            )
        )
        await session.commit()


async def seed_adjustment(
    adjustment_id: int,
    *,
    staff_id: int | None = 42,
    amount_delta: str = "10.00",
    created_at: datetime = datetime(2026, 7, 1, 12, tzinfo=UTC),
    settlement_id: int | None = None,
) -> None:
    delta = Decimal(amount_delta)
    async with TestSessionFactory() as session:
        session.add(
            LedgerAdjustment(
                id=adjustment_id,
                staff_id=staff_id,
                type=LedgerAdjustmentType.TOTAL_IN_ADJUSTMENT,
                amount_delta=delta,
                previous_total_in=Decimal("0.00"),
                new_total_in=delta,
                reason="Seeded adjustment",
                created_by_admin_id=1,
                created_at=created_at,
                settlement_id=settlement_id,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_admin_can_view_ledger_and_staff_cannot() -> None:
    await seed_done_payment(1, amount="1000.00")
    await seed_cashout(1, amount="300.00")

    async with api_client_for(ADMIN) as admin_client:
        ledger = await admin_client.get("/api/admin/ledger")
    async with api_client_for(STAFF) as staff_client:
        forbidden = await staff_client.get("/api/admin/ledger")

    assert ledger.status_code == 200
    item = ledger.json()["items"][0]
    assert item["staff_id"] == 42
    assert item["coadmin_id"] == 10
    assert item["coadmin_username"] == "default_coadmin"
    assert item["total_in"] == "1000.00"
    assert item["total_out"] == "300.00"
    assert item["settled_amount"] == "0.00"
    assert item["net"] == "700.00"
    assert item["payments_count"] == 1
    assert item["cashouts_count"] == 1
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_coadmin_totals_equal_sum_of_staff_totals_and_global_totals() -> None:
    async with TestSessionFactory() as session:
        session.add(make_user(11, "coadmin_two", UserRole.COADMIN))
        session.add(make_user(84, "Alex", UserRole.STAFF))
        session.add(make_user(85, "Blair", UserRole.STAFF))
        await session.flush()
        alex = await session.get(User, 84)
        blair = await session.get(User, 85)
        assert alex is not None
        assert blair is not None
        alex.coadmin_id = 10
        blair.coadmin_id = 11
        await session.commit()

    await seed_done_payment(1, staff_id=42, amount="100.00")
    await seed_done_payment(2, staff_id=84, amount="25.00")
    await seed_cashout(1, staff_id=42, amount="30.00")
    await seed_done_payment(3, staff_id=85, amount="40.00")
    await seed_cashout(2, staff_id=85, amount="10.00")

    async with api_client_for(ADMIN) as client:
        ledger = await client.get("/api/admin/ledger")

    body = ledger.json()
    default = next(
        item for item in body["coadmin_summaries"] if item["coadmin_id"] == 10
    )
    second = next(item for item in body["coadmin_summaries"] if item["coadmin_id"] == 11)
    default_staff = [
        item for item in body["items"] if item["coadmin_id"] == default["coadmin_id"]
    ]

    assert default["coadmin_username"] == "default_coadmin"
    assert default["total_in"] == "125.00"
    assert default["total_out"] == "30.00"
    assert default["net"] == "95.00"
    assert default["staff_count"] == 2
    assert default["payments_count"] == 2
    assert default["cashouts_count"] == 1
    assert sum(Decimal(item["total_in"]) for item in default_staff) == Decimal(
        default["total_in"]
    )
    assert second["total_in"] == "40.00"
    assert sum(
        Decimal(item["total_in"]) for item in body["coadmin_summaries"]
    ) == Decimal(body["summary"]["total_in"])
    assert sum(
        Decimal(item["total_out"]) for item in body["coadmin_summaries"]
    ) == Decimal(body["summary"]["total_out"])
    assert sum(Decimal(item["net"]) for item in body["coadmin_summaries"]) == Decimal(
        body["summary"]["net"]
    )


@pytest.mark.asyncio
async def test_admin_can_settle_coadmin_balance_and_history_shows_scope() -> None:
    async with TestSessionFactory() as session:
        session.add(make_user(84, "Alex", UserRole.STAFF))
        await session.flush()
        alex = await session.get(User, 84)
        assert alex is not None
        alex.coadmin_id = 10
        await session.commit()

    await seed_done_payment(1, staff_id=42, amount="100.00")
    await seed_cashout(1, staff_id=42, amount="30.00")
    await seed_done_payment(2, staff_id=84, amount="50.00")

    async with api_client_for(ADMIN) as client:
        created = await client.post("/api/admin/ledger/coadmins/10/settlements", json={})
        repeated = await client.post("/api/admin/ledger/coadmins/10/settlements", json={})
        ledger = await client.get("/api/admin/ledger")
        history = await client.get("/api/admin/settlements")

    assert created.status_code == 201
    assert created.json()["scope"] == "coadmin"
    assert created.json()["staff_id"] is None
    assert created.json()["coadmin_id"] == 10
    assert created.json()["coadmin_username"] == "default_coadmin"
    assert created.json()["amount"] == "120.00"
    assert created.json()["payment_ids"] == [1, 2]
    assert created.json()["cashout_ids"] == [1]
    assert repeated.status_code == 409
    assert all(item["net"] == "0.00" for item in ledger.json()["items"])
    assert ledger.json()["coadmin_summaries"][0]["net"] == "0.00"
    assert history.json()["items"][0]["scope"] == "coadmin"
    assert history.json()["items"][0]["coadmin_username"] == "default_coadmin"


@pytest.mark.asyncio
async def test_settlement_reduces_net_and_writes_audit() -> None:
    await seed_done_payment(1, amount="1000.00")
    await seed_cashout(1, amount="300.00")

    async with api_client_for(ADMIN) as client:
        created = await client.post("/api/admin/ledger/staff/42/settlements", json={})
        repeated = await client.post("/api/admin/ledger/staff/42/settlements", json={})
        ledger = await client.get("/api/admin/ledger")
        history = await client.get("/api/admin/settlements")

    assert created.status_code == 201
    assert repeated.status_code == 409
    assert repeated.json()["detail"] == "Nothing to settle."
    assert created.json()["amount"] == "700.00"
    assert created.json()["status"] == "done"
    assert created.json()["payment_ids"] == [1]
    assert created.json()["cashout_ids"] == [1]
    assert ledger.json()["items"][0]["total_in"] == "0.00"
    assert ledger.json()["items"][0]["total_out"] == "0.00"
    assert ledger.json()["items"][0]["settled_amount"] == "0.00"
    assert ledger.json()["items"][0]["net"] == "0.00"
    assert history.json()["items"][0]["amount"] == "700.00"
    assert history.json()["items"][0]["payment_ids"] == [1]
    assert history.json()["items"][0]["cashout_ids"] == [1]
    async with TestSessionFactory() as session:
        actions = (
            await session.scalars(
                select(StaffSettlementAuditLog.action).order_by(StaffSettlementAuditLog.id)
            )
        ).all()
        payment = await session.get(PaymentEvent, 1)
        cashout = await session.get(CashoutRequest, 1)
    assert [action.value for action in actions] == ["created", "done"]
    assert payment is not None
    assert payment.settlement_id == created.json()["id"]
    assert cashout is not None
    assert cashout.settlement_id == created.json()["id"]


@pytest.mark.asyncio
async def test_cannot_settle_zero_or_negative_and_cancelled_does_not_reduce_net() -> None:
    await seed_done_payment(1, amount="100.00")
    await seed_cashout(1, amount="100.00")
    async with TestSessionFactory() as session:
        session.add(
            StaffSettlement(
                staff_id=42,
                amount=Decimal("50.00"),
                status=StaffSettlementStatus.CANCELLED,
                created_by_admin_id=1,
            )
        )
        await session.commit()

    async with api_client_for(ADMIN) as client:
        rejected = await client.post("/api/admin/ledger/staff/42/settlements", json={})
        ledger = await client.get("/api/admin/ledger")

    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "Nothing to settle."
    assert ledger.json()["items"][0]["settled_amount"] == "0.00"
    assert ledger.json()["items"][0]["net"] == "0.00"


@pytest.mark.asyncio
async def test_ledger_only_counts_transactions_not_in_completed_settlement() -> None:
    await seed_done_payment(1, amount="1000.00")
    await seed_cashout(1, amount="300.00")

    async with api_client_for(ADMIN) as client:
        await client.post("/api/admin/ledger/staff/42/settlements", json={})

    await seed_done_payment(2, amount="80.00")
    await seed_cashout(2, amount="20.00")

    async with api_client_for(ADMIN) as client:
        ledger = await client.get("/api/admin/ledger")

    item = ledger.json()["items"][0]
    assert item["total_in"] == "80.00"
    assert item["total_out"] == "20.00"
    assert item["settled_amount"] == "0.00"
    assert item["net"] == "60.00"
    assert item["payments_count"] == 1
    assert item["cashouts_count"] == 1


@pytest.mark.asyncio
async def test_admin_can_adjust_total_in_and_staff_cannot() -> None:
    await seed_done_payment(1, amount="128.75")

    async with api_client_for(ADMIN) as admin_client:
        adjusted = await admin_client.post(
            "/api/admin/ledger/staff/42/adjustments",
            json={"new_total_in": "100.00", "reason": "Correction"},
        )
        ledger = await admin_client.get("/api/admin/ledger")
        history = await admin_client.get("/api/admin/ledger/adjustments")
    async with api_client_for(STAFF) as staff_client:
        forbidden = await staff_client.post(
            "/api/admin/ledger/staff/42/adjustments",
            json={"new_total_in": "150.00", "reason": "Nope"},
        )

    assert adjusted.status_code == 201
    assert adjusted.json()["amount_delta"] == "-28.75"
    assert adjusted.json()["previous_total_in"] == "128.75"
    assert adjusted.json()["new_total_in"] == "100.00"
    assert adjusted.json()["reason"] == "Correction"
    assert ledger.json()["items"][0]["total_in"] == "100.00"
    assert ledger.json()["items"][0]["net"] == "100.00"
    assert history.json()["items"][0]["reason"] == "Correction"
    assert history.json()["items"][0]["created_by_admin_username"] == "admin"
    assert forbidden.status_code == 403


@pytest.mark.asyncio
async def test_settlement_includes_adjustment_and_resets_ledger_to_zero() -> None:
    await seed_done_payment(1, amount="128.75")

    async with api_client_for(ADMIN) as client:
        adjusted = await client.post(
            "/api/admin/ledger/staff/42/adjustments",
            json={"new_total_in": "100.00", "reason": "Correction"},
        )
        settlement = await client.post("/api/admin/ledger/staff/42/settlements", json={})
        ledger = await client.get("/api/admin/ledger")

    assert settlement.status_code == 201
    assert settlement.json()["amount"] == "100.00"
    assert settlement.json()["adjustment_ids"] == [adjusted.json()["id"]]
    assert ledger.json()["items"][0]["total_in"] == "0.00"
    assert ledger.json()["items"][0]["net"] == "0.00"
    async with TestSessionFactory() as session:
        adjustment = await session.get(LedgerAdjustment, adjusted.json()["id"])
    assert adjustment is not None
    assert adjustment.settlement_id == settlement.json()["id"]


@pytest.mark.asyncio
async def test_detached_adjustments_do_not_attach_to_recreated_staff() -> None:
    async with TestSessionFactory() as session:
        old_staff = await session.get(User, 42)
        assert old_staff is not None
        await session.delete(old_staff)
        session.add(
            LedgerAdjustment(
                staff_id=None,
                type=LedgerAdjustmentType.TOTAL_IN_ADJUSTMENT,
                amount_delta=Decimal("500.00"),
                previous_total_in=Decimal("0.00"),
                new_total_in=Decimal("500.00"),
                reason="Old deleted staff adjustment",
                created_by_admin_id=1,
            )
        )
        await session.commit()

    async with TestSessionFactory() as session:
        session.add(make_user(43, "Sarah", UserRole.STAFF))
        await session.commit()

    async with api_client_for(ADMIN) as client:
        ledger = await client.get("/api/admin/ledger")
        hidden_history = await client.get("/api/admin/ledger/adjustments")
        deleted_history = await client.get(
            "/api/admin/ledger/adjustments",
            params={"include_deleted": "true"},
        )

    assert ledger.json()["items"][0]["staff_id"] == 43
    assert ledger.json()["items"][0]["total_in"] == "0.00"
    assert hidden_history.json()["items"] == []
    assert deleted_history.json()["items"][0]["staff_username"] == "Deleted Staff"


@pytest.mark.asyncio
async def test_admin_ledger_ignores_detached_historical_rows_after_staff_deletion() -> None:
    await seed_done_payment(1, staff_id=None, amount="999.00")
    await seed_cashout(1, staff_id=None, amount="400.00")
    async with TestSessionFactory() as session:
        session.add(
            StaffSettlement(
                id=1,
                staff_id=None,
                amount=Decimal("599.00"),
                status=StaffSettlementStatus.DONE,
                completed_by_admin_id=1,
                completed_at=datetime(2026, 7, 3, 12, tzinfo=UTC),
                created_by_admin_id=1,
            )
        )
        await session.commit()

    await seed_done_payment(2, staff_id=42, amount="120.00")
    await seed_cashout(2, staff_id=42, amount="50.00")

    async with api_client_for(ADMIN) as client:
        ledger = await client.get("/api/admin/ledger")
        history = await client.get("/api/admin/settlements")
        deleted_history = await client.get(
            "/api/admin/settlements",
            params={"include_deleted": "true"},
        )

    assert ledger.status_code == 200
    item = ledger.json()["items"][0]
    assert item["staff_id"] == 42
    assert item["total_in"] == "120.00"
    assert item["total_out"] == "50.00"
    assert item["settlements_count"] == 0
    assert item["net"] == "70.00"
    assert history.status_code == 200
    assert history.json()["items"] == []
    assert deleted_history.status_code == 200
    assert deleted_history.json()["items"][0]["staff_id"] is None
    assert deleted_history.json()["items"][0]["staff_username"] == "Deleted Staff"


@pytest.mark.asyncio
async def test_active_staff_settlements_show_when_deleted_rows_are_hidden() -> None:
    async with TestSessionFactory() as session:
        session.add(
            StaffSettlement(
                id=1,
                staff_id=None,
                amount=Decimal("599.00"),
                status=StaffSettlementStatus.DONE,
                completed_by_admin_id=1,
                completed_at=datetime(2026, 7, 3, 12, tzinfo=UTC),
                created_by_admin_id=1,
            )
        )
        session.add(
            StaffSettlement(
                id=2,
                staff_id=42,
                amount=Decimal("70.00"),
                status=StaffSettlementStatus.DONE,
                completed_by_admin_id=1,
                completed_at=datetime(2026, 7, 4, 12, tzinfo=UTC),
                created_by_admin_id=1,
            )
        )
        await session.commit()

    async with api_client_for(ADMIN) as client:
        history = await client.get("/api/admin/settlements")

    assert history.status_code == 200
    assert len(history.json()["items"]) == 1
    assert history.json()["items"][0]["staff_id"] == 42
    assert history.json()["items"][0]["staff_username"] == "Sarah"


@pytest.mark.asyncio
async def test_recreated_username_starts_fresh_after_deleted_staff_rows_detached() -> None:
    async with TestSessionFactory() as session:
        old_staff = await session.get(User, 42)
        assert old_staff is not None
        await session.delete(old_staff)
        await session.commit()

    await seed_done_payment(1, staff_id=None, amount="500.00")
    await seed_cashout(1, staff_id=None, amount="200.00")

    async with TestSessionFactory() as session:
        session.add(make_user(43, "Sarah", UserRole.STAFF))
        await session.commit()
    await seed_done_payment(2, staff_id=43, amount="75.00")

    async with api_client_for(ADMIN) as client:
        ledger = await client.get("/api/admin/ledger")

    assert ledger.status_code == 200
    assert ledger.json()["items"] == [
        {
            "staff_id": 43,
            "staff_username": "Sarah",
            "staff_color": "#2563EB",
            "coadmin_id": None,
            "coadmin_username": "default_coadmin",
            "total_in": "75.00",
            "total_out": "0.00",
            "settled_amount": "0.00",
            "net": "75.00",
            "payments_count": 1,
            "cashouts_count": 0,
            "settlements_count": 0,
        }
    ]


@pytest.mark.asyncio
async def test_date_filters_and_history_pagination() -> None:
    await seed_done_payment(
        1,
        amount="100.10",
        completed_at=datetime(2026, 7, 1, 12, tzinfo=UTC),
    )
    await seed_done_payment(
        2,
        amount="999.99",
        completed_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
    )
    await seed_cashout(
        1,
        amount="25.05",
        completed_at=datetime(2026, 7, 2, 12, tzinfo=UTC),
    )
    async with api_client_for(ADMIN) as client:
        await client.post(
            "/api/admin/ledger/staff/42/settlements",
            params={"date_from": "2026-07-01", "date_to": "2026-07-31"},
            json={},
        )
        ledger = await client.get(
            "/api/admin/ledger",
            params={"date_from": "2026-07-01", "date_to": "2026-07-31"},
        )
        first_page = await client.get("/api/admin/settlements", params={"limit": 1})
        second_page = await client.get(
            "/api/admin/settlements",
            params={"limit": 1, "offset": 1},
        )

    item = ledger.json()["items"][0]
    body = ledger.json()
    assert body["calculation_type"] == "custom_range"
    assert body["timezone"] == "Asia/Kathmandu"
    assert body["includes_settled"] is True
    assert item["total_in"] == "100.10"
    assert item["total_out"] == "25.05"
    assert item["net"] == "75.05"
    assert first_page.json()["has_more"] is False
    assert second_page.json()["items"] == []


@pytest.mark.asyncio
async def test_date_filtered_ledger_uses_nepal_day_boundaries_and_completed_at() -> None:
    await seed_done_payment(
        1,
        amount="10.00",
        created_at=datetime(2026, 7, 15, 12, tzinfo=UTC),
        completed_at=datetime(2026, 7, 14, 18, 15, tzinfo=UTC),
    )
    await seed_done_payment(
        2,
        amount="99.00",
        completed_at=datetime(2026, 7, 14, 18, 14, 59, tzinfo=UTC),
    )
    await seed_done_payment(
        3,
        amount="20.00",
        created_at=datetime(2026, 7, 14, 12, tzinfo=UTC),
        completed_at=datetime(2026, 7, 15, 18, 14, 59, tzinfo=UTC),
    )
    await seed_done_payment(
        4,
        amount="88.00",
        completed_at=datetime(2026, 7, 15, 18, 15, tzinfo=UTC),
    )
    await seed_cashout(
        1,
        amount="5.00",
        completed_at=datetime(2026, 7, 15, 1, tzinfo=UTC),
    )
    await seed_cashout(
        2,
        amount="77.00",
        status=CashoutStatus.CANCELLED,
        completed_at=datetime(2026, 7, 15, 1, tzinfo=UTC),
    )
    await seed_cashout(
        3,
        amount="66.00",
        status=CashoutStatus.PENDING,
        completed_at=datetime(2026, 7, 15, 1, tzinfo=UTC),
    )
    await seed_adjustment(
        1,
        amount_delta="3.00",
        created_at=datetime(2026, 7, 15, 2, tzinfo=UTC),
    )
    await seed_adjustment(
        2,
        amount_delta="-2.00",
        created_at=datetime(2026, 7, 15, 3, tzinfo=UTC),
    )

    async with api_client_for(ADMIN) as client:
        response = await client.get(
            "/api/admin/ledger",
            params={"date_from": "2026-07-15", "date_to": "2026-07-15"},
        )

    body = response.json()
    item = body["items"][0]
    assert body["calculation_type"] == "custom_range"
    assert body["period_start"] == "2026-07-15T00:00:00+05:45"
    assert body["period_end"] == "2026-07-16T00:00:00+05:45"
    assert item["total_in"] == "31.00"
    assert item["total_out"] == "5.00"
    assert item["net"] == "26.00"
    assert item["payments_count"] == 2
    assert item["cashouts_count"] == 1


@pytest.mark.asyncio
async def test_last_12_hours_uses_one_captured_timestamp_and_includes_settled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 7, 15, 18, 15, tzinfo=UTC)
    monkeypatch.setattr(
        LedgerService,
        "_now_utc",
        staticmethod(lambda: fixed_now),
    )
    async with TestSessionFactory() as session:
        session.add(
            StaffSettlement(
                id=99,
                staff_id=42,
                amount=Decimal("1.00"),
                status=StaffSettlementStatus.DONE,
                completed_by_admin_id=1,
                completed_at=fixed_now,
                created_by_admin_id=1,
            )
        )
        await session.commit()

    await seed_done_payment(
        1,
        amount="10.00",
        completed_at=fixed_now - timedelta(hours=11, minutes=59),
    )
    await seed_done_payment(
        2,
        amount="20.00",
        completed_at=fixed_now - timedelta(hours=12),
        settlement_id=99,
    )
    await seed_done_payment(
        3,
        amount="99.00",
        completed_at=fixed_now - timedelta(hours=12, seconds=1),
    )
    await seed_done_payment(
        4,
        amount="88.00",
        completed_at=fixed_now - timedelta(hours=1),
        status=PaymentStatus.PENDING,
    )
    await seed_cashout(
        1,
        amount="5.00",
        completed_at=fixed_now - timedelta(hours=1),
        settlement_id=99,
    )
    await seed_cashout(
        2,
        amount="66.00",
        created_at=fixed_now - timedelta(hours=1),
        completed_at=fixed_now - timedelta(hours=13),
    )
    await seed_cashout(
        3,
        amount="77.00",
        status=CashoutStatus.CANCELLED,
        completed_at=fixed_now - timedelta(hours=1),
    )
    await seed_adjustment(
        1,
        amount_delta="-2.00",
        created_at=fixed_now - timedelta(minutes=30),
        settlement_id=99,
    )

    async with api_client_for(ADMIN) as client:
        ledger = await client.get(
            "/api/admin/ledger",
            params={"calculation_mode": "last_12_hours"},
        )
        drilldown = await client.get(
            "/api/admin/ledger/drilldown",
            params={"calculation_mode": "last_12_hours", "staff_id": "42"},
        )

    body = ledger.json()
    item = body["items"][0]
    assert body["calculation_type"] == "rolling_activity"
    assert body["rolling_hours"] == 12
    assert body["timezone"] == "Asia/Kathmandu"
    assert body["period_start"] == "2026-07-15T12:00:00+05:45"
    assert body["period_end"] == "2026-07-16T00:00:00+05:45"
    assert body["generated_at"] == "2026-07-15T18:15:00Z"
    assert body["includes_settled"] is True
    assert item["payment_total"] == "30.00"
    assert item["adjustment_total"] == "-2.00"
    assert item["total_in"] == "28.00"
    assert item["total_out"] == "5.00"
    assert item["net"] == "23.00"
    assert item["payments_count"] == 2
    assert item["cashouts_count"] == 1
    drilldown_body = drilldown.json()
    assert [payment["id"] for payment in drilldown_body["payments"]] == [2, 1]
    assert drilldown_body["payments"][0]["settlement_id"] == 99
    assert [cashout["id"] for cashout in drilldown_body["cashouts"]] == [1]
    assert drilldown_body["cashouts"][0]["settlement_id"] == 99
    assert [adjustment["id"] for adjustment in drilldown_body["adjustments"]] == [1]
    assert drilldown_body["adjustments"][0]["settlement_id"] == 99

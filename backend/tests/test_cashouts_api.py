from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
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
from app.models.cashout import (
    CashoutAuditAction,
    CashoutRequest,
    CashoutRequestAudit,
    CashoutStatus,
    CashoutTelegramStatus,
)
from app.models.user import User, UserRole
from app.services import cashout as cashout_service

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


def make_user(user_id: int, username: str, role: UserRole) -> User:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    return User(
        id=user_id,
        username=username,
        password_hash="not-used",
        role=role,
        is_active=True,
        staff_color="#2563EB",
        created_at=timestamp,
        updated_at=timestamp,
    )


STAFF = make_user(42, "sarah", UserRole.STAFF)
OTHER_STAFF = make_user(84, "alex", UserRole.STAFF)
ADMIN = make_user(1, "admin", UserRole.ADMIN)


@pytest_asyncio.fixture(autouse=True)
async def reset_database() -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSessionFactory() as session:
        session.add_all(
            [
                make_user(42, "sarah", UserRole.STAFF),
                make_user(84, "alex", UserRole.STAFF),
                make_user(1, "admin", UserRole.ADMIN),
            ]
        )
        await session.commit()
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def staff_client() -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    async def override_current_user() -> User:
        return STAFF

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_current_user
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_client() -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    async def override_current_user() -> User:
        return ADMIN

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_current_user
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.clear()


async def seed_cashout(
    cashout_id: int,
    *,
    staff_id: int,
    tag: str,
    status: CashoutStatus = CashoutStatus.PENDING,
    telegram_status: CashoutTelegramStatus = CashoutTelegramStatus.PENDING,
    telegram_chat_id: int | None = None,
    telegram_message_id: int | None = None,
    created_at: datetime | None = None,
) -> None:
    timestamp = created_at or datetime(2026, 7, 6, tzinfo=UTC) + timedelta(
        minutes=cashout_id
    )
    async with TestSessionFactory() as session:
        session.add(
            CashoutRequest(
                id=cashout_id,
                request_number=f"CR-{cashout_id:06d}",
                idempotency_key=f"00000000-0000-0000-0000-{cashout_id:012d}",
                player_tag=tag,
                amount=Decimal("250.00"),
                notes=None,
                status=status,
                telegram_status=telegram_status,
                telegram_chat_id=telegram_chat_id,
                telegram_message_id=telegram_message_id,
                telegram_random_id=10_000 + cashout_id,
                created_by_staff_id=staff_id,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_staff_creates_trimmed_idempotent_cashout(
    staff_client: AsyncClient,
) -> None:
    payload = {
        "player_tag": "  ABC12345  ",
        "amount": "250.00",
        "notes": "  VIP Player  ",
        "idempotency_key": "9b3e7304-f4df-44b6-b5d7-267defbe7813",
    }

    first = await staff_client.post("/api/cashouts", json=payload)
    repeated = await staff_client.post("/api/cashouts", json=payload)

    assert first.status_code == 201
    assert repeated.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]
    assert first.json()["request_number"] == "CR-000001"
    assert first.json()["player_tag"] == "ABC12345"
    assert first.json()["notes"] == "VIP Player"
    assert first.json()["status"] == "pending"
    assert first.json()["telegram_status"] == "pending"
    async with TestSessionFactory() as session:
        assert await session.scalar(select(func.count(CashoutRequest.id))) == 1
        actions = (
            await session.scalars(select(CashoutRequestAudit.action))
        ).all()
        assert actions == [CashoutAuditAction.CREATED]


@pytest.mark.asyncio
async def test_cashout_validation_and_idempotency_conflict(
    staff_client: AsyncClient,
) -> None:
    key = "9b3e7304-f4df-44b6-b5d7-267defbe7813"
    invalid_tag = await staff_client.post(
        "/api/cashouts",
        json={
            "player_tag": "   ",
            "amount": "1.00",
            "idempotency_key": key,
        },
    )
    invalid_amount = await staff_client.post(
        "/api/cashouts",
        json={
            "player_tag": "ABC",
            "amount": "0",
            "idempotency_key": key,
        },
    )
    await staff_client.post(
        "/api/cashouts",
        json={
            "player_tag": "ABC",
            "amount": "10.00",
            "idempotency_key": key,
        },
    )
    conflict = await staff_client.post(
        "/api/cashouts",
        json={
            "player_tag": "DIFFERENT",
            "amount": "10.00",
            "idempotency_key": key,
        },
    )

    assert invalid_tag.status_code == 422
    assert invalid_amount.status_code == 422
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_staff_history_is_private_newest_first_and_paginated(
    staff_client: AsyncClient,
) -> None:
    for cashout_id in range(1, 23):
        await seed_cashout(cashout_id, staff_id=42, tag=f"OWN-{cashout_id}")
    await seed_cashout(30, staff_id=84, tag="OTHER")

    first = await staff_client.get("/api/cashouts")
    second = await staff_client.get(
        "/api/cashouts",
        params={"limit": 20, "offset": 20},
    )

    assert first.status_code == 200
    assert [item["id"] for item in first.json()["items"]] == list(
        range(22, 2, -1)
    )
    assert first.json()["has_more"] is True
    assert [item["id"] for item in second.json()["items"]] == [2, 1]
    assert all(item["created_by_staff_id"] == 42 for item in first.json()["items"])


@pytest.mark.asyncio
async def test_admin_can_filter_search_complete_cancel_and_retry(
    admin_client: AsyncClient,
) -> None:
    await seed_cashout(1, staff_id=42, tag="VIP-PLAYER")
    await seed_cashout(
        2,
        staff_id=84,
        tag="REGULAR",
        status=CashoutStatus.FAILED_TO_SEND,
        telegram_status=CashoutTelegramStatus.FAILED_TO_SEND,
    )

    filtered = await admin_client.get(
        "/api/cashouts",
        params={"search": "VIP", "status": "pending"},
    )
    completed = await admin_client.post("/api/cashouts/1/complete")
    retried = await admin_client.post("/api/cashouts/2/retry-telegram")
    cancelled = await admin_client.post("/api/cashouts/2/cancel")
    audit = await admin_client.get("/api/cashouts/2/audit")

    assert [item["id"] for item in filtered.json()["items"]] == [1]
    assert filtered.json()["items"][0]["requested_by"]["username"] == "sarah"
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_at"] is not None
    assert retried.json()["telegram_status"] == "pending"
    assert cancelled.json()["status"] == "cancelled"
    assert [entry["action"] for entry in audit.json()] == [
        "telegram_retry",
        "cancelled",
    ]


@pytest.mark.asyncio
async def test_admin_cancelling_multiple_cashouts_deletes_each_linked_message(
    admin_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted_targets: list[cashout_service.CashoutTelegramDeletionTarget] = []

    async def delete_spy(
        target: cashout_service.CashoutTelegramDeletionTarget,
        *,
        cancellation_status: str,
    ) -> cashout_service.CashoutTelegramDeletionResult:
        assert cancellation_status == "cancelled"
        deleted_targets.append(target)
        return cashout_service.CashoutTelegramDeletionResult("deleted")

    monkeypatch.setattr(
        cashout_service,
        "_delete_cancelled_cashout_telegram_message",
        delete_spy,
    )
    for cashout_id, message_id in ((41, 9001), (42, 9002), (43, 9003)):
        await seed_cashout(
            cashout_id,
            staff_id=42,
            tag=f"BULK-{cashout_id}",
            status=CashoutStatus.SENT,
            telegram_status=CashoutTelegramStatus.SENT,
            telegram_chat_id=-1001234567890,
            telegram_message_id=message_id,
        )

    responses = [
        await admin_client.post(f"/api/cashouts/{cashout_id}/cancel")
        for cashout_id in (41, 42, 43)
    ]

    assert [response.status_code for response in responses] == [200, 200, 200]
    assert [
        (
            target.cashout_request_id,
            target.telegram_chat_id,
            target.telegram_message_id,
        )
        for target in deleted_targets
    ] == [
        (41, -1001234567890, 9001),
        (42, -1001234567890, 9002),
        (43, -1001234567890, 9003),
    ]


@pytest.mark.asyncio
async def test_cancelled_cashout_cancel_is_retry_safe(
    admin_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deletion_statuses: list[str] = []

    async def delete_spy(
        target: cashout_service.CashoutTelegramDeletionTarget,
        *,
        cancellation_status: str,
    ) -> cashout_service.CashoutTelegramDeletionResult:
        assert target.cashout_request_id == 44
        deletion_statuses.append(cancellation_status)
        return cashout_service.CashoutTelegramDeletionResult("already_missing")

    monkeypatch.setattr(
        cashout_service,
        "_delete_cancelled_cashout_telegram_message",
        delete_spy,
    )
    await seed_cashout(
        44,
        staff_id=42,
        tag="RETRY-CANCEL",
        status=CashoutStatus.CANCELLED,
        telegram_status=CashoutTelegramStatus.SENT,
        telegram_chat_id=-1001234567890,
        telegram_message_id=9004,
    )

    response = await admin_client.post("/api/cashouts/44/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert deletion_statuses == ["already_cancelled"]


@pytest.mark.asyncio
async def test_cancelled_cashout_is_hidden_from_staff_but_retained_for_admin() -> None:
    async with api_client_for(STAFF) as staff_api:
        created = await staff_api.post(
            "/api/cashouts",
            json={
                "player_tag": "CANCEL-ME",
                "amount": "75.00",
                "notes": "Keep the audit",
                "idempotency_key": "3097fdd0-b12e-4cc1-adb2-48aa8ebfa22b",
            },
        )
        cashout_id = created.json()["id"]
        before_cancel = await staff_api.get("/api/cashouts")

    async with api_client_for(ADMIN) as admin_api:
        cancelled = await admin_api.post(f"/api/cashouts/{cashout_id}/cancel")
        admin_history = await admin_api.get("/api/cashouts")
        audit = await admin_api.get(f"/api/cashouts/{cashout_id}/audit")

    async with api_client_for(STAFF) as staff_api:
        staff_history = await staff_api.get("/api/cashouts")

    assert created.status_code == 201
    assert [item["id"] for item in before_cancel.json()["items"]] == [cashout_id]
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert [item["id"] for item in staff_history.json()["items"]] == []
    assert [item["id"] for item in admin_history.json()["items"]] == [cashout_id]
    assert admin_history.json()["items"][0]["status"] == "cancelled"
    assert [entry["action"] for entry in audit.json()] == [
        "created",
        "cancelled",
    ]
    async with TestSessionFactory() as session:
        stored = await session.get(CashoutRequest, cashout_id)
        assert stored is not None
        assert stored.status == CashoutStatus.CANCELLED


@pytest.mark.asyncio
async def test_staff_cannot_administer_or_edit_another_cashout(
    staff_client: AsyncClient,
) -> None:
    await seed_cashout(1, staff_id=84, tag="OTHER")

    edit = await staff_client.patch(
        "/api/cashouts/1/notes",
        json={"notes": "Not mine"},
    )
    complete = await staff_client.post("/api/cashouts/1/complete")
    audit = await staff_client.get("/api/cashouts/1/audit")

    assert edit.status_code == 403
    assert complete.status_code == 403
    assert audit.status_code == 403


@pytest.mark.asyncio
async def test_completed_cashout_notes_are_immutable(
    staff_client: AsyncClient,
) -> None:
    await seed_cashout(
        1,
        staff_id=42,
        tag="DONE",
        status=CashoutStatus.COMPLETED,
    )

    response = await staff_client.patch(
        "/api/cashouts/1/notes",
        json={"notes": "Too late"},
    )

    assert response.status_code == 409

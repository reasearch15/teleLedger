from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.auth.security import hash_password
from app.db.base import Base
from app.db.session import get_auth_session, get_session
from app.main import app
from app.models.cashout import CashoutRequest, CashoutStatus, CashoutTelegramStatus
from app.models.payment_event import PaymentEvent, PaymentStatus
from app.models.telegram_message import TelegramMessage
from app.models.user import User, UserRole

ADMIN_PASSWORD = "A-secure-admin-password"
STAFF_PASSWORD = "A-secure-staff-password"
ADMIN_PASSWORD_HASH = hash_password(ADMIN_PASSWORD)
STAFF_PASSWORD_HASH = hash_password(STAFF_PASSWORD)

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
async def client() -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_auth_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


async def seed_user(
    user_id: int,
    *,
    username: str,
    password_hash: str,
    role: UserRole,
    is_active: bool = True,
) -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    async with TestSessionFactory() as session:
        session.add(
            User(
                id=user_id,
                username=username,
                password_hash=password_hash,
                role=role,
                is_active=is_active,
                staff_color="#2563EB",
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await session.commit()


async def login(client: AsyncClient, username: str, password: str) -> None:
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200


async def seed_done_payment_for_staff(staff_id: int, payment_id: int = 1) -> None:
    timestamp = datetime(2026, 6, 29, 12, tzinfo=UTC)
    async with TestSessionFactory() as session:
        session.add(
            TelegramMessage(
                id=payment_id,
                telegram_chat_id=100,
                telegram_message_id=payment_id,
                sender_id=None,
                sender_name="Sender",
                raw_text="payment",
                received_at=timestamp,
                created_at=timestamp,
            )
        )
        session.add(
            PaymentEvent(
                id=payment_id,
                telegram_message_id=payment_id,
                recipient_tag="player",
                amount=Decimal("36.28"),
                payment_sender_name="Sender",
                payment_datetime=timestamp,
                total_in=Decimal("36.28"),
                total_out=Decimal("0.00"),
                raw_text="payment",
                status=PaymentStatus.DONE,
                claimed_by_staff_id=staff_id,
                completed_by_staff_id=staff_id,
                completed_at=timestamp,
                parser_confidence=100,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await session.commit()


async def seed_cashout_for_staff(staff_id: int, cashout_id: int = 1) -> None:
    timestamp = datetime(2026, 6, 29, 12, tzinfo=UTC)
    async with TestSessionFactory() as session:
        session.add(
            CashoutRequest(
                id=cashout_id,
                request_number=f"CR-{cashout_id:06d}",
                idempotency_key=f"key-{cashout_id}",
                player_tag="#player",
                amount=Decimal("10.00"),
                notes=None,
                status=CashoutStatus.COMPLETED,
                telegram_status=CashoutTelegramStatus.SENT,
                telegram_random_id=1000 + cashout_id,
                created_by_staff_id=staff_id,
                completed_by_staff_id=staff_id,
                completed_at=timestamp,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_admin_can_delete_staff(client: AsyncClient) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await seed_user(
        2,
        username="staff01",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
    )
    await login(client, "admin", ADMIN_PASSWORD)

    response = await client.delete("/api/admin/staff/2")

    assert response.status_code == 204
    staff_response = await client.get("/api/admin/staff")
    assert staff_response.json() == []
    async with TestSessionFactory() as session:
        assert await session.get(User, 2) is None


@pytest.mark.asyncio
async def test_staff_cannot_delete_staff(client: AsyncClient) -> None:
    await seed_user(
        2,
        username="staff_user",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
    )
    await seed_user(
        3,
        username="other_staff",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
    )
    await login(client, "staff_user", STAFF_PASSWORD)

    response = await client.delete("/api/admin/staff/3")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_delete_self(client: AsyncClient) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await login(client, "admin", ADMIN_PASSWORD)

    response = await client.delete("/api/admin/staff/1")

    assert response.status_code == 403
    assert response.json()["detail"] == "Administrators cannot delete their own account."


@pytest.mark.asyncio
async def test_deleted_staff_cannot_login(client: AsyncClient) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await seed_user(
        2,
        username="staff01",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
    )
    await login(client, "admin", ADMIN_PASSWORD)
    await client.delete("/api/admin/staff/2")

    login_response = await client.post(
        "/api/auth/login",
        json={"username": "staff01", "password": STAFF_PASSWORD},
    )

    assert login_response.status_code == 401


@pytest.mark.asyncio
async def test_deleted_staff_existing_session_is_invalid() -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await seed_user(
        2,
        username="staff01",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
    )

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_auth_session] = override_session
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as staff_client:
        await login(staff_client, "staff01", STAFF_PASSWORD)
        assert (await staff_client.get("/api/auth/me")).status_code == 200

        async with AsyncClient(transport=transport, base_url="http://test") as admin_client:
            await login(admin_client, "admin", ADMIN_PASSWORD)
            delete_response = await admin_client.delete("/api/admin/staff/2")
            assert delete_response.status_code == 204

        me_response = await staff_client.get("/api/auth/me")
        assert me_response.status_code == 401

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_same_username_can_be_created_again_with_new_id(
    client: AsyncClient,
) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await seed_user(
        2,
        username="staff01",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
    )
    await login(client, "admin", ADMIN_PASSWORD)
    await client.delete("/api/admin/staff/2")
    coadmin_response = await client.post(
        "/api/admin/coadmins",
        json={"username": "coadmin01", "password": "Another-secure-password"},
    )

    create_response = await client.post(
        "/api/admin/staff",
        json={
            "username": "staff01",
            "password": "Another-secure-password",
            "coadmin_id": coadmin_response.json()["id"],
        },
    )

    assert coadmin_response.status_code == 201
    assert create_response.status_code == 201
    assert create_response.json()["username"] == "staff01"
    async with TestSessionFactory() as session:
        recreated = await session.get(User, create_response.json()["id"])
        assert recreated is not None
        assert recreated.username == "staff01"
        assert recreated.is_active is True


@pytest.mark.asyncio
async def test_old_payment_and_cashout_rows_do_not_attach_to_recreated_staff(
    client: AsyncClient,
) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await seed_user(
        2,
        username="staff01",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
    )
    await seed_done_payment_for_staff(2)
    await seed_cashout_for_staff(2)
    await login(client, "admin", ADMIN_PASSWORD)
    await client.delete("/api/admin/staff/2")
    coadmin_response = await client.post(
        "/api/admin/coadmins",
        json={"username": "coadmin01", "password": "Another-secure-password"},
    )

    async with TestSessionFactory() as session:
        payment = await session.get(PaymentEvent, 1)
        cashout = await session.get(CashoutRequest, 1)
        assert payment is not None
        assert cashout is not None
        assert payment.claimed_by_staff_id is None
        assert payment.completed_by_staff_id is None
        assert cashout.created_by_staff_id is None
        assert cashout.completed_by_staff_id is None

    create_response = await client.post(
        "/api/admin/staff",
        json={
            "username": "staff01",
            "password": "Another-secure-password",
            "coadmin_id": coadmin_response.json()["id"],
        },
    )
    assert coadmin_response.status_code == 201
    assert create_response.status_code == 201
    new_staff_id = create_response.json()["id"]

    async with TestSessionFactory() as session:
        payment = await session.get(PaymentEvent, 1)
        cashout = await session.get(CashoutRequest, 1)
        assert payment is not None
        assert cashout is not None
        assert payment.claimed_by_staff_id is None
        assert payment.completed_by_staff_id is None
        assert cashout.created_by_staff_id is None
        assert cashout.completed_by_staff_id is None

    await login(client, "staff01", "Another-secure-password")
    history_response = await client.get("/api/payments/my-history")
    cashouts_response = await client.get("/api/cashouts")

    assert history_response.status_code == 200
    assert history_response.json()["items"] == []
    assert cashouts_response.status_code == 200
    assert cashouts_response.json()["items"] == []
    async with TestSessionFactory() as session:
        recreated = await session.get(User, new_staff_id)
        assert recreated is not None
        assert recreated.username == "staff01"

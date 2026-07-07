from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.auth import create_admin as create_admin_module
from app.auth.create_admin import AdminCredentials, prompt_admin_credentials
from app.auth.security import hash_password, verify_password
from app.db import retry as db_retry
from app.db.base import Base
from app.db.session import get_auth_session, get_session
from app.main import app
from app.models.user import User, UserRole
from app.services.user import AuthService

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


def test_create_admin_cli_prompt_logic() -> None:
    passwords = iter([ADMIN_PASSWORD, ADMIN_PASSWORD])

    credentials = prompt_admin_credentials(
        input_function=lambda _: "  Primary.Admin  ",
        password_function=lambda _: next(passwords),
    )

    assert credentials.username == "primary.admin"
    assert credentials.password == ADMIN_PASSWORD


@pytest.mark.asyncio
async def test_create_admin_cli_persists_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(create_admin_module, "SessionFactory", TestSessionFactory)

    username = await create_admin_module.create_admin(
        AdminCredentials(username="primary_admin", password=ADMIN_PASSWORD)
    )

    assert username == "primary_admin"
    async with TestSessionFactory() as session:
        admin = await session.scalar(
            select(User).where(User.username == "primary_admin")
        )
        assert admin is not None
        assert admin.role == UserRole.ADMIN
        assert admin.password_hash != ADMIN_PASSWORD


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )

    response = await client.post(
        "/api/auth/login",
        json={"username": "ADMIN", "password": ADMIN_PASSWORD},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert "telegram_ledger_session" in response.cookies
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    me_response = await client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "admin"
    logout_response = await client.post("/api/auth/logout")
    assert logout_response.status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_auth_me_retries_one_transient_disconnect(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await login(client, "admin", ADMIN_PASSWORD)
    original = AuthService.get_active_user
    call_count = 0
    logged_messages: list[str] = []

    async def flaky_get_active_user(self: AuthService, user_id: int) -> User:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise DBAPIError(
                "SELECT users",
                {},
                ConnectionResetError(10054, "connection reset by peer"),
                connection_invalidated=True,
            )
        return await original(self, user_id)

    monkeypatch.setattr(AuthService, "get_active_user", flaky_get_active_user)
    monkeypatch.setattr(db_retry, "SessionFactory", TestSessionFactory)
    monkeypatch.setattr(
        db_retry.logger,
        "warning",
        lambda message, **_: logged_messages.append(message),
    )
    monkeypatch.setattr(
        db_retry.logger,
        "info",
        lambda message, **_: logged_messages.append(message),
    )

    response = await client.get("/api/auth/me")

    assert response.status_code == 200
    assert call_count == 2
    assert "stale_database_connection_detected" in logged_messages
    assert "database_read_retry_succeeded" in logged_messages


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )

    response = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_inactive_staff_cannot_login(client: AsyncClient) -> None:
    await seed_user(
        2,
        username="inactive_staff",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
        is_active=False,
    )

    response = await client.post(
        "/api/auth/login",
        json={"username": "inactive_staff", "password": STAFF_PASSWORD},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_staff_cannot_create_staff(client: AsyncClient) -> None:
    await seed_user(
        2,
        username="staff_user",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
    )
    await login(client, "staff_user", STAFF_PASSWORD)

    response = await client.post(
        "/api/admin/staff",
        json={"username": "new_staff", "password": "Another-secure-password"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_create_staff(client: AsyncClient) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await login(client, "admin", ADMIN_PASSWORD)

    response = await client.post(
        "/api/admin/staff",
        json={"username": "New.Staff", "password": "Another-secure-password"},
    )

    assert response.status_code == 201
    assert response.json()["username"] == "new.staff"
    assert response.json()["role"] == "staff"
    staff_response = await client.get("/api/admin/staff")
    assert [staff["username"] for staff in staff_response.json()] == ["new.staff"]


@pytest.mark.asyncio
async def test_admin_can_disable_and_reset_staff(client: AsyncClient) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await seed_user(
        2,
        username="staff_user",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
    )
    await login(client, "admin", ADMIN_PASSWORD)

    reset_response = await client.patch(
        "/api/admin/staff/2/reset-password",
        json={"password": "Replacement-secure-password"},
    )
    disable_response = await client.patch("/api/admin/staff/2/disable")

    assert reset_response.status_code == 200
    assert disable_response.status_code == 200
    assert disable_response.json()["is_active"] is False
    async with TestSessionFactory() as session:
        staff = await session.get(User, 2)
        assert staff is not None
        valid, _ = verify_password(
            "Replacement-secure-password",
            staff.password_hash,
        )
        assert valid


@pytest.mark.asyncio
async def test_protected_payment_endpoint_requires_login(client: AsyncClient) -> None:
    response = await client.get("/api/payments")

    assert response.status_code == 401

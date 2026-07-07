from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.auth.security import hash_password, verify_password
from app.db.session import get_auth_session, get_session
from app.main import app
from app.models.user import User, UserRole

ADMIN_PASSWORD = "A-secure-admin-password"
STAFF_PASSWORD = "A-secure-staff-password"
COADMIN_PASSWORD = "Another-secure-password"
ADMIN_PASSWORD_HASH = hash_password(ADMIN_PASSWORD)
STAFF_PASSWORD_HASH = hash_password(STAFF_PASSWORD)
COADMIN_PASSWORD_HASH = hash_password(COADMIN_PASSWORD)

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
    from app.db.base import Base

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
    coadmin_id: int | None = None,
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
                coadmin_id=coadmin_id,
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


@pytest.mark.asyncio
async def test_admin_can_reset_coadmin_password(client: AsyncClient) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await seed_user(
        2,
        username="ops_coadmin",
        password_hash=COADMIN_PASSWORD_HASH,
        role=UserRole.COADMIN,
    )
    await login(client, "admin", ADMIN_PASSWORD)

    response = await client.patch(
        "/api/admin/coadmins/2/reset-password",
        json={"password": "Replacement-secure-password"},
    )

    assert response.status_code == 200
    async with TestSessionFactory() as session:
        coadmin = await session.get(User, 2)
        assert coadmin is not None
        valid, _ = verify_password(
            "Replacement-secure-password",
            coadmin.password_hash,
        )
        assert valid is True


@pytest.mark.asyncio
async def test_admin_can_delete_coadmin_with_no_assigned_staff(
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
        username="ops_coadmin",
        password_hash=COADMIN_PASSWORD_HASH,
        role=UserRole.COADMIN,
    )
    await login(client, "admin", ADMIN_PASSWORD)

    response = await client.delete("/api/admin/coadmins/2")

    assert response.status_code == 204
    async with TestSessionFactory() as session:
        assert await session.get(User, 2) is None


@pytest.mark.asyncio
async def test_admin_cannot_delete_coadmin_with_assigned_staff(
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
        username="ops_coadmin",
        password_hash=COADMIN_PASSWORD_HASH,
        role=UserRole.COADMIN,
    )
    await seed_user(
        3,
        username="staff_user",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
        coadmin_id=2,
    )
    await login(client, "admin", ADMIN_PASSWORD)

    response = await client.delete("/api/admin/coadmins/2")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Cannot delete coadmin while staff are assigned. "
        "Reassign or delete staff first."
    )
    async with TestSessionFactory() as session:
        assert await session.get(User, 2) is not None


@pytest.mark.asyncio
async def test_staff_cannot_manage_coadmins(client: AsyncClient) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await seed_user(
        2,
        username="ops_coadmin",
        password_hash=COADMIN_PASSWORD_HASH,
        role=UserRole.COADMIN,
    )
    await seed_user(
        3,
        username="staff_user",
        password_hash=STAFF_PASSWORD_HASH,
        role=UserRole.STAFF,
        coadmin_id=2,
    )
    await login(client, "staff_user", STAFF_PASSWORD)

    reset_response = await client.patch(
        "/api/admin/coadmins/2/reset-password",
        json={"password": "Replacement-secure-password"},
    )
    delete_response = await client.delete("/api/admin/coadmins/2")
    disable_response = await client.patch("/api/admin/coadmins/2/disable")

    assert reset_response.status_code == 403
    assert delete_response.status_code == 403
    assert disable_response.status_code == 403


@pytest.mark.asyncio
async def test_coadmin_cannot_manage_coadmins(client: AsyncClient) -> None:
    await seed_user(
        1,
        username="admin",
        password_hash=ADMIN_PASSWORD_HASH,
        role=UserRole.ADMIN,
    )
    await seed_user(
        2,
        username="ops_coadmin",
        password_hash=COADMIN_PASSWORD_HASH,
        role=UserRole.COADMIN,
    )
    await seed_user(
        3,
        username="other_coadmin",
        password_hash=COADMIN_PASSWORD_HASH,
        role=UserRole.COADMIN,
    )
    await login(client, "ops_coadmin", COADMIN_PASSWORD)

    reset_response = await client.patch(
        "/api/admin/coadmins/3/reset-password",
        json={"password": "Replacement-secure-password"},
    )
    delete_response = await client.delete("/api/admin/coadmins/3")

    assert reset_response.status_code == 403
    assert delete_response.status_code == 403

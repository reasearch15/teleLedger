from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.db import retry as db_retry
from app.db.base import Base
from app.db.session import get_auth_session, get_session
from app.main import app
from app.models.payment_event import PaymentEvent, PaymentStatus
from app.models.telegram_message import TelegramMessage
from app.models.user import User, UserRole
from app.services.payment import PaymentService

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

    async def override_current_user() -> User:
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        return User(
            id=42,
            username="ledger_staff",
            password_hash="not-used",
            role=UserRole.STAFF,
            is_active=True,
            created_at=timestamp,
            updated_at=timestamp,
        )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_auth_session] = override_session
    app.dependency_overrides[get_current_user] = override_current_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_client() -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    async def override_current_user() -> User:
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        return User(
            id=1,
            username="ledger_admin",
            password_hash="not-used",
            role=UserRole.ADMIN,
            is_active=True,
            staff_color="#7C3AED",
            created_at=timestamp,
            updated_at=timestamp,
        )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_auth_session] = override_session
    app.dependency_overrides[get_current_user] = override_current_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


async def seed_payment(
    payment_id: int,
    *,
    status: PaymentStatus = PaymentStatus.PENDING,
    recipient_tag: str = "Stephen_Mckinney_21",
    sender_name: str = "Krista R",
    payment_datetime: datetime = datetime(2026, 6, 29, 15, 8),
    created_at: datetime | None = None,
    received_at: datetime | None = None,
    telegram_message_id: int | None = None,
    claimed_by_staff_id: int | None = None,
    completed_by_staff_id: int | None = None,
    completed_at: datetime | None = None,
) -> None:
    timestamp = created_at or datetime(2026, 6, 29, 12, tzinfo=UTC) + timedelta(
        minutes=payment_id
    )
    completion_timestamp = completed_at or timestamp
    telegram_timestamp = received_at or timestamp
    raw_text = f"You received $36.28 from {sender_name} for {recipient_tag}"

    async with TestSessionFactory() as session:
        session.add(
            TelegramMessage(
                id=payment_id,
                telegram_chat_id=100,
                telegram_message_id=telegram_message_id or payment_id,
                sender_id=None,
                sender_name=sender_name,
                raw_text=raw_text,
                received_at=telegram_timestamp,
                created_at=timestamp,
            )
        )
        session.add(
            PaymentEvent(
                id=payment_id,
                telegram_message_id=payment_id,
                recipient_tag=recipient_tag,
                amount=Decimal("36.28"),
                payment_sender_name=sender_name,
                payment_datetime=payment_datetime,
                total_in=Decimal("5709.59"),
                total_out=Decimal("1881.66"),
                raw_text=raw_text,
                status=status,
                claimed_by_staff_id=claimed_by_staff_id,
                claimed_at=timestamp if claimed_by_staff_id is not None else None,
                completed_by_staff_id=(
                    completed_by_staff_id
                    if completed_by_staff_id is not None
                    else 99
                    if status == PaymentStatus.DONE
                    else None
                ),
                completed_at=(
                    completion_timestamp if status == PaymentStatus.DONE else None
                ),
                parser_confidence=100,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await session.commit()


async def seed_account(
    user_id: int,
    *,
    username: str,
    role: UserRole,
    color: str,
    coadmin_id: int | None = None,
) -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    async with TestSessionFactory() as session:
        session.add(
            User(
                id=user_id,
                username=username,
                password_hash="not-used",
                role=role,
                is_active=True,
                staff_color=color,
                coadmin_id=coadmin_id,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await session.commit()


@asynccontextmanager
async def payment_client_for(user: User) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    async def override_current_user() -> User:
        return user

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_auth_session] = override_session
    app.dependency_overrides[get_current_user] = override_current_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_payments_retries_one_transient_disconnect(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_payment(1)
    original = PaymentService.list_payments
    call_count = 0
    logged_messages: list[str] = []

    async def flaky_list(self: PaymentService, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise DBAPIError(
                "SELECT payment_events",
                {},
                ConnectionResetError(10054, "connection reset by peer"),
                connection_invalidated=True,
            )
        return await original(self, **kwargs)

    monkeypatch.setattr(PaymentService, "list_payments", flaky_list)
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

    response = await client.get("/api/payments")

    assert response.status_code == 200
    assert call_count == 2
    assert "stale_database_connection_detected" in logged_messages
    assert "database_read_retry_succeeded" in logged_messages


async def test_list_payments_newest_arrival_first(client: AsyncClient) -> None:
    await seed_payment(1, received_at=datetime(2026, 6, 28, tzinfo=UTC))
    await seed_payment(2, received_at=datetime(2026, 6, 30, tzinfo=UTC))

    response = await client.get("/api/payments")

    assert response.status_code == 200
    body = response.json()
    assert [payment["id"] for payment in body["items"]] == [2, 1]
    assert body == {
        "items": body["items"],
        "total": None,
        "limit": 7,
        "offset": 0,
        "has_more": False,
    }


@pytest.mark.asyncio
async def test_claimed_payments_keep_arrival_order(client: AsyncClient) -> None:
    await seed_payment(
        1,
        status=PaymentStatus.PENDING,
        received_at=datetime(2026, 6, 28, tzinfo=UTC),
    )
    await seed_payment(
        2,
        status=PaymentStatus.IN_PROGRESS,
        received_at=datetime(2026, 6, 30, tzinfo=UTC),
        claimed_by_staff_id=42,
    )

    response = await client.get("/api/payments")

    assert response.status_code == 200
    assert [payment["id"] for payment in response.json()["items"]] == [2, 1]


@pytest.mark.asyncio
async def test_admin_list_keeps_arrival_order_across_statuses(
    admin_client: AsyncClient,
) -> None:
    await seed_payment(
        1,
        status=PaymentStatus.DONE,
        received_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    await seed_payment(
        2,
        status=PaymentStatus.IN_PROGRESS,
        received_at=datetime(2026, 6, 30, tzinfo=UTC),
        claimed_by_staff_id=42,
    )
    await seed_payment(
        3,
        status=PaymentStatus.PENDING,
        received_at=datetime(2026, 6, 29, tzinfo=UTC),
    )

    response = await admin_client.get("/api/payments")

    assert response.status_code == 200
    assert [payment["id"] for payment in response.json()["items"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_equal_timestamps_use_telegram_message_id_desc(client: AsyncClient) -> None:
    timestamp = datetime(2026, 6, 29, 15, 8, tzinfo=UTC)
    await seed_payment(
        1,
        received_at=timestamp,
        telegram_message_id=800,
    )
    await seed_payment(
        2,
        received_at=timestamp,
        telegram_message_id=900,
    )

    response = await client.get("/api/payments")

    assert response.status_code == 200
    assert [payment["id"] for payment in response.json()["items"]] == [2, 1]


@pytest.mark.asyncio
async def test_equal_receipt_timestamps_use_payment_datetime_desc(
    client: AsyncClient,
) -> None:
    timestamp = datetime(2026, 6, 29, 15, 8, tzinfo=UTC)
    await seed_payment(
        1,
        received_at=timestamp,
        payment_datetime=datetime(2026, 6, 29, 12, 0),
        telegram_message_id=900,
    )
    await seed_payment(
        2,
        received_at=timestamp,
        payment_datetime=datetime(2026, 6, 29, 13, 0),
        telegram_message_id=800,
    )

    response = await client.get("/api/payments")

    assert response.status_code == 200
    assert [payment["id"] for payment in response.json()["items"]] == [2, 1]


@pytest.mark.asyncio
async def test_list_payments_defaults_to_seven_and_omits_raw_text(
    client: AsyncClient,
) -> None:
    for payment_id in range(1, 11):
        await seed_payment(payment_id)

    response = await client.get("/api/payments")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 7
    assert body["total"] is None
    assert body["limit"] == 7
    assert body["offset"] == 0
    assert body["has_more"] is True
    assert all("raw_text" not in payment for payment in body["items"])


@pytest.mark.asyncio
async def test_default_payment_page_executes_one_select_without_count(
    client: AsyncClient,
) -> None:
    await seed_payment(1)
    select_statements: list[str] = []

    def capture_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            select_statements.append(statement)

    event.listen(
        test_engine.sync_engine,
        "before_cursor_execute",
        capture_statement,
    )
    try:
        response = await client.get("/api/payments")
    finally:
        event.remove(
            test_engine.sync_engine,
            "before_cursor_execute",
            capture_statement,
        )

    assert response.status_code == 200
    assert len(select_statements) == 1
    assert "count(" not in select_statements[0].lower()


@pytest.mark.asyncio
async def test_list_payments_limit_is_capped_at_fifty(client: AsyncClient) -> None:
    for payment_id in range(1, 56):
        await seed_payment(payment_id)

    response = await client.get("/api/payments", params={"limit": 50})
    invalid_response = await client.get("/api/payments", params={"limit": 51})

    assert response.status_code == 200
    assert len(response.json()["items"]) == 50
    assert response.json()["has_more"] is True
    assert invalid_response.status_code == 422


@pytest.mark.asyncio
async def test_list_payments_offset_returns_next_page(client: AsyncClient) -> None:
    for payment_id in range(1, 11):
        await seed_payment(payment_id)

    first_response = await client.get("/api/payments")
    second_response = await client.get(
        "/api/payments",
        params={"limit": 7, "offset": 7},
    )

    assert [item["id"] for item in first_response.json()["items"]] == [
        10,
        9,
        8,
        7,
        6,
        5,
        4,
    ]
    assert [item["id"] for item in second_response.json()["items"]] == [3, 2, 1]
    assert second_response.json()["total"] is None
    assert second_response.json()["offset"] == 7
    assert second_response.json()["has_more"] is False


@pytest.mark.asyncio
async def test_claim_pending_payment(client: AsyncClient) -> None:
    await seed_payment(1)

    response = await client.post("/api/payments/1/claim")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "in_progress"
    assert body["claimed_by_staff_id"] == 42
    assert body["claimed_at"] is not None


@pytest.mark.asyncio
async def test_cannot_claim_done_payment(client: AsyncClient) -> None:
    await seed_payment(1, status=PaymentStatus.DONE)

    response = await client.post("/api/payments/1/claim")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_cannot_double_claim(client: AsyncClient) -> None:
    await seed_payment(1, status=PaymentStatus.IN_PROGRESS, claimed_by_staff_id=84)

    response = await client.post("/api/payments/1/claim")

    assert response.status_code == 409
    assert response.json()["detail"] == "This payment has already been claimed."


@pytest.mark.asyncio
async def test_mark_payment_done(client: AsyncClient) -> None:
    await seed_payment(1, status=PaymentStatus.IN_PROGRESS, claimed_by_staff_id=42)

    response = await client.post("/api/payments/1/done")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["completed_by_staff_id"] == 42
    assert body["completed_at"] is not None


@pytest.mark.asyncio
async def test_claiming_payment_does_not_change_relative_order(
    client: AsyncClient,
) -> None:
    await seed_payment(1, received_at=datetime(2026, 6, 28, tzinfo=UTC))
    await seed_payment(2, received_at=datetime(2026, 6, 30, tzinfo=UTC))

    before_claim = await client.get("/api/payments")
    claim_response = await client.post("/api/payments/2/claim")
    after_claim = await client.get("/api/payments")

    assert claim_response.status_code == 200
    assert [item["id"] for item in before_claim.json()["items"]] == [2, 1]
    assert [item["id"] for item in after_claim.json()["items"]] == [2, 1]


@pytest.mark.asyncio
async def test_marking_done_does_not_change_admin_relative_order(
    client: AsyncClient,
) -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)

    async def override_staff_user() -> User:
        return User(
            id=42,
            username="ledger_staff",
            password_hash="not-used",
            role=UserRole.STAFF,
            is_active=True,
            created_at=timestamp,
            updated_at=timestamp,
        )

    async def override_admin_user() -> User:
        return User(
            id=1,
            username="ledger_admin",
            password_hash="not-used",
            role=UserRole.ADMIN,
            is_active=True,
            created_at=timestamp,
            updated_at=timestamp,
        )

    await seed_payment(1, received_at=datetime(2026, 6, 28, tzinfo=UTC))
    await seed_payment(2, received_at=datetime(2026, 6, 30, tzinfo=UTC))
    claim_response = await client.post("/api/payments/2/claim")

    app.dependency_overrides[get_current_user] = override_admin_user
    before_done = await client.get("/api/payments")
    app.dependency_overrides[get_current_user] = override_staff_user
    done_response = await client.post("/api/payments/2/done")
    app.dependency_overrides[get_current_user] = override_admin_user
    after_done = await client.get("/api/payments")

    assert claim_response.status_code == 200
    assert done_response.status_code == 200
    assert [item["id"] for item in before_done.json()["items"]] == [2, 1]
    assert [item["id"] for item in after_done.json()["items"]] == [2, 1]


@pytest.mark.asyncio
async def test_unclaim_payment(client: AsyncClient) -> None:
    await seed_payment(1, status=PaymentStatus.IN_PROGRESS, claimed_by_staff_id=42)

    response = await client.post("/api/payments/1/unclaim")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["claimed_by_staff_id"] is None
    assert body["claimed_at"] is None


@pytest.mark.asyncio
async def test_staff_only_sees_pending_and_own_claims(client: AsyncClient) -> None:
    await seed_payment(
        1,
        status=PaymentStatus.PENDING,
        received_at=datetime(2026, 6, 28, tzinfo=UTC),
    )
    await seed_payment(
        2,
        status=PaymentStatus.IN_PROGRESS,
        received_at=datetime(2026, 6, 30, tzinfo=UTC),
        claimed_by_staff_id=42,
    )
    await seed_payment(
        3,
        status=PaymentStatus.IN_PROGRESS,
        received_at=datetime(2026, 7, 1, tzinfo=UTC),
        claimed_by_staff_id=84,
    )
    await seed_payment(
        4,
        status=PaymentStatus.DONE,
        received_at=datetime(2026, 7, 2, tzinfo=UTC),
    )

    response = await client.get("/api/payments")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [2, 1]


@pytest.mark.asyncio
async def test_not_ours_hides_payment_for_same_coadmin_only() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(10, username="coadmin_one", role=UserRole.COADMIN, color="#111111")
    await seed_account(11, username="coadmin_two", role=UserRole.COADMIN, color="#222222")
    await seed_account(
        42,
        username="staff_one",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_account(
        43,
        username="staff_two",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_account(
        84,
        username="other_staff",
        role=UserRole.STAFF,
        color="#16A34A",
        coadmin_id=11,
    )
    await seed_payment(1)

    staff_one = User(
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
    staff_two = User(
        id=43,
        username="staff_two",
        password_hash="not-used",
        role=UserRole.STAFF,
        is_active=True,
        staff_color="#2563EB",
        coadmin_id=10,
        created_at=timestamp,
        updated_at=timestamp,
    )
    other_staff = User(
        id=84,
        username="other_staff",
        password_hash="not-used",
        role=UserRole.STAFF,
        is_active=True,
        staff_color="#16A34A",
        coadmin_id=11,
        created_at=timestamp,
        updated_at=timestamp,
    )

    async with payment_client_for(staff_one) as api:
        dismiss_response = await api.post("/api/payments/1/not-ours")
        after_dismiss = await api.get("/api/payments")
    async with payment_client_for(staff_two) as api:
        same_team_response = await api.get("/api/payments")
    async with payment_client_for(other_staff) as api:
        other_team_response = await api.get("/api/payments")

    assert dismiss_response.status_code == 204
    assert [item["id"] for item in after_dismiss.json()["items"]] == []
    assert [item["id"] for item in same_team_response.json()["items"]] == []
    assert [item["id"] for item in other_team_response.json()["items"]] == [1]


@pytest.mark.asyncio
async def test_claim_still_works_globally_after_other_coadmin_dismisses() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(10, username="coadmin_one", role=UserRole.COADMIN, color="#111111")
    await seed_account(11, username="coadmin_two", role=UserRole.COADMIN, color="#222222")
    await seed_account(
        42,
        username="staff_one",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_account(
        84,
        username="other_staff",
        role=UserRole.STAFF,
        color="#16A34A",
        coadmin_id=11,
    )
    await seed_payment(1)

    staff_one = User(
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
    other_staff = User(
        id=84,
        username="other_staff",
        password_hash="not-used",
        role=UserRole.STAFF,
        is_active=True,
        staff_color="#16A34A",
        coadmin_id=11,
        created_at=timestamp,
        updated_at=timestamp,
    )

    async with payment_client_for(staff_one) as api:
        assert (await api.post("/api/payments/1/not-ours")).status_code == 204
    async with payment_client_for(other_staff) as api:
        claim_response = await api.post("/api/payments/1/claim")
    async with payment_client_for(staff_one) as api:
        same_team_after_claim = await api.get("/api/payments")

    assert claim_response.status_code == 200
    assert claim_response.json()["status"] == "in_progress"
    assert claim_response.json()["claimed_by_staff_id"] == 84
    assert [item["id"] for item in same_team_after_claim.json()["items"]] == []


@pytest.mark.asyncio
async def test_staff_history_shows_claimed_and_completed_payments(
    client: AsyncClient,
) -> None:
    await seed_payment(
        1,
        status=PaymentStatus.PENDING,
        received_at=datetime(2026, 6, 28, tzinfo=UTC),
    )
    await seed_payment(
        2,
        status=PaymentStatus.IN_PROGRESS,
        received_at=datetime(2026, 6, 29, tzinfo=UTC),
        claimed_by_staff_id=42,
    )
    await seed_payment(
        3,
        status=PaymentStatus.DONE,
        received_at=datetime(2026, 6, 30, tzinfo=UTC),
        claimed_by_staff_id=42,
        completed_by_staff_id=42,
    )

    response = await client.get("/api/payments/my-history")

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 20
    assert [item["id"] for item in body["items"]] == [3, 2]
    assert body["items"][0]["completed_by_staff_id"] == 42


@pytest.mark.asyncio
async def test_staff_history_excludes_other_staff_payments(
    client: AsyncClient,
) -> None:
    await seed_payment(
        1,
        status=PaymentStatus.IN_PROGRESS,
        claimed_by_staff_id=84,
    )
    await seed_payment(
        2,
        status=PaymentStatus.DONE,
        claimed_by_staff_id=84,
        completed_by_staff_id=84,
    )
    await seed_payment(
        3,
        status=PaymentStatus.DONE,
        claimed_by_staff_id=42,
        completed_by_staff_id=42,
    )

    response = await client.get("/api/payments/my-history")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [3]


@pytest.mark.asyncio
async def test_admin_payment_permissions_unaffected(
    admin_client: AsyncClient,
) -> None:
    await seed_payment(1, status=PaymentStatus.DONE)

    list_response = await admin_client.get("/api/payments")
    history_response = await admin_client.get("/api/payments/my-history")

    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["items"]] == [1]
    assert history_response.status_code == 403


@pytest.mark.asyncio
async def test_active_payment_list_excludes_done_payments(
    admin_client: AsyncClient,
) -> None:
    await seed_payment(1, status=PaymentStatus.PENDING)
    await seed_payment(2, status=PaymentStatus.IN_PROGRESS, claimed_by_staff_id=42)
    await seed_payment(3, status=PaymentStatus.DONE, completed_by_staff_id=42)

    response = await admin_client.get(
        "/api/payments",
        params={"active_only": "true"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [2, 1]


@pytest.mark.asyncio
async def test_admin_history_shows_all_claimed_and_completed_payments(
    admin_client: AsyncClient,
) -> None:
    await seed_account(
        42,
        username="first_staff",
        role=UserRole.STAFF,
        color="#2563EB",
    )
    await seed_account(
        84,
        username="second_staff",
        role=UserRole.STAFF,
        color="#DC2626",
    )
    await seed_payment(1, status=PaymentStatus.PENDING)
    await seed_payment(
        2,
        status=PaymentStatus.IN_PROGRESS,
        claimed_by_staff_id=42,
    )
    await seed_payment(
        3,
        status=PaymentStatus.DONE,
        claimed_by_staff_id=42,
        completed_by_staff_id=84,
    )

    response = await admin_client.get("/api/payments/history")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [3, 2]
    assert body["items"][0]["status"] == "done"
    assert body["items"][0]["claimed_by_staff"]["username"] == "first_staff"
    assert body["items"][0]["completed_by_staff"]["username"] == "second_staff"
    assert body["items"][0]["claimed_at"] is not None
    assert body["items"][0]["completed_at"] is not None


@pytest.mark.asyncio
async def test_staff_cannot_view_admin_payment_history(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/payments/history")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_staff_history_pagination(client: AsyncClient) -> None:
    for payment_id in range(1, 23):
        await seed_payment(
            payment_id,
            status=PaymentStatus.DONE,
            claimed_by_staff_id=42,
            completed_by_staff_id=42,
        )

    first_response = await client.get("/api/payments/my-history")
    second_response = await client.get(
        "/api/payments/my-history",
        params={"limit": 20, "offset": 20},
    )

    assert first_response.status_code == 200
    assert [item["id"] for item in first_response.json()["items"]] == list(
        range(22, 2, -1)
    )
    assert first_response.json()["has_more"] is True
    assert [item["id"] for item in second_response.json()["items"]] == [2, 1]
    assert second_response.json()["has_more"] is False


@pytest.mark.asyncio
async def test_payment_history_orders_by_completed_at_newest_first(
    admin_client: AsyncClient,
) -> None:
    await seed_account(42, username="first_staff", role=UserRole.STAFF, color="#2563EB")
    await seed_payment(
        1,
        status=PaymentStatus.DONE,
        claimed_by_staff_id=42,
        completed_by_staff_id=42,
        completed_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
        payment_datetime=datetime(2025, 1, 1, 12, 0),
        received_at=datetime(2025, 1, 1, 12, tzinfo=UTC),
    )
    await seed_payment(
        2,
        status=PaymentStatus.DONE,
        claimed_by_staff_id=42,
        completed_by_staff_id=42,
        completed_at=datetime(2026, 7, 1, 12, tzinfo=UTC),
        payment_datetime=datetime(2024, 1, 1, 12, 0),
        received_at=datetime(2024, 1, 1, 12, tzinfo=UTC),
    )

    response = await admin_client.get("/api/payments/history")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [2, 1]


@pytest.mark.asyncio
async def test_payment_history_reopen_and_complete_moves_payment_to_top(
    admin_client: AsyncClient,
) -> None:
    await seed_account(
        1,
        username="ledger_admin",
        role=UserRole.ADMIN,
        color="#7C3AED",
    )
    await seed_payment(
        1,
        status=PaymentStatus.DONE,
        claimed_by_staff_id=42,
        completed_by_staff_id=42,
        completed_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )
    await seed_payment(
        2,
        status=PaymentStatus.DONE,
        claimed_by_staff_id=42,
        completed_by_staff_id=42,
        completed_at=datetime(2026, 6, 1, 12, tzinfo=UTC),
    )

    initial_history = await admin_client.get("/api/payments/history")
    assert [item["id"] for item in initial_history.json()["items"]] == [2, 1]

    reopen_response = await admin_client.post("/api/payments/admin/1/reopen")
    assert reopen_response.status_code == 200

    staff_user = User(
        id=42,
        username="ledger_staff",
        password_hash="not-used",
        role=UserRole.STAFF,
        is_active=True,
        staff_color="#2563EB",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    async with TestSessionFactory() as session:
        service = PaymentService(session)
        await service.claim(1, staff_user)
        completed = await service.mark_done(1, staff_user)

    assert completed.completed_at is not None

    final_history = await admin_client.get("/api/payments/history")
    assert [item["id"] for item in final_history.json()["items"]] == [1, 2]


@pytest.mark.asyncio
async def test_staff_cannot_complete_or_unclaim_another_staff_payment(
    client: AsyncClient,
) -> None:
    await seed_payment(
        1,
        status=PaymentStatus.IN_PROGRESS,
        claimed_by_staff_id=84,
    )

    done_response = await client.post("/api/payments/1/done")
    unclaim_response = await client.post("/api/payments/1/unclaim")

    assert done_response.status_code == 409
    assert unclaim_response.status_code == 409


@pytest.mark.asyncio
async def test_staff_cannot_complete_unclaimed_payment(client: AsyncClient) -> None:
    await seed_payment(1, status=PaymentStatus.PENDING)

    response = await client.post("/api/payments/1/done")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_admin_can_assign_force_unclaim_and_see_audit(
    admin_client: AsyncClient,
) -> None:
    await seed_account(
        1,
        username="ledger_admin",
        role=UserRole.ADMIN,
        color="#7C3AED",
    )
    await seed_account(
        84,
        username="sarah",
        role=UserRole.STAFF,
        color="#EA580C",
    )
    await seed_payment(1)

    assigned = await admin_client.post(
        "/api/payments/admin/1/assign",
        json={"staff_id": 84},
    )
    listed = await admin_client.get("/api/payments")
    unclaimed = await admin_client.post("/api/payments/admin/1/force-unclaim")
    audit = await admin_client.get("/api/payments/admin/1/audit")

    assert assigned.status_code == 200
    assert assigned.json()["claimed_by_staff_id"] == 84
    assert listed.json()["items"][0]["claimed_by_staff"] == {
        "id": 84,
        "username": "sarah",
        "color": "#EA580C",
    }
    assert unclaimed.status_code == 200
    assert unclaimed.json()["status"] == "pending"
    assert [entry["action"] for entry in audit.json()] == [
        "reassigned",
        "unclaimed",
    ]


@pytest.mark.asyncio
async def test_admin_can_reopen_done_payment(
    admin_client: AsyncClient,
) -> None:
    await seed_account(
        1,
        username="ledger_admin",
        role=UserRole.ADMIN,
        color="#7C3AED",
    )
    await seed_account(
        84,
        username="sarah",
        role=UserRole.STAFF,
        color="#EA580C",
    )
    await seed_payment(
        1,
        status=PaymentStatus.DONE,
        claimed_by_staff_id=84,
        completed_by_staff_id=84,
    )

    response = await admin_client.post("/api/payments/admin/1/reopen")
    audit = await admin_client.get("/api/payments/admin/1/audit")
    history = await admin_client.get("/api/payments/history")
    active = await admin_client.get(
        "/api/payments",
        params={"active_only": "true"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["claimed_by_staff_id"] is None
    assert response.json()["claimed_at"] is None
    assert response.json()["completed_by_staff_id"] is None
    assert response.json()["completed_at"] is None
    assert history.json()["items"] == []
    assert [item["id"] for item in active.json()["items"]] == [1]
    assert active.json()["items"][0]["status"] == "pending"
    assert audit.json()[-1] == {
        "id": audit.json()[-1]["id"],
        "payment_event_id": 1,
        "actor_user_id": 1,
        "actor_username": "ledger_admin",
        "subject_staff_id": 84,
        "subject_username": "sarah",
        "action": "reopened",
        "from_status": "done",
        "to_status": "pending",
        "created_at": audit.json()[-1]["created_at"],
    }


@pytest.mark.asyncio
async def test_staff_cannot_reopen_done_payment(client: AsyncClient) -> None:
    await seed_payment(
        1,
        status=PaymentStatus.DONE,
        claimed_by_staff_id=42,
        completed_by_staff_id=42,
    )

    response = await client.post("/api/payments/admin/1/reopen")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_search_and_filter_payments(client: AsyncClient) -> None:
    await seed_payment(
        1,
        status=PaymentStatus.PENDING,
        recipient_tag="Stephen_Mckinney_21",
        payment_datetime=datetime(2026, 6, 29, 15, 8),
    )
    await seed_payment(
        2,
        status=PaymentStatus.DONE,
        recipient_tag="Stephen_Old",
        payment_datetime=datetime(2026, 6, 29, 12, 0),
    )
    await seed_payment(
        3,
        status=PaymentStatus.PENDING,
        recipient_tag="Another_Recipient",
        payment_datetime=datetime(2026, 7, 1, 12, 0),
    )

    response = await client.get(
        "/api/payments",
        params={
            "status": "pending",
            "search": "Stephen",
            "date_from": "2026-06-29",
            "date_to": "2026-06-29",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [payment["id"] for payment in body["items"]] == [1]
    assert body["total"] is None


@pytest.mark.asyncio
async def test_list_payments_returns_total_only_when_requested(
    client: AsyncClient,
) -> None:
    for payment_id in range(1, 11):
        await seed_payment(payment_id)

    response = await client.get(
        "/api/payments",
        params={"include_total": "true"},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 10
    assert response.json()["has_more"] is True


@pytest.mark.asyncio
async def test_all_coadmins_declined_moves_payment_to_admin_review() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(10, username="coadmin_one", role=UserRole.COADMIN, color="#111111")
    await seed_account(11, username="coadmin_two", role=UserRole.COADMIN, color="#222222")
    await seed_account(
        42,
        username="staff_one",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_account(
        84,
        username="other_staff",
        role=UserRole.STAFF,
        color="#16A34A",
        coadmin_id=11,
    )
    await seed_payment(1)

    staff_one = User(
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
    other_staff = User(
        id=84,
        username="other_staff",
        password_hash="not-used",
        role=UserRole.STAFF,
        is_active=True,
        staff_color="#16A34A",
        coadmin_id=11,
        created_at=timestamp,
        updated_at=timestamp,
    )

    async with payment_client_for(staff_one) as api:
        assert (await api.post("/api/payments/1/not-ours")).status_code == 204
        after_first = await api.get("/api/payments")
    async with payment_client_for(other_staff) as api:
        assert (await api.post("/api/payments/1/not-ours")).status_code == 204
        after_second = await api.get("/api/payments")

    async with payment_client_for(staff_one) as api:
        staff_one_list = await api.get("/api/payments")
    async with payment_client_for(other_staff) as api:
        staff_two_list = await api.get("/api/payments")

    assert [item["id"] for item in after_first.json()["items"]] == []
    assert [item["id"] for item in after_second.json()["items"]] == []
    assert staff_one_list.json()["items"] == []
    assert staff_two_list.json()["items"] == []


@pytest.mark.asyncio
async def test_admin_declined_review_lists_coadmin_dismissals() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(1, username="ledger_admin", role=UserRole.ADMIN, color="#7C3AED")
    await seed_account(10, username="coadmin_one", role=UserRole.COADMIN, color="#111111")
    await seed_account(11, username="coadmin_two", role=UserRole.COADMIN, color="#222222")
    await seed_account(
        42,
        username="staff_one",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_account(
        84,
        username="other_staff",
        role=UserRole.STAFF,
        color="#16A34A",
        coadmin_id=11,
    )
    await seed_payment(1)

    admin_user = User(
        id=1,
        username="ledger_admin",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        staff_color="#7C3AED",
        created_at=timestamp,
        updated_at=timestamp,
    )
    staff_one = User(
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
    other_staff = User(
        id=84,
        username="other_staff",
        password_hash="not-used",
        role=UserRole.STAFF,
        is_active=True,
        staff_color="#16A34A",
        coadmin_id=11,
        created_at=timestamp,
        updated_at=timestamp,
    )

    async with payment_client_for(staff_one) as api:
        await api.post("/api/payments/1/not-ours")
    async with payment_client_for(other_staff) as api:
        await api.post("/api/payments/1/not-ours")

    async with payment_client_for(admin_user) as api:
        normal_list = await api.get("/api/payments")
        declined_list = await api.get("/api/payments/admin/declined")

    assert normal_list.status_code == 200
    normal_body = normal_list.json()
    assert [item["id"] for item in normal_body["items"]] == [1]
    assert normal_body["items"][0]["can_dismiss"] is True
    assert normal_body["items"][0]["eligible_coadmin_count"] == 2
    assert normal_body["items"][0]["declined_coadmin_count"] == 2
    assert declined_list.status_code == 200
    body = declined_list.json()
    assert [item["id"] for item in body["items"]] == [1]
    assert body["items"][0]["all_coadmins_declined_at"] is not None
    assert len(body["items"][0]["coadmin_dismissals"]) == 2
    usernames = {
        dismissal["coadmin_username"]
        for dismissal in body["items"][0]["coadmin_dismissals"]
    }
    assert usernames == {"coadmin_one", "coadmin_two"}


@pytest.mark.asyncio
async def test_admin_can_dismiss_and_delete_declined_payment() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(1, username="ledger_admin", role=UserRole.ADMIN, color="#7C3AED")
    await seed_account(10, username="coadmin_one", role=UserRole.COADMIN, color="#111111")
    await seed_account(
        42,
        username="staff_one",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_payment(1)

    admin_user = User(
        id=1,
        username="ledger_admin",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        staff_color="#7C3AED",
        created_at=timestamp,
        updated_at=timestamp,
    )
    staff_one = User(
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

    async with payment_client_for(staff_one) as api:
        await api.post("/api/payments/1/not-ours")

    async with payment_client_for(admin_user) as api:
        dismiss_response = await api.post("/api/payments/admin/1/dismiss-declined")
        after_dismiss = await api.get("/api/payments/admin/declined")

    assert dismiss_response.status_code == 204
    assert after_dismiss.json()["items"] == []

    await seed_payment(2)
    async with payment_client_for(staff_one) as api:
        await api.post("/api/payments/2/not-ours")

    async with payment_client_for(admin_user) as api:
        delete_response = await api.delete("/api/payments/admin/2")
        after_delete = await api.get("/api/payments/admin/declined")

    assert delete_response.status_code == 204
    assert [item["id"] for item in after_delete.json()["items"]] == []


@pytest.mark.asyncio
async def test_partial_coadmin_decline_does_not_trigger_all_declined() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(1, username="ledger_admin", role=UserRole.ADMIN, color="#7C3AED")
    await seed_account(10, username="coadmin_one", role=UserRole.COADMIN, color="#111111")
    await seed_account(11, username="coadmin_two", role=UserRole.COADMIN, color="#222222")
    await seed_account(
        42,
        username="staff_one",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_account(
        84,
        username="other_staff",
        role=UserRole.STAFF,
        color="#16A34A",
        coadmin_id=11,
    )
    await seed_payment(1)

    admin_user = User(
        id=1,
        username="ledger_admin",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        staff_color="#7C3AED",
        created_at=timestamp,
        updated_at=timestamp,
    )
    staff_one = User(
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

    async with payment_client_for(staff_one) as api:
        await api.post("/api/payments/1/not-ours")

    async with payment_client_for(admin_user) as api:
        declined_list = await api.get("/api/payments/admin/declined")
        normal_list = await api.get("/api/payments")

    assert declined_list.json()["items"] == []
    normal_body = normal_list.json()
    assert [item["id"] for item in normal_body["items"]] == [1]
    assert normal_body["items"][0]["all_coadmins_declined_at"] is None
    assert normal_body["items"][0]["can_dismiss"] is False
    assert normal_body["items"][0]["declined_coadmin_count"] == 1
    assert normal_body["items"][0]["eligible_coadmin_count"] == 2


@pytest.mark.asyncio
async def test_zero_declines_has_no_dismiss_button_for_admin() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(1, username="ledger_admin", role=UserRole.ADMIN, color="#7C3AED")
    await seed_account(10, username="coadmin_one", role=UserRole.COADMIN, color="#111111")
    await seed_account(
        42,
        username="staff_one",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_payment(1)

    admin_user = User(
        id=1,
        username="ledger_admin",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        staff_color="#7C3AED",
        created_at=timestamp,
        updated_at=timestamp,
    )

    async with payment_client_for(admin_user) as api:
        response = await api.get("/api/payments?active_only=true")

    body = response.json()["items"][0]
    assert body["can_dismiss"] is False
    assert body["declined_coadmin_count"] == 0


@pytest.mark.asyncio
async def test_empty_active_coadmin_does_not_block_all_declined() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(1, username="ledger_admin", role=UserRole.ADMIN, color="#7C3AED")
    await seed_account(10, username="charlie", role=UserRole.COADMIN, color="#111111")
    await seed_account(11, username="default_coadmin", role=UserRole.COADMIN, color="#222222")
    await seed_account(
        42,
        username="bella",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_payment(1)

    admin_user = User(
        id=1,
        username="ledger_admin",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        staff_color="#7C3AED",
        created_at=timestamp,
        updated_at=timestamp,
    )
    staff_user = User(
        id=42,
        username="bella",
        password_hash="not-used",
        role=UserRole.STAFF,
        is_active=True,
        staff_color="#2563EB",
        coadmin_id=10,
        created_at=timestamp,
        updated_at=timestamp,
    )

    async with payment_client_for(staff_user) as api:
        assert (await api.post("/api/payments/1/not-ours")).status_code == 204

    async with payment_client_for(admin_user) as api:
        response = await api.get("/api/payments?active_only=true")

    body = response.json()["items"][0]
    assert body["status"] == "pending"
    assert body["can_dismiss"] is True
    assert body["eligible_coadmin_count"] == 1
    assert body["declined_coadmin_count"] == 1
    assert body["all_coadmins_declined_at"] is not None


@pytest.mark.asyncio
async def test_same_coadmin_decline_is_counted_once() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(10, username="coadmin_one", role=UserRole.COADMIN, color="#111111")
    await seed_account(
        42,
        username="staff_one",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_account(
        43,
        username="staff_two",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_payment(1)

    staff_one = User(
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
    staff_two = User(
        id=43,
        username="staff_two",
        password_hash="not-used",
        role=UserRole.STAFF,
        is_active=True,
        staff_color="#2563EB",
        coadmin_id=10,
        created_at=timestamp,
        updated_at=timestamp,
    )

    async with payment_client_for(staff_one) as api:
        await api.post("/api/payments/1/not-ours")
    async with payment_client_for(staff_two) as api:
        await api.post("/api/payments/1/not-ours")

    async with TestSessionFactory() as session:
        service = PaymentService(session)
        payment = await service._repository.get_by_id(1)
        assert payment is not None
        eligibility = await service._compute_dismissal_eligibility(payment)

    assert eligibility.declined_coadmin_count == 1
    assert eligibility.can_dismiss is True


@pytest.mark.asyncio
async def test_disabled_coadmin_is_not_required_for_dismiss() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(1, username="ledger_admin", role=UserRole.ADMIN, color="#7C3AED")
    await seed_account(10, username="coadmin_one", role=UserRole.COADMIN, color="#111111")
    await seed_account(11, username="coadmin_two", role=UserRole.COADMIN, color="#222222")
    await seed_account(
        42,
        username="staff_one",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_account(
        84,
        username="other_staff",
        role=UserRole.STAFF,
        color="#16A34A",
        coadmin_id=11,
    )
    await seed_payment(1)

    async with TestSessionFactory() as session:
        disabled = await session.get(User, 11)
        assert disabled is not None
        disabled.is_active = False
        await session.commit()

    admin_user = User(
        id=1,
        username="ledger_admin",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        staff_color="#7C3AED",
        created_at=timestamp,
        updated_at=timestamp,
    )
    staff_one = User(
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

    async with payment_client_for(staff_one) as api:
        await api.post("/api/payments/1/not-ours")

    async with payment_client_for(admin_user) as api:
        response = await api.get("/api/payments?active_only=true")

    body = response.json()["items"][0]
    assert body["eligible_coadmin_count"] == 1
    assert body["can_dismiss"] is True


@pytest.mark.asyncio
async def test_new_active_coadmin_requires_decline_before_dismiss() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(1, username="ledger_admin", role=UserRole.ADMIN, color="#7C3AED")
    await seed_account(10, username="coadmin_one", role=UserRole.COADMIN, color="#111111")
    await seed_account(
        42,
        username="staff_one",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_payment(1)

    admin_user = User(
        id=1,
        username="ledger_admin",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        staff_color="#7C3AED",
        created_at=timestamp,
        updated_at=timestamp,
    )
    staff_one = User(
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

    async with payment_client_for(staff_one) as api:
        await api.post("/api/payments/1/not-ours")

    await seed_account(11, username="coadmin_two", role=UserRole.COADMIN, color="#222222")
    await seed_account(
        84,
        username="other_staff",
        role=UserRole.STAFF,
        color="#16A34A",
        coadmin_id=11,
    )

    async with payment_client_for(admin_user) as api:
        response = await api.get("/api/payments?active_only=true")

    body = response.json()["items"][0]
    assert body["eligible_coadmin_count"] == 2
    assert body["declined_coadmin_count"] == 1
    assert body["can_dismiss"] is False


@pytest.mark.asyncio
async def test_admin_dismiss_removes_fully_declined_payment_from_active_list() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    await seed_account(1, username="ledger_admin", role=UserRole.ADMIN, color="#7C3AED")
    await seed_account(10, username="coadmin_one", role=UserRole.COADMIN, color="#111111")
    await seed_account(
        42,
        username="staff_one",
        role=UserRole.STAFF,
        color="#2563EB",
        coadmin_id=10,
    )
    await seed_payment(1)

    admin_user = User(
        id=1,
        username="ledger_admin",
        password_hash="not-used",
        role=UserRole.ADMIN,
        is_active=True,
        staff_color="#7C3AED",
        created_at=timestamp,
        updated_at=timestamp,
    )
    staff_one = User(
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

    async with payment_client_for(staff_one) as api:
        await api.post("/api/payments/1/not-ours")

    async with payment_client_for(admin_user) as api:
        before = await api.get("/api/payments?active_only=true")
        assert before.json()["items"][0]["can_dismiss"] is True
        dismiss_response = await api.post("/api/payments/admin/1/dismiss-declined")
        after = await api.get("/api/payments?active_only=true")

    assert dismiss_response.status_code == 204
    assert after.json()["items"] == []

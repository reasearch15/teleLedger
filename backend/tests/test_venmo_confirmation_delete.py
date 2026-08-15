from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.media_asset import MediaAsset
from app.models.user import User, UserRole
from app.models.venmo_confirmation import (
    VenmoConfirmationAttempt,
    VenmoConfirmationAttemptStatus,
    VenmoConfirmationEvent,
    VenmoConfirmationEventType,
    VenmoConfirmationInquiry,
    VenmoConfirmationRequest,
    VenmoConfirmationStatus,
)
from app.services.venmo_confirmation import VenmoConfirmationService
from app.telegram.venmo_confirmation import (
    VenmoConfirmationCallbackAction,
    encode_venmo_confirmation_callback,
)
from app.telegram.venmo_confirmation_delivery import deliver_next_due_venmo_confirmation

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


def make_user(
    user_id: int,
    username: str,
    role: UserRole,
    *,
    coadmin_id: int | None = None,
) -> User:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    return User(
        id=user_id,
        username=username,
        password_hash="not-used",
        role=role,
        is_active=True,
        staff_color="#2563EB",
        coadmin_id=coadmin_id,
        created_at=timestamp,
        updated_at=timestamp,
    )


STAFF = make_user(42, "sarah", UserRole.STAFF, coadmin_id=10)
COADMIN = make_user(10, "default_coadmin", UserRole.COADMIN)
ADMIN = make_user(1, "admin", UserRole.ADMIN)


@pytest_asyncio.fixture(autouse=True)
async def reset_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[None]:
    monkeypatch.setenv("INQUIRY_MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-1001234567890")
    get_settings.cache_clear()
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSessionFactory() as session:
        session.add_all(
            [
                make_user(42, "sarah", UserRole.STAFF, coadmin_id=10),
                make_user(10, "default_coadmin", UserRole.COADMIN),
                make_user(1, "admin", UserRole.ADMIN),
            ]
        )
        await session.commit()
    yield
    app.dependency_overrides.clear()
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()
    get_settings.cache_clear()


@asynccontextmanager
async def api_client_for(user: User) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    async def override_current_user() -> User:
        return user

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_current_user
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    app.dependency_overrides.clear()


async def seed_pending_request(
    tmp_path: Path,
    *,
    request_id: int = 300,
    with_posted_message: bool = False,
    with_failed_attempt: bool = False,
    with_scheduled_retry: bool = False,
    with_active_lease: bool = False,
    status: VenmoConfirmationStatus = VenmoConfirmationStatus.PENDING,
) -> tuple[int, int]:
    (tmp_path / "evidence").mkdir(exist_ok=True)
    (tmp_path / "evidence" / f"venmo-{request_id}.png").write_bytes(b"fake-image")
    async with TestSessionFactory() as session:
        session.add(
            MediaAsset(
                id=request_id,
                coadmin_id=10,
                storage_key=f"evidence/venmo-{request_id}.png",
                original_filename="venmo.png",
                mime_type="image/png",
                size_bytes=10,
                checksum_sha256="a" * 64,
                created_by_user_id=42,
            )
        )
        session.add(
            VenmoConfirmationRequest(
                id=request_id,
                coadmin_id=10,
                requested_by_staff_id=42,
                screenshot_media_asset_id=request_id,
                status=status,
                payment_note="Pending cleanup",
            )
        )
        attempt_kwargs: dict[str, object] = {
            "id": request_id * 10,
            "request_id": request_id,
            "attempt_number": 1,
        }
        if with_posted_message:
            attempt_kwargs.update(
                {
                    "status": VenmoConfirmationAttemptStatus.POSTED,
                    "telegram_chat_id": -100123,
                    "telegram_message_id": 9000 + request_id,
                }
            )
        elif with_failed_attempt:
            attempt_kwargs.update(
                {
                    "status": VenmoConfirmationAttemptStatus.FAILED_TO_SEND,
                    "last_error": "temporary send failure",
                }
            )
        else:
            attempt_kwargs["status"] = VenmoConfirmationAttemptStatus.PENDING
            if with_scheduled_retry:
                attempt_kwargs["next_retry_at"] = datetime.now(UTC) + timedelta(minutes=5)
            if with_active_lease:
                attempt_kwargs["delivery_lease_until"] = datetime.now(UTC) + timedelta(seconds=60)
        session.add(VenmoConfirmationAttempt(**attempt_kwargs))
        await session.commit()
        return request_id, request_id * 10


@pytest.mark.asyncio
async def test_admin_can_delete_pending_request(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_pending_request(tmp_path)

    async with api_client_for(ADMIN) as client:
        response = await client.delete(f"/api/venmo-confirmations/{request_id}")

    assert response.status_code == 204
    async with TestSessionFactory() as session:
        assert await session.get(VenmoConfirmationRequest, request_id) is None
        assert await session.get(VenmoConfirmationAttempt, attempt_id) is None
        assert await session.get(MediaAsset, request_id) is None


@pytest.mark.asyncio
async def test_staff_cannot_delete_pending_request(tmp_path: Path) -> None:
    request_id, _ = await seed_pending_request(tmp_path)

    async with api_client_for(STAFF) as client:
        response = await client.delete(f"/api/venmo-confirmations/{request_id}")

    assert response.status_code == 403
    async with TestSessionFactory() as session:
        assert await session.get(VenmoConfirmationRequest, request_id) is not None


@pytest.mark.asyncio
async def test_coadmin_cannot_delete_pending_request(tmp_path: Path) -> None:
    request_id, _ = await seed_pending_request(tmp_path)

    async with api_client_for(COADMIN) as client:
        response = await client.delete(f"/api/venmo-confirmations/{request_id}")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_confirmed_request_cannot_be_deleted(tmp_path: Path) -> None:
    request_id, _ = await seed_pending_request(
        tmp_path,
        request_id=301,
        status=VenmoConfirmationStatus.CONFIRMED,
        with_posted_message=True,
    )

    async with api_client_for(ADMIN) as client:
        response = await client.delete(f"/api/venmo-confirmations/{request_id}")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_not_received_request_cannot_be_deleted(tmp_path: Path) -> None:
    request_id, _ = await seed_pending_request(
        tmp_path,
        request_id=302,
        status=VenmoConfirmationStatus.NOT_RECEIVED,
        with_posted_message=True,
    )

    async with api_client_for(ADMIN) as client:
        response = await client.delete(f"/api/venmo-confirmations/{request_id}")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_pending_request_with_failed_attempt_can_be_deleted(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_pending_request(
        tmp_path,
        request_id=303,
        with_failed_attempt=True,
    )

    async with api_client_for(ADMIN) as client:
        response = await client.delete(f"/api/venmo-confirmations/{request_id}")

    assert response.status_code == 204
    async with TestSessionFactory() as session:
        assert await session.get(VenmoConfirmationRequest, request_id) is None
        assert await session.get(VenmoConfirmationAttempt, attempt_id) is None


@pytest.mark.asyncio
async def test_pending_request_with_scheduled_retry_is_not_sent_after_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.telegram import venmo_confirmation_delivery

    monkeypatch.setattr(
        venmo_confirmation_delivery,
        "SessionFactory",
        TestSessionFactory,
    )
    request_id, attempt_id = await seed_pending_request(
        tmp_path,
        request_id=304,
        with_scheduled_retry=True,
    )

    async with TestSessionFactory() as session, session.begin():
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
        assert attempt is not None
        attempt.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.flush()

    async with api_client_for(ADMIN) as client:
        response = await client.delete(f"/api/venmo-confirmations/{request_id}")
    assert response.status_code == 204

    processed = await deliver_next_due_venmo_confirmation(
        gateway_factory=lambda: _NoSendGateway(),
    )
    assert processed is False


class _NoSendGateway:
    sent = 0

    async def __aenter__(self) -> _NoSendGateway:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def send_photo(self, **_: object) -> int:
        _NoSendGateway.sent += 1
        return 9999


@pytest.mark.asyncio
async def test_active_delivery_lease_blocks_delete(tmp_path: Path) -> None:
    request_id, _ = await seed_pending_request(
        tmp_path,
        request_id=305,
        with_active_lease=True,
    )

    async with api_client_for(ADMIN) as client:
        response = await client.delete(f"/api/venmo-confirmations/{request_id}")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_delete_removes_related_rows_without_orphans(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_pending_request(
        tmp_path,
        request_id=306,
        with_posted_message=True,
    )
    async with TestSessionFactory() as session:
        session.add(
            VenmoConfirmationInquiry(
                id=3060,
                request_id=request_id,
                source_attempt_id=attempt_id,
            )
        )
        session.add(
            VenmoConfirmationEvent(
                id=3061,
                request_id=request_id,
                attempt_id=attempt_id,
                event_type=VenmoConfirmationEventType.ATTEMPT_POSTED,
                actor_user_id=42,
                actor_source="atlas",
                actor_identifier="42",
            )
        )
        await session.commit()

    async with api_client_for(ADMIN) as client:
        response = await client.delete(f"/api/venmo-confirmations/{request_id}")
    assert response.status_code == 204

    async with TestSessionFactory() as session:
        assert await session.scalar(select(func.count()).select_from(VenmoConfirmationAttempt)) == 0
        assert await session.scalar(select(func.count()).select_from(VenmoConfirmationInquiry)) == 0
        assert await session.scalar(select(func.count()).select_from(VenmoConfirmationEvent)) == 0


@pytest.mark.asyncio
async def test_delete_does_not_edit_telegram_messages(tmp_path: Path) -> None:
    request_id, _ = await seed_pending_request(
        tmp_path,
        request_id=307,
        with_posted_message=True,
    )

    async with api_client_for(ADMIN) as client:
        response = await client.delete(f"/api/venmo-confirmations/{request_id}")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_telegram_callback_for_deleted_request_fails_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-100123")
    get_settings.cache_clear()
    answers: list[dict[str, object]] = []

    class CallbackGateway:
        async def __aenter__(self) -> CallbackGateway:
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        async def answer_callback_query(self, **kwargs: object) -> None:
            answers.append(dict(kwargs))

    request_id, attempt_id = await seed_pending_request(
        tmp_path,
        request_id=308,
        with_posted_message=True,
    )

    async with api_client_for(ADMIN) as client:
        assert (await client.delete(f"/api/venmo-confirmations/{request_id}")).status_code == 204

    async with TestSessionFactory() as session, session.begin():
        service = VenmoConfirmationService(session)
        result = await service.handle_telegram_callback(
            query_id="q-deleted",
            callback_data=encode_venmo_confirmation_callback(
                attempt_id,
                VenmoConfirmationCallbackAction.CONFIRM,
            ),
            telegram_chat_id=-100123,
            telegram_user_id=555,
            telegram_username="player",
            message_id=9000 + request_id,
            gateway=CallbackGateway(),
        )

    assert result.status == "not_found"
    assert answers
    assert answers[0]["alert"] is True

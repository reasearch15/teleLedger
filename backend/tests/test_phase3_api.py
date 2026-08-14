from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.media_asset import MediaAsset
from app.models.notification import NotificationType, PersistentNotification
from app.models.user import User, UserRole
from app.models.venmo_confirmation import (
    VenmoConfirmationAttempt,
    VenmoConfirmationAttemptStatus,
    VenmoConfirmationEvent,
    VenmoConfirmationEventType,
    VenmoConfirmationInquiry,
    VenmoConfirmationRequest,
)

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
OTHER_STAFF = make_user(84, "alex", UserRole.STAFF, coadmin_id=11)
COADMIN = make_user(10, "default_coadmin", UserRole.COADMIN)
OTHER_COADMIN = make_user(11, "other_coadmin", UserRole.COADMIN)


@pytest_asyncio.fixture(autouse=True)
async def reset_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[None]:
    monkeypatch.setenv("INQUIRY_MEDIA_DIR", str(tmp_path))
    get_settings.cache_clear()
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSessionFactory() as session:
        session.add_all(
            [
                make_user(42, "sarah", UserRole.STAFF, coadmin_id=10),
                make_user(84, "alex", UserRole.STAFF, coadmin_id=11),
                make_user(10, "default_coadmin", UserRole.COADMIN),
                make_user(11, "other_coadmin", UserRole.COADMIN),
            ]
        )
        await session.commit()
    yield
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


async def seed_notification() -> None:
    async with TestSessionFactory() as session:
        session.add_all(
            [
                PersistentNotification(
                    id=1,
                    recipient_user_id=42,
                    coadmin_id=10,
                    type=NotificationType.VENMO_CONFIRMATION_CONFIRMED,
                    related_entity_type="venmo_confirmation_request",
                    related_entity_id=100,
                    title="Venmo payment confirmed",
                    body="Payment was confirmed.",
                    payload={"request_id": 100},
                ),
                PersistentNotification(
                    id=2,
                    recipient_user_id=84,
                    coadmin_id=11,
                    type=NotificationType.VENMO_CONFIRMATION_CONFIRMED,
                    related_entity_type="venmo_confirmation_request",
                    related_entity_id=200,
                    title="Other notification",
                    body=None,
                    payload={"request_id": 200},
                ),
            ]
        )
        await session.commit()


async def seed_venmo(media_root: Path) -> None:
    (media_root / "evidence").mkdir()
    (media_root / "evidence" / "venmo.png").write_bytes(b"fake-image")
    async with TestSessionFactory() as session:
        session.add_all(
            [
                MediaAsset(
                    id=1,
                    coadmin_id=10,
                    storage_key="evidence/venmo.png",
                    original_filename="venmo.png",
                    mime_type="image/png",
                    size_bytes=10,
                    checksum_sha256="a" * 64,
                    created_by_user_id=42,
                ),
                MediaAsset(
                    id=2,
                    coadmin_id=11,
                    storage_key="other.png",
                    original_filename="other.png",
                    mime_type="image/png",
                    size_bytes=10,
                    checksum_sha256="b" * 64,
                    created_by_user_id=84,
                ),
            ]
        )
        session.add(
            VenmoConfirmationRequest(
                id=100,
                coadmin_id=10,
                requested_by_staff_id=42,
                screenshot_media_asset_id=1,
                payment_note="Player paid",
                metadata_json={"player": "ABC123"},
            )
        )
        session.add(
            VenmoConfirmationRequest(
                id=200,
                coadmin_id=11,
                requested_by_staff_id=84,
                screenshot_media_asset_id=2,
            )
        )
        session.add(
            VenmoConfirmationAttempt(
                id=501,
                request_id=100,
                attempt_number=1,
                status=VenmoConfirmationAttemptStatus.POSTED,
                telegram_chat_id=-100123,
                telegram_message_id=777,
            )
        )
        session.add(
            VenmoConfirmationInquiry(
                id=601,
                request_id=100,
                source_attempt_id=501,
            )
        )
        session.add(
            VenmoConfirmationEvent(
                id=701,
                request_id=100,
                attempt_id=501,
                event_type=VenmoConfirmationEventType.ATTEMPT_POSTED,
                actor_user_id=42,
                actor_source="atlas",
                actor_identifier="42",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_notification_list_count_and_read_are_scoped() -> None:
    await seed_notification()

    async with api_client_for(STAFF) as client:
        response = await client.get("/api/notifications")
        assert response.status_code == 200
        body = response.json()
        assert body["unread_count"] == 1
        assert [item["id"] for item in body["items"]] == [1]
        assert body["items"][0]["navigation_href"] == "/venmo-confirmations/100"

        read = await client.post("/api/notifications/1/read")
        assert read.status_code == 200
        count = await client.get("/api/notifications/unread-count")
        assert count.json()["unread_count"] == 0

        forbidden = await client.post("/api/notifications/2/read")
        assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_venmo_detail_returns_history_and_media(tmp_path: Path) -> None:
    await seed_venmo(tmp_path)

    async with api_client_for(STAFF) as client:
        response = await client.get("/api/venmo-confirmations/100")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == 100
        assert body["requested_by_username"] == "sarah"
        assert body["coadmin_username"] == "default_coadmin"
        assert body["media"]["preview_url"] == "/api/venmo-confirmations/media/1"
        assert len(body["attempts"]) == 1
        assert len(body["inquiries"]) == 1
        assert body["events"][0]["actor_username"] == "sarah"


@pytest.mark.asyncio
async def test_venmo_and_media_access_are_coadmin_scoped(tmp_path: Path) -> None:
    await seed_venmo(tmp_path)

    async with api_client_for(OTHER_STAFF) as client:
        response = await client.get("/api/venmo-confirmations/100")
        assert response.status_code == 404
        media = await client.get("/api/venmo-confirmations/media/1")
        assert media.status_code == 404

    async with api_client_for(STAFF) as client:
        media = await client.get("/api/venmo-confirmations/media/1")
        assert media.status_code == 200
        assert media.content == b"fake-image"


@pytest.mark.asyncio
async def test_duplicate_venmo_confirm_action_conflicts_without_duplicate_event(
    tmp_path: Path,
) -> None:
    await seed_venmo(tmp_path)

    async with api_client_for(STAFF) as client:
        first = await client.post("/api/venmo-confirmations/attempts/501/confirm")
        assert first.status_code == 200
        second = await client.post("/api/venmo-confirmations/attempts/501/confirm")
        assert second.status_code == 409

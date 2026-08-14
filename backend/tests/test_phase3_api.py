from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
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
    VenmoConfirmationStatus,
)
from app.telegram import venmo_confirmation_reconciliation
from app.telegram.cashout_bot.api import (
    TelegramBotApiError,
    TelegramBotApiGateway,
    TelegramBotFailureClass,
    TelegramBotUpdate,
)
from app.telegram.cashout_bot.updates import handle_cashout_bot_update
from app.telegram.venmo_confirmation import (
    VenmoConfirmationCallbackAction,
    encode_venmo_confirmation_callback,
)
from app.telegram.venmo_confirmation_reconciliation import (
    reconcile_venmo_confirmation_telegram_state,
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
ADMIN = make_user(1, "admin", UserRole.ADMIN)

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


@pytest_asyncio.fixture(autouse=True)
async def reset_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[None]:
    monkeypatch.setenv("INQUIRY_MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(
        venmo_confirmation_reconciliation,
        "SessionFactory",
        TestSessionFactory,
    )
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
                make_user(1, "admin", UserRole.ADMIN),
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


def upload_stayed_inside_media_root(
    media_root: Path,
    storage_key: str,
    filename: str,
) -> bool:
    path = (media_root / storage_key).resolve()
    return (
        path.is_relative_to(media_root.resolve())
        and path.exists()
        and not (media_root.parent / filename).exists()
    )


class FakeConfirmationGateway:
    sent: list[dict[str, object]] = []
    answers: list[dict[str, object]] = []
    edits: list[dict[str, object]] = []
    fail_send = False
    fail_edit = False
    edit_error: Exception | None = None

    async def __aenter__(self) -> FakeConfirmationGateway:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    async def send_photo(
        self,
        *,
        chat_id: int,
        photo_path: Path,
        caption: str,
        buttons: list[list[tuple[str, str]]],
        mime_type: str,
        filename: str | None = None,
    ) -> int | None:
        if self.fail_send:
            raise TelegramBotApiError(
                "temporary send failure",
                failure_class=TelegramBotFailureClass.RETRYABLE,
            )
        self.sent.append(
            {
                "chat_id": chat_id,
                "photo_path": photo_path,
                "photo_exists": await asyncio.to_thread(photo_path.exists),
                "caption": caption,
                "buttons": buttons,
                "mime_type": mime_type,
                "filename": filename,
            }
        )
        return 9000 + len(self.sent)

    async def answer_callback_query(
        self,
        *,
        query_id: str,
        text: str,
        alert: bool = False,
    ) -> None:
        self.answers.append({"query_id": query_id, "text": text, "alert": alert})

    async def edit_message_caption(
        self,
        *,
        chat_id: int,
        message_id: int,
        caption: str,
        buttons: list[list[tuple[str, str]]] | None = None,
    ) -> None:
        if self.edit_error is not None:
            raise self.edit_error
        if self.fail_edit:
            raise RuntimeError("caption edit failed")
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "caption": caption,
                "buttons": buttons,
            }
        )


def reset_fake_gateway() -> None:
    FakeConfirmationGateway.sent = []
    FakeConfirmationGateway.answers = []
    FakeConfirmationGateway.edits = []
    FakeConfirmationGateway.fail_send = False
    FakeConfirmationGateway.fail_edit = False
    FakeConfirmationGateway.edit_error = None


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
async def test_staff_can_create_generic_confirmation_with_image_and_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fake_gateway()
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-1001234567890")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.api.routes.venmo_confirmations.TelegramBotApiGateway",
        FakeConfirmationGateway,
    )

    async with api_client_for(STAFF) as client:
        response = await client.post(
            "/api/venmo-confirmations",
            data={"payment_note": "PayPal ref ABC"},
            files={"file": ("dog.png", PNG_BYTES, "image/png")},
        )
        listed = await client.get("/api/venmo-confirmations")

    assert response.status_code == 201
    body = response.json()
    assert body["payment_note"] == "PayPal ref ABC"
    assert body["media"]["original_filename"] == "dog.png"
    assert body["attempts"][0]["status"] == "posted"
    assert body["attempts"][0]["telegram_message_id"] == 9001
    assert body["events"][0]["event_type"] == "request_created"
    assert "attempt_posted" in [event["event_type"] for event in body["events"]]
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == body["id"]
    assert FakeConfirmationGateway.sent
    sent = FakeConfirmationGateway.sent[0]
    assert sent["mime_type"] == "image/png"
    assert sent["photo_exists"] is True
    assert "Confirmation request #" in str(sent["caption"])
    labels = [label for row in sent["buttons"] for label, _ in row]
    assert labels == ["Confirm", "Not Received"]


@pytest.mark.asyncio
async def test_coadmin_can_create_confirmation_without_payment_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fake_gateway()
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-1001234567890")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.api.routes.venmo_confirmations.TelegramBotApiGateway",
        FakeConfirmationGateway,
    )

    async with api_client_for(COADMIN) as client:
        response = await client.post(
            "/api/venmo-confirmations",
            files={"file": ("arbitrary-test-image.png", PNG_BYTES, "image/png")},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["requested_by_staff_id"] is None
    assert body["payment_note"] is None
    assert body["media"]["original_filename"] == "arbitrary-test-image.png"


@pytest.mark.asyncio
async def test_telegram_failure_leaves_durable_confirmation_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_fake_gateway()
    FakeConfirmationGateway.fail_send = True
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-1001234567890")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.api.routes.venmo_confirmations.TelegramBotApiGateway",
        FakeConfirmationGateway,
    )

    async with api_client_for(STAFF) as client:
        response = await client.post(
            "/api/venmo-confirmations",
            files={"file": ("receipt.png", PNG_BYTES, "image/png")},
        )
        listed = await client.get("/api/venmo-confirmations")

    assert response.status_code == 201
    body = response.json()
    assert body["attempts"][0]["status"] == "failed_to_send"
    assert body["attempts"][0]["last_error"] == "temporary send failure"
    assert body["events"][-1]["event_type"] == "failure"
    assert listed.json()["items"][0]["id"] == body["id"]


@pytest.mark.asyncio
async def test_venmo_telegram_confirm_callback_marks_request_confirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_venmo(tmp_path)
    reset_fake_gateway()
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-100123")
    get_settings.cache_clear()
    gateway = FakeConfirmationGateway()

    update = TelegramBotUpdate(
        update_id=1,
        payload={
            "callback_query": {
                "id": "callback-1",
                "data": encode_venmo_confirmation_callback(
                    501,
                    VenmoConfirmationCallbackAction.CONFIRM,
                ),
                "from": {"id": 700, "username": "receiver"},
                "message": {
                    "message_id": 777,
                    "chat": {"id": -100123, "type": "supergroup"},
                },
            }
        },
    )

    await handle_cashout_bot_update(
        update,
        gateway=gateway,
        session_factory=TestSessionFactory,
        report=lambda _: None,
    )

    async with TestSessionFactory() as session:
        request = await session.get(VenmoConfirmationRequest, 100)
        attempt = await session.get(VenmoConfirmationAttempt, 501)
    assert request is not None
    assert attempt is not None
    assert request.status == VenmoConfirmationStatus.CONFIRMED
    assert attempt.status == VenmoConfirmationAttemptStatus.CONFIRMED
    assert FakeConfirmationGateway.edits[-1]["buttons"] is None
    caption = str(FakeConfirmationGateway.edits[-1]["caption"])
    assert "✅✅ CONFIRMATION COMPLETED ✅✅" in caption
    assert "🟢 CONFIRMED" in caption
    assert "✅ EVIDENCE CONFIRMED" in caption
    assert "Confirmed By: receiver" in caption


@pytest.mark.asyncio
async def test_gateway_edit_caption_removes_confirmation_buttons_with_empty_markup() -> None:
    observed_payload: dict[str, object] | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_payload
        assert request.url.path.endswith("/editMessageCaption")
        observed_payload = json.loads(request.content.decode())
        return httpx.Response(200, json={"ok": True, "result": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = TelegramBotApiGateway(token="123:test-token", client=client)
        await gateway.edit_message_caption(
            chat_id=-100123,
            message_id=777,
            caption="terminal",
            buttons=None,
        )

    assert observed_payload is not None
    assert observed_payload["reply_markup"] == {"inline_keyboard": []}


@pytest.mark.asyncio
async def test_duplicate_venmo_confirm_repairs_stale_message_without_duplicate_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_venmo(tmp_path)
    reset_fake_gateway()
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-100123")
    get_settings.cache_clear()
    gateway = FakeConfirmationGateway()
    update = TelegramBotUpdate(
        update_id=1,
        payload={
            "callback_query": {
                "id": "callback-duplicate",
                "data": encode_venmo_confirmation_callback(
                    501,
                    VenmoConfirmationCallbackAction.CONFIRM,
                ),
                "from": {"id": 700, "username": "receiver"},
                "message": {
                    "message_id": 777,
                    "chat": {"id": -100123, "type": "supergroup"},
                },
            }
        },
    )

    await handle_cashout_bot_update(
        update,
        gateway=gateway,
        session_factory=TestSessionFactory,
        report=lambda _: None,
    )
    await handle_cashout_bot_update(
        update,
        gateway=gateway,
        session_factory=TestSessionFactory,
        report=lambda _: None,
    )

    async with TestSessionFactory() as session:
        events = list(
            await session.scalars(
                select(VenmoConfirmationEvent).where(
                    VenmoConfirmationEvent.request_id == 100,
                    VenmoConfirmationEvent.event_type == VenmoConfirmationEventType.CONFIRMED,
                )
            )
        )
    assert len(events) == 1
    assert FakeConfirmationGateway.edits[-1]["buttons"] is None
    assert "✅✅ CONFIRMATION COMPLETED ✅✅" in str(
        FakeConfirmationGateway.edits[-1]["caption"]
    )
    assert FakeConfirmationGateway.answers[-1]["text"] == "Already confirmed."


@pytest.mark.asyncio
async def test_venmo_confirm_edit_failure_preserves_confirmed_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_venmo(tmp_path)
    reset_fake_gateway()
    FakeConfirmationGateway.fail_edit = True
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-100123")
    get_settings.cache_clear()
    gateway = FakeConfirmationGateway()

    await handle_cashout_bot_update(
        TelegramBotUpdate(
            update_id=1,
            payload={
                "callback_query": {
                    "id": "callback-edit-fails",
                    "data": encode_venmo_confirmation_callback(
                        501,
                        VenmoConfirmationCallbackAction.CONFIRM,
                    ),
                    "from": {"id": 700, "username": "receiver"},
                    "message": {
                        "message_id": 777,
                        "chat": {"id": -100123, "type": "supergroup"},
                    },
                }
            },
        ),
        gateway=gateway,
        session_factory=TestSessionFactory,
        report=lambda _: None,
    )

    async with TestSessionFactory() as session:
        request = await session.get(VenmoConfirmationRequest, 100)
        attempt = await session.get(VenmoConfirmationAttempt, 501)
        confirmed_events = list(
            await session.scalars(
                select(VenmoConfirmationEvent).where(
                    VenmoConfirmationEvent.request_id == 100,
                    VenmoConfirmationEvent.event_type == VenmoConfirmationEventType.CONFIRMED,
                )
            )
        )
    assert request is not None
    assert attempt is not None
    assert request.status == VenmoConfirmationStatus.CONFIRMED
    assert attempt.status == VenmoConfirmationAttemptStatus.CONFIRMED
    assert attempt.last_error == "terminal_sync_failed: caption edit failed"
    assert len(confirmed_events) == 1
    assert FakeConfirmationGateway.answers[-1]["text"] == "Confirmation marked confirmed."


@pytest.mark.asyncio
async def test_venmo_telegram_not_received_callback_records_inquiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_venmo(tmp_path)
    async with TestSessionFactory() as session:
        existing = await session.get(VenmoConfirmationInquiry, 601)
        assert existing is not None
        await session.delete(existing)
        await session.commit()
    reset_fake_gateway()
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-100123")
    get_settings.cache_clear()
    gateway = FakeConfirmationGateway()

    update = TelegramBotUpdate(
        update_id=1,
        payload={
            "callback_query": {
                "id": "callback-2",
                "data": encode_venmo_confirmation_callback(
                    501,
                    VenmoConfirmationCallbackAction.NOT_RECEIVED,
                ),
                "from": {"id": 700, "username": "receiver"},
                "message": {
                    "message_id": 777,
                    "chat": {"id": -100123, "type": "supergroup"},
                },
            }
        },
    )

    await handle_cashout_bot_update(
        update,
        gateway=gateway,
        session_factory=TestSessionFactory,
        report=lambda _: None,
    )

    async with TestSessionFactory() as session:
        request = await session.get(VenmoConfirmationRequest, 100)
        attempt = await session.get(VenmoConfirmationAttempt, 501)
    assert request is not None
    assert attempt is not None
    assert request.status == VenmoConfirmationStatus.NOT_RECEIVED
    assert attempt.status == VenmoConfirmationAttemptStatus.NOT_RECEIVED
    caption = str(FakeConfirmationGateway.edits[-1]["caption"])
    assert "⚠️ CONFIRMATION NOT RECEIVED" in caption
    assert "🟡 FOLLOW-UP REQUIRED" in caption
    assert "The evidence was marked Not Received." in caption


@pytest.mark.asyncio
async def test_duplicate_venmo_not_received_repairs_stale_message_without_duplicate_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_venmo(tmp_path)
    async with TestSessionFactory() as session:
        existing = await session.get(VenmoConfirmationInquiry, 601)
        assert existing is not None
        await session.delete(existing)
        await session.commit()
    reset_fake_gateway()
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-100123")
    get_settings.cache_clear()
    gateway = FakeConfirmationGateway()
    update = TelegramBotUpdate(
        update_id=1,
        payload={
            "callback_query": {
                "id": "callback-not-received-duplicate",
                "data": encode_venmo_confirmation_callback(
                    501,
                    VenmoConfirmationCallbackAction.NOT_RECEIVED,
                ),
                "from": {"id": 700, "username": "receiver"},
                "message": {
                    "message_id": 777,
                    "chat": {"id": -100123, "type": "supergroup"},
                },
            }
        },
    )

    await handle_cashout_bot_update(
        update,
        gateway=gateway,
        session_factory=TestSessionFactory,
        report=lambda _: None,
    )
    await handle_cashout_bot_update(
        update,
        gateway=gateway,
        session_factory=TestSessionFactory,
        report=lambda _: None,
    )

    async with TestSessionFactory() as session:
        events = list(
            await session.scalars(
                select(VenmoConfirmationEvent).where(
                    VenmoConfirmationEvent.request_id == 100,
                    VenmoConfirmationEvent.event_type == VenmoConfirmationEventType.NOT_RECEIVED,
                )
            )
        )
        inquiries = list(
            await session.scalars(
                select(VenmoConfirmationInquiry).where(
                    VenmoConfirmationInquiry.request_id == 100,
                )
            )
        )
    assert len(events) == 1
    assert len(inquiries) == 1
    assert FakeConfirmationGateway.edits[-1]["buttons"] is None
    assert "⚠️ CONFIRMATION NOT RECEIVED" in str(FakeConfirmationGateway.edits[-1]["caption"])
    assert FakeConfirmationGateway.answers[-1]["text"] == "Confirmation was already resolved."


@pytest.mark.asyncio
async def test_venmo_reconciliation_repairs_confirmed_terminal_message(
    tmp_path: Path,
) -> None:
    await seed_venmo(tmp_path)
    reset_fake_gateway()
    async with TestSessionFactory() as session, session.begin():
        request = await session.get(VenmoConfirmationRequest, 100)
        attempt = await session.get(VenmoConfirmationAttempt, 501)
        assert request is not None
        assert attempt is not None
        request.status = VenmoConfirmationStatus.CONFIRMED
        request.confirmed_at = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
        request.confirmed_by_display_name = "receiver"
        attempt.status = VenmoConfirmationAttemptStatus.CONFIRMED
        attempt.last_error = "terminal_sync_failed: stale"

    result = await reconcile_venmo_confirmation_telegram_state(
        request_id=100,
        gateway=FakeConfirmationGateway(),
    )

    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, 501)
    assert result.status == "confirmed"
    assert result.sync_result == "edited_terminal"
    assert attempt is not None
    assert attempt.last_error is None
    assert FakeConfirmationGateway.edits[-1]["buttons"] is None
    assert FakeConfirmationGateway.edits[-1]["caption"] == (
        "✅✅ CONFIRMATION COMPLETED ✅✅\n"
        "🟢 CONFIRMED\n"
        "\n"
        "Request ID: #100\n"
        "\n"
        "✅ EVIDENCE CONFIRMED\n"
        "\n"
        "Confirmed By: receiver\n"
        "Confirmed At: 2026-07-15 16:00 UTC"
    )


@pytest.mark.asyncio
async def test_venmo_reconciliation_treats_message_not_modified_as_already_synced(
    tmp_path: Path,
) -> None:
    await seed_venmo(tmp_path)
    reset_fake_gateway()
    async with TestSessionFactory() as session, session.begin():
        request = await session.get(VenmoConfirmationRequest, 100)
        attempt = await session.get(VenmoConfirmationAttempt, 501)
        assert request is not None
        assert attempt is not None
        request.status = VenmoConfirmationStatus.CONFIRMED
        request.confirmed_at = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
        request.confirmed_by_display_name = "receiver"
        attempt.status = VenmoConfirmationAttemptStatus.CONFIRMED
        attempt.last_error = "terminal_sync_failed: stale"
        session.add(
            VenmoConfirmationEvent(
                id=702,
                request_id=100,
                attempt_id=501,
                event_type=VenmoConfirmationEventType.CONFIRMED,
                actor_source="telegram",
                actor_identifier="700",
            )
        )
    FakeConfirmationGateway.edit_error = TelegramBotApiError(
        "Bad Request: message is not modified: specified new message content and "
        "reply markup are exactly the same as a current content and reply markup "
        "of the message",
        failure_class=TelegramBotFailureClass.NON_RETRYABLE,
        status_code=400,
    )

    result = await reconcile_venmo_confirmation_telegram_state(
        request_id=100,
        gateway=FakeConfirmationGateway(),
    )

    async with TestSessionFactory() as session:
        request = await session.get(VenmoConfirmationRequest, 100)
        attempt = await session.get(VenmoConfirmationAttempt, 501)
        confirmed_events = list(
            await session.scalars(
                select(VenmoConfirmationEvent).where(
                    VenmoConfirmationEvent.request_id == 100,
                    VenmoConfirmationEvent.event_type == VenmoConfirmationEventType.CONFIRMED,
                )
            )
        )
    assert result.status == "confirmed"
    assert result.sync_result == "already_synced"
    assert request is not None
    assert attempt is not None
    assert request.status == VenmoConfirmationStatus.CONFIRMED
    assert request.confirmed_by_display_name == "receiver"
    assert attempt.status == VenmoConfirmationAttemptStatus.CONFIRMED
    assert attempt.last_error is None
    assert len(confirmed_events) == 1


@pytest.mark.asyncio
async def test_venmo_reconciliation_keeps_genuine_telegram_400_as_failure(
    tmp_path: Path,
) -> None:
    await seed_venmo(tmp_path)
    reset_fake_gateway()
    async with TestSessionFactory() as session, session.begin():
        request = await session.get(VenmoConfirmationRequest, 100)
        attempt = await session.get(VenmoConfirmationAttempt, 501)
        assert request is not None
        assert attempt is not None
        request.status = VenmoConfirmationStatus.CONFIRMED
        request.confirmed_at = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
        request.confirmed_by_display_name = "receiver"
        attempt.status = VenmoConfirmationAttemptStatus.CONFIRMED
        session.add(
            VenmoConfirmationEvent(
                id=702,
                request_id=100,
                attempt_id=501,
                event_type=VenmoConfirmationEventType.CONFIRMED,
                actor_source="telegram",
                actor_identifier="700",
            )
        )
    FakeConfirmationGateway.edit_error = TelegramBotApiError(
        "Bad Request: message caption is too long",
        failure_class=TelegramBotFailureClass.NON_RETRYABLE,
        status_code=400,
    )

    result = await reconcile_venmo_confirmation_telegram_state(
        request_id=100,
        gateway=FakeConfirmationGateway(),
    )

    async with TestSessionFactory() as session:
        request = await session.get(VenmoConfirmationRequest, 100)
        attempt = await session.get(VenmoConfirmationAttempt, 501)
        confirmed_events = list(
            await session.scalars(
                select(VenmoConfirmationEvent).where(
                    VenmoConfirmationEvent.request_id == 100,
                    VenmoConfirmationEvent.event_type == VenmoConfirmationEventType.CONFIRMED,
                )
            )
        )
    assert result.status == "confirmed"
    assert result.sync_result == "failed"
    assert request is not None
    assert attempt is not None
    assert request.status == VenmoConfirmationStatus.CONFIRMED
    assert attempt.status == VenmoConfirmationAttemptStatus.CONFIRMED
    assert attempt.last_error == "terminal_sync_failed: Bad Request: message caption is too long"
    assert len(confirmed_events) == 1


@pytest.mark.asyncio
async def test_venmo_not_received_reconciliation_treats_message_not_modified_as_synced(
    tmp_path: Path,
) -> None:
    await seed_venmo(tmp_path)
    reset_fake_gateway()
    async with TestSessionFactory() as session, session.begin():
        request = await session.get(VenmoConfirmationRequest, 100)
        attempt = await session.get(VenmoConfirmationAttempt, 501)
        assert request is not None
        assert attempt is not None
        request.status = VenmoConfirmationStatus.NOT_RECEIVED
        attempt.status = VenmoConfirmationAttemptStatus.NOT_RECEIVED
        attempt.last_error = "terminal_sync_failed: stale"
        session.add(
            VenmoConfirmationEvent(
                id=702,
                request_id=100,
                attempt_id=501,
                event_type=VenmoConfirmationEventType.NOT_RECEIVED,
                actor_source="telegram",
                actor_identifier="700",
            )
        )
    FakeConfirmationGateway.edit_error = TelegramBotApiError(
        "Bad Request: message is not modified",
        failure_class=TelegramBotFailureClass.NON_RETRYABLE,
        status_code=400,
    )

    result = await reconcile_venmo_confirmation_telegram_state(
        request_id=100,
        gateway=FakeConfirmationGateway(),
    )

    async with TestSessionFactory() as session:
        request = await session.get(VenmoConfirmationRequest, 100)
        attempt = await session.get(VenmoConfirmationAttempt, 501)
        not_received_events = list(
            await session.scalars(
                select(VenmoConfirmationEvent).where(
                    VenmoConfirmationEvent.request_id == 100,
                    VenmoConfirmationEvent.event_type == VenmoConfirmationEventType.NOT_RECEIVED,
                )
            )
        )
    assert result.status == "not_received"
    assert result.sync_result == "already_synced"
    assert request is not None
    assert attempt is not None
    assert request.status == VenmoConfirmationStatus.NOT_RECEIVED
    assert attempt.status == VenmoConfirmationAttemptStatus.NOT_RECEIVED
    assert attempt.last_error is None
    assert len(not_received_events) == 1


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


@pytest.mark.asyncio
async def test_authorized_user_can_upload_venmo_payment_screenshot(tmp_path: Path) -> None:
    await seed_venmo(tmp_path)

    async with api_client_for(STAFF) as client:
        response = await client.post(
            "/api/venmo-confirmations/100/payment-screenshot",
            files={"file": ("receipt.png", PNG_BYTES, "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["media"]["original_filename"] == "receipt.png"
    assert body["media"]["mime_type"] == "image/png"
    assert body["media"]["preview_url"].startswith("/api/venmo-confirmations/media/")
    assert body["media"]["preview_url"] != "/api/venmo-confirmations/media/1"
    assert body["screenshot_media_asset_id"] == body["media"]["id"]
    assert body["events"][-1]["event_type"] == "payment_screenshot_uploaded"


@pytest.mark.asyncio
async def test_cross_coadmin_venmo_screenshot_upload_is_rejected(tmp_path: Path) -> None:
    await seed_venmo(tmp_path)

    async with api_client_for(OTHER_STAFF) as client:
        response = await client.post(
            "/api/venmo-confirmations/100/payment-screenshot",
            files={"file": ("receipt.png", PNG_BYTES, "image/png")},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_non_image_venmo_screenshot_upload_is_rejected(tmp_path: Path) -> None:
    await seed_venmo(tmp_path)

    async with api_client_for(STAFF) as client:
        response = await client.post(
            "/api/venmo-confirmations/100/payment-screenshot",
            files={"file": ("receipt.txt", b"not-image", "text/plain")},
        )

    assert response.status_code == 415


@pytest.mark.asyncio
async def test_oversized_venmo_screenshot_upload_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_venmo(tmp_path)
    monkeypatch.setenv("INQUIRY_MEDIA_MAX_BYTES", "8")
    get_settings.cache_clear()

    async with api_client_for(STAFF) as client:
        response = await client.post(
            "/api/venmo-confirmations/100/payment-screenshot",
            files={"file": ("receipt.png", PNG_BYTES, "image/png")},
        )

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_traversal_filename_cannot_escape_venmo_media_root(tmp_path: Path) -> None:
    await seed_venmo(tmp_path)

    async with api_client_for(STAFF) as client:
        response = await client.post(
            "/api/venmo-confirmations/100/payment-screenshot",
            files={"file": ("../evil.png", PNG_BYTES, "image/png")},
        )

    assert response.status_code == 200
    media_id = response.json()["media"]["id"]
    async with TestSessionFactory() as session:
        asset = await session.get(MediaAsset, media_id)
        assert asset is not None
        assert asset.original_filename == "evil.png"
        storage_key = asset.storage_key
    assert upload_stayed_inside_media_root(tmp_path, storage_key, "evil.png")


@pytest.mark.asyncio
async def test_venmo_screenshot_upload_does_not_alter_ledger_totals(tmp_path: Path) -> None:
    await seed_venmo(tmp_path)

    async with api_client_for(ADMIN) as client:
        before = await client.get("/api/admin/ledger")
    async with api_client_for(STAFF) as client:
        uploaded = await client.post(
            "/api/venmo-confirmations/100/payment-screenshot",
            files={"file": ("receipt.png", PNG_BYTES, "image/png")},
        )
    async with api_client_for(ADMIN) as client:
        after = await client.get("/api/admin/ledger")

    assert before.status_code == 200
    assert uploaded.status_code == 200
    assert after.status_code == 200
    assert after.json()["summary"] == before.json()["summary"]

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

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
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import app
from app.models.cashout import (
    CashoutCompletionType,
    CashoutRequest,
    CashoutStatus,
    CashoutTelegramStatus,
    CashoutType,
)
from app.models.media_asset import MediaAsset
from app.models.user import User, UserRole
from app.models.workflow_settings import CoadminTelegramWorkflowSettings
from app.services import cashout as cashout_service
from app.services.cashout_telegram import CashoutTelegramService
from app.telegram import cashout_delivery
from app.telegram.cashout_bot.api import TelegramBotApiError, TelegramBotFailureClass
from app.telegram.cashout_bot.messages import (
    CashoutCallbackAction,
    encode_callback_data,
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

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 32


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
ADMIN = make_user(1, "admin", UserRole.ADMIN)


class PhotoBotGateway:
    def __init__(self, *, message_id: int = 901) -> None:
        self.message_id = message_id
        self.sent_photos: list[dict[str, Any]] = []
        self.sent_cashouts: list[dict[str, Any]] = []
        self.caption_edits: list[dict[str, Any]] = []
        self.text_edits: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []

    async def send_photo(
        self,
        *,
        chat_id: int,
        photo_path: Path,
        caption: str,
        buttons: list[list[tuple[str, str]]],
        mime_type: str,
        filename: str | None = None,
    ) -> int:
        self.sent_photos.append(
            {
                "chat_id": chat_id,
                "photo_path": photo_path,
                "caption": caption,
                "buttons": buttons,
                "mime_type": mime_type,
                "filename": filename,
            }
        )
        return self.message_id

    async def send_cashout_task_message(
        self,
        *,
        chat_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]],
    ) -> int:
        self.sent_cashouts.append({"chat_id": chat_id, "text": text, "buttons": buttons})
        return self.message_id

    async def edit_message_caption(
        self,
        *,
        chat_id: int,
        message_id: int,
        caption: str,
        buttons: list[list[tuple[str, str]]] | None = None,
    ) -> None:
        self.caption_edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "caption": caption,
                "buttons": buttons,
            }
        )

    async def edit_cashout_task_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
    ) -> None:
        self.text_edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "buttons": buttons,
            }
        )

    async def answer_callback_query(
        self,
        *,
        query_id: int | str,
        text: str,
        alert: bool = False,
    ) -> None:
        self.answers.append({"query_id": query_id, "text": text, "alert": alert})

    async def delete_message(self, *, chat_id: int, message_id: int) -> bool:
        return False

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> int:
        return 777

    async def get_updates(self, *, offset: int | None) -> list[object]:
        return []

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        return None


class FailingPhotoGateway(PhotoBotGateway):
    def __init__(self, error: TelegramBotApiError) -> None:
        super().__init__()
        self.error = error
        self.calls = 0

    async def send_photo(
        self,
        *,
        chat_id: int,
        photo_path: Path,
        caption: str,
        buttons: list[list[tuple[str, str]]],
        mime_type: str,
        filename: str | None = None,
    ) -> int:
        del chat_id, photo_path, caption, buttons, mime_type, filename
        self.calls += 1
        raise self.error


@asynccontextmanager
async def staff_client() -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    async def override_current_user() -> User:
        return STAFF

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


@asynccontextmanager
async def admin_client() -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    async def override_current_user() -> User:
        return ADMIN

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


@pytest.fixture(autouse=True)
def stub_immediate_telegram_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cashout_service,
        "_attempt_immediate_cashout_delivery",
        AsyncMock(return_value=None),
    )


@pytest_asyncio.fixture(autouse=True)
async def reset_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "inquiry_media_dir", str(tmp_path / "media"))
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSessionFactory() as session:
        session.add_all(
            [
                make_user(42, "sarah", UserRole.STAFF, coadmin_id=10),
                make_user(1, "admin", UserRole.ADMIN),
                make_user(10, "default_coadmin", UserRole.COADMIN),
            ]
        )
        session.add(
            CoadminTelegramWorkflowSettings(
                coadmin_id=10,
                cashout_group_id=-1004373307239,
                venmo_confirmation_group_id=-1004373307240,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        await session.commit()
    monkeypatch.setattr(cashout_delivery, "SessionFactory", TestSessionFactory)
    monkeypatch.setattr(
        "app.websocket.cross_process.notify_live_event",
        AsyncMock(return_value=None),
    )
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


async def _seed_qr_cashout(
    *,
    cashout_id: int = 5,
    telegram_status: CashoutTelegramStatus = CashoutTelegramStatus.PENDING,
    telegram_message_id: int | None = None,
    storage_key: str = "cashout/10/qr/test-qr.png",
) -> int:
    async with TestSessionFactory() as session:
        media = MediaAsset(
            id=100 + cashout_id,
            coadmin_id=10,
            storage_key=storage_key,
            original_filename="test-qr.png",
            mime_type="image/png",
            size_bytes=len(PNG_BYTES),
            checksum_sha256="abc123",
            created_by_user_id=42,
            created_at=datetime(2026, 7, 6, tzinfo=UTC),
        )
        session.add(media)
        session.add(
            CashoutRequest(
                id=cashout_id,
                request_number=f"CR-{cashout_id:06d}",
                idempotency_key=f"00000000-0000-0000-0000-{cashout_id:012d}",
                player_tag="QR",
                cashout_type=CashoutType.QR,
                qr_media_asset_id=media.id,
                amount=Decimal("250.00"),
                status=CashoutStatus.PENDING,
                telegram_status=telegram_status,
                telegram_message_id=telegram_message_id,
                telegram_chat_id=-1004373307239 if telegram_message_id else None,
                telegram_random_id=900000 + cashout_id,
                created_by_staff_id=42,
                coadmin_id=10,
                created_at=datetime(2026, 7, 6, tzinfo=UTC),
                updated_at=datetime(2026, 7, 6, tzinfo=UTC),
            )
        )
        await session.commit()
        return media.id


@pytest.mark.asyncio
async def test_normal_cashout_create_still_works() -> None:
    async with staff_client() as client:
        response = await client.post(
            "/api/cashouts",
            json={
                "player_tag": "ABC12345",
                "amount": "250.00",
                "notes": "VIP",
                "idempotency_key": "11111111-1111-1111-1111-111111111111",
            },
        )
    assert response.status_code == 201
    payload = response.json()
    assert payload["cashout_type"] == "standard"
    assert payload["qr_media_asset_id"] is None
    assert payload["player_tag"] == "ABC12345"


@pytest.mark.asyncio
async def test_qr_cashout_requires_amount_and_image() -> None:
    async with staff_client() as client:
        missing_image = await client.post(
            "/api/cashouts/qr",
            data={
                "amount": "250.00",
                "idempotency_key": "22222222-2222-2222-2222-222222222222",
            },
        )
        assert missing_image.status_code == 400
        assert "QR image is required" in missing_image.json()["detail"]

        invalid_image = await client.post(
            "/api/cashouts/qr",
            data={
                "amount": "250.00",
                "idempotency_key": "33333333-3333-3333-3333-333333333333",
            },
            files={"qr_image": ("bad.txt", b"not-an-image", "text/plain")},
        )
        assert invalid_image.status_code == 415


@pytest.mark.asyncio
async def test_qr_cashout_persists_media_and_single_record(tmp_path: Path) -> None:
    async with staff_client() as client:
        response = await client.post(
            "/api/cashouts/qr",
            data={
                "amount": "250.00",
                "idempotency_key": "44444444-4444-4444-4444-444444444444",
            },
            files={"qr_image": ("qr.png", PNG_BYTES, "image/png")},
        )
    assert response.status_code == 201
    payload = response.json()
    assert payload["cashout_type"] == "qr"
    assert payload["qr_media_asset_id"] is not None

    async with TestSessionFactory() as session:
        count = await session.scalar(select(func.count()).select_from(CashoutRequest))
        assert count == 1
        media = await session.get(MediaAsset, payload["qr_media_asset_id"])
        assert media is not None
        stored_path = tmp_path / "media" / media.storage_key
        assert stored_path.is_file()
        assert stored_path.read_bytes() == PNG_BYTES


@pytest.mark.asyncio
async def test_qr_delivery_sends_photo_with_caption_and_buttons(tmp_path: Path) -> None:
    storage_key = "cashout/10/qr/delivery-test.png"
    media_path = tmp_path / "media" / storage_key
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(PNG_BYTES)
    await _seed_qr_cashout(storage_key=storage_key)
    gateway = PhotoBotGateway()

    processed = await cashout_delivery.deliver_next_cashout(
        object(),
        None,
        telegram_chat_id=-1004373307239,
        bot_gateway=gateway,
    )

    assert processed is True
    assert len(gateway.sent_photos) == 1
    photo = gateway.sent_photos[0]
    assert photo["photo_path"].is_file()
    assert "💸 Cash Out — CR-000005" in photo["caption"]
    assert "Amount: $250.00" in photo["caption"]
    assert photo["buttons"] == [
        [
            ("Full Payment", encode_callback_data(5, CashoutCallbackAction.FULL)),
            ("Partial Payment", encode_callback_data(5, CashoutCallbackAction.PARTIAL)),
        ]
    ]
    assert len(gateway.sent_cashouts) == 0

    async with TestSessionFactory() as session:
        cashout = await session.get(CashoutRequest, 5)
        assert cashout is not None
        assert cashout.telegram_message_id == 901
        assert cashout.telegram_status == CashoutTelegramStatus.SENT


@pytest.mark.asyncio
async def test_qr_full_payment_updates_caption_not_text(tmp_path: Path) -> None:
    storage_key = "cashout/10/qr/payment-test.png"
    media_path = tmp_path / "media" / storage_key
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(PNG_BYTES)
    await _seed_qr_cashout(
        storage_key=storage_key,
        telegram_status=CashoutTelegramStatus.SENT,
        telegram_message_id=901,
    )
    gateway = PhotoBotGateway()

    async with TestSessionFactory() as session:
        result = await CashoutTelegramService(session, gateway=gateway).handle_callback_query(
            query_id="q1",
            callback_data=encode_callback_data(5, CashoutCallbackAction.FULL),
            telegram_chat_id=-1004373307239,
            telegram_user_id=999,
            telegram_username="cashier",
            message_id=901,
        )
    assert result.status == "completed_full"
    assert len(gateway.caption_edits) == 1
    assert "Paid in Full" in gateway.caption_edits[0]["caption"]
    assert len(gateway.text_edits) == 0


@pytest.mark.asyncio
async def test_qr_partial_payment_updates_caption(tmp_path: Path) -> None:
    storage_key = "cashout/10/qr/partial-test.png"
    media_path = tmp_path / "media" / storage_key
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(PNG_BYTES)
    await _seed_qr_cashout(
        cashout_id=6,
        storage_key=storage_key,
        telegram_status=CashoutTelegramStatus.SENT,
        telegram_message_id=902,
    )
    gateway = PhotoBotGateway()

    async with TestSessionFactory() as session:
        service = CashoutTelegramService(session, gateway=gateway)
        await service.handle_callback_query(
            query_id="q2",
            callback_data=encode_callback_data(6, CashoutCallbackAction.PARTIAL),
            telegram_chat_id=-1004373307239,
            telegram_user_id=999,
            telegram_username="cashier",
            message_id=902,
        )
        result = await service.handle_partial_amount_message(
            telegram_chat_id=-1004373307239,
            telegram_user_id=999,
            telegram_username="cashier",
            text="100",
        )
    assert result is not None
    assert result.status == "completed_partial"
    partial_edits = [
        edit for edit in gateway.caption_edits if "Partial Payment" in edit["caption"]
    ]
    assert partial_edits


@pytest.mark.asyncio
async def test_qr_cancel_updates_caption() -> None:
    await _seed_qr_cashout(
        telegram_status=CashoutTelegramStatus.SENT,
        telegram_message_id=901,
    )
    gateway = PhotoBotGateway()
    async with TestSessionFactory() as session:
        cashout = await session.get(CashoutRequest, 5)
        assert cashout is not None
        cashout.status = CashoutStatus.CANCELLED
        cashout.cancelled_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(cashout)
        status = await CashoutTelegramService(session, gateway=gateway).sync_cancelled_task(
            cashout,
            prefer_delete=False,
        )
    assert status == "edited_cancelled"
    assert gateway.caption_edits
    assert "Cancelled" in gateway.caption_edits[-1]["caption"]


@pytest.mark.asyncio
async def test_qr_retry_resends_same_cashout_without_duplicate_record(
    tmp_path: Path,
) -> None:
    storage_key = "cashout/10/qr/retry-test.png"
    media_path = tmp_path / "media" / storage_key
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(PNG_BYTES)
    await _seed_qr_cashout(
        storage_key=storage_key,
        telegram_status=CashoutTelegramStatus.FAILED_TO_SEND,
    )

    async with admin_client() as client:
        retry = await client.post("/api/cashouts/5/retry-telegram")
        assert retry.status_code == 200

    gateway = PhotoBotGateway(message_id=1001)
    await cashout_delivery.deliver_cashout_by_id(
        5,
        telegram_chat_id=-1004373307239,
        bot_gateway=gateway,
    )

    async with TestSessionFactory() as session:
        count = await session.scalar(select(func.count()).select_from(CashoutRequest))
        assert count == 1
        cashout = await session.get(CashoutRequest, 5)
        assert cashout is not None
        assert cashout.telegram_message_id == 1001
    assert len(gateway.sent_photos) == 1


@pytest.mark.asyncio
async def test_qr_delivery_failure_preserves_cashout_for_retry(tmp_path: Path) -> None:
    storage_key = "cashout/10/qr/failure-test.png"
    media_path = tmp_path / "media" / storage_key
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(PNG_BYTES)
    await _seed_qr_cashout(storage_key=storage_key)
    gateway = FailingPhotoGateway(
        TelegramBotApiError(
            "temporary failure",
            failure_class=TelegramBotFailureClass.RETRYABLE,
        )
    )

    await cashout_delivery.deliver_next_cashout(
        object(),
        None,
        telegram_chat_id=-1004373307239,
        bot_gateway=gateway,
    )

    async with TestSessionFactory() as session:
        cashout = await session.get(CashoutRequest, 5)
        assert cashout is not None
        assert cashout.telegram_status == CashoutTelegramStatus.PENDING
        assert cashout.qr_media_asset_id is not None
        assert cashout.telegram_message_id is None


@pytest.mark.asyncio
async def test_existing_standard_cashout_without_type_fields_still_works() -> None:
    async with TestSessionFactory() as session:
        session.add(
            CashoutRequest(
                id=99,
                request_number="CR-000099",
                idempotency_key="99999999-9999-9999-9999-999999999999",
                player_tag="LEGACY",
                amount=Decimal("100.00"),
                status=CashoutStatus.COMPLETED,
                telegram_status=CashoutTelegramStatus.SENT,
                telegram_message_id=123,
                telegram_chat_id=-1004373307239,
                telegram_random_id=999,
                actual_paid_amount=Decimal("100.00"),
                completion_type=CashoutCompletionType.FULL,
                created_by_staff_id=42,
                coadmin_id=10,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        await session.commit()

    async with staff_client() as client:
        response = await client.get("/api/cashouts")
    assert response.status_code == 200
    items = response.json()["items"]
    legacy = next(item for item in items if item["id"] == 99)
    assert legacy["cashout_type"] == "standard"
    assert legacy["player_tag"] == "LEGACY"

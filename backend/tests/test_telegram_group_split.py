from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.models.cashout import (
    CashoutRequest,
    CashoutStatus,
    CashoutTelegramStatus,
)
from app.models.media_asset import MediaAsset
from app.models.user import User, UserRole
from app.models.venmo_confirmation import (
    VenmoConfirmationAttempt,
    VenmoConfirmationAttemptStatus,
    VenmoConfirmationEventType,
    VenmoConfirmationRequest,
    VenmoConfirmationStatus,
)
from app.services.cashout_telegram import CashoutTelegramService
from app.services.inquiry import InquiryService
from app.services.venmo_confirmation import VenmoConfirmationService
from app.telegram import cashout_delivery, venmo_confirmation_delivery
from app.telegram.cashout_bot.messages import CashoutCallbackAction, encode_callback_data
from app.telegram.peer_ids import authorize_configured_or_persisted_chat
from app.telegram.venmo_confirmation import (
    VenmoConfirmationCallbackAction,
    encode_venmo_confirmation_callback,
)
from app.telegram.venmo_confirmation_delivery import send_confirmation_attempt_with_retries

PAYMENT_GROUP_ID = -5413513424
CASHOUT_GROUP_ID = -5310496053
VENMO_GROUP_ID = -5198735527
LEGACY_SHARED_GROUP_ID = -1004487243563

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


class RecordingVenmoGateway:
    sent: list[dict[str, object]] = []

    async def __aenter__(self) -> RecordingVenmoGateway:
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
    ) -> int:
        self.sent.append(
            {
                "chat_id": chat_id,
                "photo_path": photo_path,
                "caption": caption,
                "buttons": buttons,
                "mime_type": mime_type,
                "filename": filename,
            }
        )
        return 9001

    async def answer_callback_query(self, **kwargs: object) -> None:
        return None

    async def edit_message_caption(self, **kwargs: object) -> None:
        return None


class RecordingCashoutGateway:
    def __init__(self) -> None:
        self.sent_cashouts: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []

    async def send_cashout_task_message(
        self,
        *,
        chat_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]],
    ) -> int:
        self.sent_cashouts.append({"chat_id": chat_id, "text": text, "buttons": buttons})
        return 555

    async def answer_callback_query(
        self,
        *,
        query_id: int | str,
        text: str,
        alert: bool = False,
    ) -> None:
        self.answers.append({"query_id": query_id, "text": text, "alert": alert})

    async def edit_cashout_task_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
    ) -> None:
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "buttons": buttons,
            }
        )


def gateway_factory() -> RecordingVenmoGateway:
    return RecordingVenmoGateway()


async def no_sleep(_: float) -> None:
    return None


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


@pytest_asyncio.fixture(autouse=True)
async def reset_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[None]:
    monkeypatch.setenv("INQUIRY_MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_GROUP_ID", str(PAYMENT_GROUP_ID))
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", str(CASHOUT_GROUP_ID))
    monkeypatch.setenv("TELEGRAM_VENMO_GROUP_ID", str(VENMO_GROUP_ID))
    monkeypatch.setattr(venmo_confirmation_delivery, "SessionFactory", TestSessionFactory)
    monkeypatch.setattr(cashout_delivery, "SessionFactory", TestSessionFactory)
    get_settings.cache_clear()
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSessionFactory() as session:
        session.add_all(
            [
                make_user(42, "sarah", UserRole.STAFF, coadmin_id=10),
                make_user(10, "default_coadmin", UserRole.COADMIN),
            ]
        )
        await session.commit()
    RecordingVenmoGateway.sent = []
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()
    get_settings.cache_clear()


async def seed_venmo_attempt(tmp_path: Path) -> tuple[int, int]:
    (tmp_path / "evidence").mkdir(exist_ok=True)
    (tmp_path / "evidence" / "venmo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    async with TestSessionFactory() as session, session.begin():
        session.add(
            MediaAsset(
                id=1,
                coadmin_id=10,
                storage_key="evidence/venmo.png",
                original_filename="venmo.png",
                mime_type="image/png",
                size_bytes=8,
                checksum_sha256="a" * 64,
                created_by_user_id=42,
            )
        )
        service = VenmoConfirmationService(session)
        request = await service.create_request(
            actor=STAFF,
            screenshot_media_asset_id=1,
            payment_note="Player paid",
        )
        attempt = await service.create_attempt(request_id=request.id, coadmin_id=10)
        return request.id, attempt.id


async def deliver_venmo(request_id: int, attempt_id: int) -> None:
    async with TestSessionFactory() as session, session.begin():
        service = VenmoConfirmationService(session)
        request = await session.get(VenmoConfirmationRequest, request_id)
        media = await session.get(MediaAsset, 1)
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
        assert request is not None
        assert media is not None
        assert attempt is not None
        await send_confirmation_attempt_with_retries(
            service=service,
            request=request,
            media=media,
            attempt=attempt,
            event_type=VenmoConfirmationEventType.ATTEMPT_POSTED,
            gateway_factory=gateway_factory,
            sleep=no_sleep,
            retry_delays_seconds=(0.0, 2.0),
            jitter_ratio=0,
        )


async def seed_cashout(
    *,
    telegram_chat_id: int | None = CASHOUT_GROUP_ID,
    telegram_message_id: int | None = 555,
    status: CashoutStatus = CashoutStatus.SENT,
    telegram_status: CashoutTelegramStatus = CashoutTelegramStatus.SENT,
) -> None:
    timestamp = datetime(2026, 7, 6, 20, 35, tzinfo=UTC)
    async with TestSessionFactory() as session:
        session.add(
            CashoutRequest(
                id=1,
                request_number="CR-000001",
                idempotency_key="00000000-0000-0000-0000-000000000001",
                coadmin_id=10,
                created_by_staff_id=42,
                player_tag="Player",
                amount=Decimal("250.00"),
                status=status,
                telegram_status=telegram_status,
                telegram_chat_id=telegram_chat_id,
                telegram_message_id=telegram_message_id,
                telegram_random_id=10001,
                telegram_attempts=0 if telegram_message_id is None else 1,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        await session.commit()


def test_authorize_helper_allows_persisted_legacy_chat() -> None:
    assert authorize_configured_or_persisted_chat(
        incoming_chat_id=VENMO_GROUP_ID,
        configured_chat_id=VENMO_GROUP_ID,
        persisted_chat_id=None,
    )
    assert authorize_configured_or_persisted_chat(
        incoming_chat_id=LEGACY_SHARED_GROUP_ID,
        configured_chat_id=VENMO_GROUP_ID,
        persisted_chat_id=LEGACY_SHARED_GROUP_ID,
    )
    assert not authorize_configured_or_persisted_chat(
        incoming_chat_id=CASHOUT_GROUP_ID,
        configured_chat_id=VENMO_GROUP_ID,
        persisted_chat_id=VENMO_GROUP_ID,
    )
    assert not authorize_configured_or_persisted_chat(
        incoming_chat_id=CASHOUT_GROUP_ID,
        configured_chat_id=VENMO_GROUP_ID,
        persisted_chat_id=None,
    )


@pytest.mark.asyncio
async def test_venmo_delivery_uses_venmo_group_not_cashout(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_venmo_attempt(tmp_path)
    await deliver_venmo(request_id, attempt_id)

    assert RecordingVenmoGateway.sent[0]["chat_id"] == VENMO_GROUP_ID
    assert RecordingVenmoGateway.sent[0]["chat_id"] != CASHOUT_GROUP_ID
    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
    assert attempt is not None
    assert attempt.telegram_chat_id == VENMO_GROUP_ID
    assert attempt.telegram_message_id == 9001


@pytest.mark.asyncio
async def test_venmo_retry_still_targets_venmo_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.telegram.cashout_bot.api import TelegramBotApiError, TelegramBotFailureClass

    class RetryGateway(RecordingVenmoGateway):
        calls = 0

        async def send_photo(self, **kwargs: object) -> int:
            type(self).calls += 1
            if type(self).calls == 1:
                raise TelegramBotApiError(
                    "Telegram Bot API request timed out",
                    failure_class=TelegramBotFailureClass.RETRYABLE,
                )
            return await super().send_photo(**kwargs)

    monkeypatch.setattr(
        "app.telegram.venmo_confirmation_delivery.TelegramBotApiGateway",
        RetryGateway,
    )
    RetryGateway.sent = []
    RetryGateway.calls = 0

    def retry_factory() -> RetryGateway:
        return RetryGateway()

    request_id, attempt_id = await seed_venmo_attempt(tmp_path)
    async with TestSessionFactory() as session, session.begin():
        service = VenmoConfirmationService(session)
        request = await session.get(VenmoConfirmationRequest, request_id)
        media = await session.get(MediaAsset, 1)
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
        assert request is not None
        assert media is not None
        assert attempt is not None
        await send_confirmation_attempt_with_retries(
            service=service,
            request=request,
            media=media,
            attempt=attempt,
            event_type=VenmoConfirmationEventType.ATTEMPT_POSTED,
            gateway_factory=retry_factory,
            sleep=no_sleep,
            retry_delays_seconds=(0.0, 2.0),
            jitter_ratio=0,
        )

    assert RetryGateway.calls == 2
    assert all(item["chat_id"] == VENMO_GROUP_ID for item in RetryGateway.sent)
    assert CASHOUT_GROUP_ID not in {item["chat_id"] for item in RetryGateway.sent}


@pytest.mark.asyncio
async def test_unset_venmo_group_falls_back_to_cashout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_VENMO_GROUP_ID", raising=False)
    get_settings.cache_clear()
    request_id, attempt_id = await seed_venmo_attempt(tmp_path)
    await deliver_venmo(request_id, attempt_id)
    assert RecordingVenmoGateway.sent[0]["chat_id"] == CASHOUT_GROUP_ID
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_missing_venmo_and_cashout_group_fails_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_VENMO_GROUP_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "")
    get_settings.cache_clear()
    request_id, attempt_id = await seed_venmo_attempt(tmp_path)
    await deliver_venmo(request_id, attempt_id)
    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.FAILED_TO_SEND
    assert attempt.last_error is not None
    assert "TELEGRAM_VENMO_GROUP_ID or TELEGRAM_CASHOUT_GROUP_ID" in attempt.last_error
    assert RecordingVenmoGateway.sent == []
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_cashout_delivery_uses_cashout_group_not_venmo() -> None:
    await seed_cashout(
        telegram_chat_id=None,
        telegram_message_id=None,
        status=CashoutStatus.PENDING,
        telegram_status=CashoutTelegramStatus.PENDING,
    )
    gateway = RecordingCashoutGateway()
    processed = await cashout_delivery.deliver_next_cashout(
        object(),
        "group",
        telegram_chat_id=CASHOUT_GROUP_ID,
        bot_gateway=gateway,
    )
    assert processed is True
    assert gateway.sent_cashouts[0]["chat_id"] == CASHOUT_GROUP_ID
    assert gateway.sent_cashouts[0]["chat_id"] != VENMO_GROUP_ID


@pytest.mark.asyncio
async def test_venmo_callback_in_venmo_group_succeeds(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_venmo_attempt(tmp_path)
    await deliver_venmo(request_id, attempt_id)
    gateway = RecordingVenmoGateway()
    async with TestSessionFactory() as session, session.begin():
        result = await VenmoConfirmationService(session).handle_telegram_callback(
            query_id="q1",
            callback_data=encode_venmo_confirmation_callback(
                attempt_id,
                VenmoConfirmationCallbackAction.CONFIRM,
            ),
            telegram_chat_id=VENMO_GROUP_ID,
            telegram_user_id=555,
            telegram_username="operator",
            message_id=9001,
            gateway=gateway,
        )
    assert result.status == "confirmed"
    async with TestSessionFactory() as session:
        request = await session.get(VenmoConfirmationRequest, request_id)
    assert request is not None
    assert request.status == VenmoConfirmationStatus.CONFIRMED


@pytest.mark.asyncio
async def test_venmo_callback_in_cashout_group_is_rejected(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_venmo_attempt(tmp_path)
    await deliver_venmo(request_id, attempt_id)
    gateway = RecordingVenmoGateway()
    async with TestSessionFactory() as session, session.begin():
        result = await VenmoConfirmationService(session).handle_telegram_callback(
            query_id="q1",
            callback_data=encode_venmo_confirmation_callback(
                attempt_id,
                VenmoConfirmationCallbackAction.CONFIRM,
            ),
            telegram_chat_id=CASHOUT_GROUP_ID,
            telegram_user_id=555,
            telegram_username="operator",
            message_id=9001,
            gateway=gateway,
        )
    assert result.status == "wrong_group"
    async with TestSessionFactory() as session:
        request = await session.get(VenmoConfirmationRequest, request_id)
    assert request is not None
    assert request.status == VenmoConfirmationStatus.PENDING


@pytest.mark.asyncio
async def test_legacy_persisted_venmo_callback_remains_valid(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_venmo_attempt(tmp_path)
    async with TestSessionFactory() as session, session.begin():
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
        assert attempt is not None
        attempt.status = VenmoConfirmationAttemptStatus.POSTED
        attempt.telegram_chat_id = LEGACY_SHARED_GROUP_ID
        attempt.telegram_message_id = 203
    gateway = RecordingVenmoGateway()
    async with TestSessionFactory() as session, session.begin():
        result = await VenmoConfirmationService(session).handle_telegram_callback(
            query_id="q-legacy",
            callback_data=encode_venmo_confirmation_callback(
                attempt_id,
                VenmoConfirmationCallbackAction.CONFIRM,
            ),
            telegram_chat_id=LEGACY_SHARED_GROUP_ID,
            telegram_user_id=555,
            telegram_username="operator",
            message_id=203,
            gateway=gateway,
        )
    assert result.status == "confirmed"
    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
    assert attempt is not None
    assert attempt.telegram_chat_id == LEGACY_SHARED_GROUP_ID


@pytest.mark.asyncio
async def test_cashout_callback_in_cashout_group_succeeds() -> None:
    await seed_cashout()
    gateway = RecordingCashoutGateway()
    async with TestSessionFactory() as session:
        result = await CashoutTelegramService(
            session,
            gateway=gateway,
        ).handle_callback_query(
            query_id="q1",
            callback_data=encode_callback_data(1, CashoutCallbackAction.FULL),
            telegram_chat_id=CASHOUT_GROUP_ID,
            telegram_user_id=9001,
            telegram_username="operator",
            message_id=555,
        )
    assert result.status == "completed_full"
    assert gateway.edits[-1]["chat_id"] == CASHOUT_GROUP_ID


@pytest.mark.asyncio
async def test_cashout_callback_in_venmo_group_is_rejected() -> None:
    await seed_cashout()
    gateway = RecordingCashoutGateway()
    async with TestSessionFactory() as session:
        result = await CashoutTelegramService(
            session,
            gateway=gateway,
        ).handle_callback_query(
            query_id="q1",
            callback_data=encode_callback_data(1, CashoutCallbackAction.FULL),
            telegram_chat_id=VENMO_GROUP_ID,
            telegram_user_id=9001,
            telegram_username="operator",
            message_id=555,
        )
    assert result.status == "not_found"
    async with TestSessionFactory() as session:
        stored = await session.get(CashoutRequest, 1)
    assert stored is not None
    assert stored.status == CashoutStatus.SENT


@pytest.mark.asyncio
async def test_legacy_persisted_cashout_callback_and_edits_keep_stored_chat() -> None:
    await seed_cashout(telegram_chat_id=LEGACY_SHARED_GROUP_ID, telegram_message_id=12015)
    gateway = RecordingCashoutGateway()
    async with TestSessionFactory() as session:
        result = await CashoutTelegramService(
            session,
            gateway=gateway,
        ).handle_callback_query(
            query_id="q-legacy",
            callback_data=encode_callback_data(1, CashoutCallbackAction.FULL),
            telegram_chat_id=LEGACY_SHARED_GROUP_ID,
            telegram_user_id=9001,
            telegram_username="operator",
            message_id=12015,
        )
    assert result.status == "completed_full"
    assert gateway.edits[-1]["chat_id"] == LEGACY_SHARED_GROUP_ID
    assert gateway.edits[-1]["message_id"] == 12015
    async with TestSessionFactory() as session:
        stored = await session.get(CashoutRequest, 1)
    assert stored is not None
    assert stored.telegram_chat_id == LEGACY_SHARED_GROUP_ID


@pytest.mark.asyncio
async def test_inquiry_stays_on_cashout_group_when_venmo_is_split() -> None:
    async with TestSessionFactory() as session:
        chat_id = InquiryService(session)._cashout_chat_id()
    assert chat_id == CASHOUT_GROUP_ID
    assert chat_id != VENMO_GROUP_ID
    settings = Settings()
    assert settings.telegram_group_id == PAYMENT_GROUP_ID
    assert settings.telegram_group_target == PAYMENT_GROUP_ID

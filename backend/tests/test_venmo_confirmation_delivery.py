from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
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
    VenmoConfirmationRequest,
    VenmoConfirmationStatus,
)
from app.services.venmo_confirmation import VenmoConfirmationService
from app.telegram import venmo_confirmation_delivery
from app.telegram.cashout_bot.api import TelegramBotApiError, TelegramBotFailureClass
from app.telegram.cashout_bot.updates import handle_cashout_bot_update
from app.telegram.venmo_confirmation_delivery import (
    deliver_next_due_venmo_confirmation,
    send_confirmation_attempt_with_retries,
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


class SequencedGateway:
    outcomes: list[int | Exception | None] = []
    sent: list[dict[str, object]] = []

    async def __aenter__(self) -> SequencedGateway:
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
        outcome = self.outcomes.pop(0) if self.outcomes else 9001
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def gateway_factory() -> SequencedGateway:
    return SequencedGateway()


async def no_sleep(_: float) -> None:
    return None


def user(
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


STAFF = user(42, "sarah", UserRole.STAFF, coadmin_id=10)


@pytest_asyncio.fixture(autouse=True)
async def reset_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[None]:
    monkeypatch.setenv("INQUIRY_MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-1001234567890")
    monkeypatch.setattr(venmo_confirmation_delivery, "SessionFactory", TestSessionFactory)
    get_settings.cache_clear()
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSessionFactory() as session:
        session.add_all(
            [
                user(42, "sarah", UserRole.STAFF, coadmin_id=10),
                user(10, "default_coadmin", UserRole.COADMIN),
            ]
        )
        await session.commit()
    SequencedGateway.outcomes = []
    SequencedGateway.sent = []
    yield
    app.dependency_overrides.clear()
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()
    get_settings.cache_clear()


async def seed_attempt(tmp_path: Path) -> tuple[int, int]:
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


async def run_delivery(
    request_id: int,
    attempt_id: int,
    *,
    retry_delays_seconds: tuple[float, ...] = (0, 2, 5, 10, 20, 40),
) -> None:
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
            retry_delays_seconds=retry_delays_seconds,
            jitter_ratio=0,
        )


@pytest.mark.asyncio
async def test_first_timeout_second_send_succeeds_same_attempt(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    SequencedGateway.outcomes = [
        TelegramBotApiError(
            "Telegram Bot API request timed out",
            failure_class=TelegramBotFailureClass.RETRYABLE,
        ),
        9002,
    ]

    await run_delivery(request_id, attempt_id)

    async with TestSessionFactory() as session:
        request = await session.get(VenmoConfirmationRequest, request_id)
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
        attempts = list(
            await session.scalars(
                select(VenmoConfirmationAttempt).where(
                    VenmoConfirmationAttempt.request_id == request_id
                )
            )
        )
    assert request is not None
    assert attempt is not None
    assert request.status == VenmoConfirmationStatus.PENDING
    assert attempt.status == VenmoConfirmationAttemptStatus.POSTED
    assert attempt.telegram_message_id == 9002
    assert attempt.delivery_attempts == 2
    assert attempt.last_error is None
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_multiple_timeouts_then_success(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    timeout = TelegramBotApiError(
        "Telegram Bot API request timed out",
        failure_class=TelegramBotFailureClass.RETRYABLE,
    )
    SequencedGateway.outcomes = [timeout, timeout, timeout, 9004]

    await run_delivery(request_id, attempt_id)

    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.POSTED
    assert attempt.delivery_attempts == 4


@pytest.mark.asyncio
async def test_retry_budget_exhausted_marks_failed(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    SequencedGateway.outcomes = [
        TelegramBotApiError("temporary 500", failure_class=TelegramBotFailureClass.RETRYABLE)
        for _ in range(6)
    ]

    await run_delivery(request_id, attempt_id)

    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.FAILED_TO_SEND
    assert attempt.last_error == "temporary 500"
    assert attempt.next_retry_at is None


@pytest.mark.asyncio
async def test_telegram_500_retries_automatically(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    SequencedGateway.outcomes = [
        TelegramBotApiError(
            "Internal Server Error",
            failure_class=TelegramBotFailureClass.RETRYABLE,
            status_code=500,
        ),
        9002,
    ]

    await run_delivery(request_id, attempt_id)

    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.POSTED
    assert attempt.delivery_attempts == 2


@pytest.mark.asyncio
async def test_telegram_429_records_retry_after(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    SequencedGateway.outcomes = [
        TelegramBotApiError(
            "rate limited",
            failure_class=TelegramBotFailureClass.RETRYABLE,
            status_code=429,
            retry_after_seconds=45,
        ),
        9002,
    ]

    await run_delivery(request_id, attempt_id)

    async with TestSessionFactory() as session:
        events = list(
            await session.scalars(
                select(VenmoConfirmationEvent)
                .where(VenmoConfirmationEvent.event_type == VenmoConfirmationEventType.FAILURE)
                .order_by(VenmoConfirmationEvent.id)
            )
        )
    assert events
    assert events[0].payload["delay_seconds"] == 45.0


@pytest.mark.asyncio
async def test_permanent_400_does_not_retry(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    SequencedGateway.outcomes = [
        TelegramBotApiError(
            "Bad Request: chat not found",
            failure_class=TelegramBotFailureClass.NON_RETRYABLE,
            status_code=400,
        )
    ]

    await run_delivery(request_id, attempt_id)

    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
    assert len(SequencedGateway.sent) == 1
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.FAILED_TO_SEND
    assert attempt.last_error == "Bad Request: chat not found"


@pytest.mark.asyncio
async def test_forbidden_bot_failure_does_not_retry(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    SequencedGateway.outcomes = [
        TelegramBotApiError(
            "Forbidden: bot was kicked",
            failure_class=TelegramBotFailureClass.CONFIGURATION,
            status_code=403,
        )
    ]

    await run_delivery(request_id, attempt_id)

    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
    assert len(SequencedGateway.sent) == 1
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.FAILED_TO_SEND


@pytest.mark.asyncio
async def test_known_message_id_prevents_further_retry(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    async with TestSessionFactory() as session, session.begin():
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
        assert attempt is not None
        attempt.telegram_chat_id = -1001234567890
        attempt.telegram_message_id = 777
        attempt.status = VenmoConfirmationAttemptStatus.POSTED

    await run_delivery(request_id, attempt_id)

    assert SequencedGateway.sent == []


@pytest.mark.asyncio
async def test_active_retry_blocks_manual_resend(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    async with TestSessionFactory() as session, session.begin():
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
        assert attempt is not None
        attempt.next_retry_at = datetime.now(UTC) + timedelta(seconds=30)
        attempt.last_error = "Telegram Bot API request timed out"

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as session:
            yield session

    async def override_user() -> User:
        return STAFF

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_current_user] = override_user

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(f"/api/venmo-confirmations/{request_id}/resend")

    assert response.status_code == 409
    async with TestSessionFactory() as session:
        attempts = list(
            await session.scalars(
                select(VenmoConfirmationAttempt).where(
                    VenmoConfirmationAttempt.request_id == request_id
                )
            )
        )
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_due_retry_survives_restart_worker_pickup(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    async with TestSessionFactory() as session, session.begin():
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
        assert attempt is not None
        attempt.delivery_attempts = 1
        attempt.last_error = "Telegram Bot API request timed out"
        attempt.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)

    SequencedGateway.outcomes = [9010]

    processed = await deliver_next_due_venmo_confirmation(gateway_factory=gateway_factory)

    assert processed is True
    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.POSTED
    assert attempt.telegram_message_id == 9010
    assert attempt.delivery_attempts == 2


@pytest.mark.asyncio
async def test_message_caption_reconciliation_links_ambiguous_timeout(
    tmp_path: Path,
) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    async with TestSessionFactory() as session, session.begin():
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
        assert attempt is not None
        attempt.delivery_attempts = 1
        attempt.last_error = "Telegram Bot API request timed out"
        attempt.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)

    await handle_cashout_bot_update(
        type(
            "Update",
            (),
            {
                "update_id": 1,
                "payload": {
                    "message": {
                        "message_id": 8801,
                        "chat": {"id": -1001234567890, "type": "supergroup"},
                        "from": {"id": 999, "username": "bot"},
                        "caption": (
                            f"Confirmation request #{request_id}\n"
                            "Attempt #1\n"
                            "Was this evidence received/accepted?"
                        ),
                    }
                },
            },
        )(),
        gateway=object(),
        session_factory=TestSessionFactory,
        report=lambda _: None,
    )

    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, attempt_id)
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.POSTED
    assert attempt.telegram_message_id == 8801
    assert attempt.next_retry_at is None


@pytest.mark.asyncio
async def test_initial_venmo_card_preserves_note(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    SequencedGateway.outcomes = [9001]
    await run_delivery(request_id, attempt_id, retry_delays_seconds=(0,))
    caption = str(SequencedGateway.sent[-1]["caption"])
    assert "Note: Player paid" in caption
    assert "Requested By: sarah" in caption
    assert caption.count("Note:") == 1


@pytest.mark.asyncio
async def test_send_again_preserves_note_from_db(tmp_path: Path) -> None:
    request_id, attempt_id = await seed_attempt(tmp_path)
    SequencedGateway.outcomes = [9001, 9002]
    await run_delivery(request_id, attempt_id, retry_delays_seconds=(0,))
    async with TestSessionFactory() as session, session.begin():
        service = VenmoConfirmationService(session)
        await service.mark_attempt_not_received(
            attempt_id=attempt_id,
            coadmin_id=10,
            telegram_user_id=700,
            telegram_username="ayush",
            display_name="Ayush",
        )
        request = await session.get(VenmoConfirmationRequest, request_id)
        media = await session.get(MediaAsset, 1)
        assert request is not None
        assert media is not None
        attempt_two = await service.create_attempt(request_id=request.id, coadmin_id=10)
        await send_confirmation_attempt_with_retries(
            service=service,
            request=request,
            media=media,
            attempt=attempt_two,
            event_type=VenmoConfirmationEventType.RESEND_POSTED,
            gateway_factory=gateway_factory,
            sleep=no_sleep,
            retry_delays_seconds=(0,),
            jitter_ratio=0,
        )
    caption = str(SequencedGateway.sent[-1]["caption"])
    assert "Attempt #2" in caption
    assert "Note: Player paid" in caption
    assert "Requested By: sarah" in caption
    assert caption.count("Note: Player paid") == 1

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.base import Base
from app.maintenance.recover_legacy_venmo_confirmations import (
    format_recovery_rows,
    recover_legacy_venmo_confirmations,
)
from app.models.inquiry_message import (
    InquiryDirection,
    InquiryMediaDownloadStatus,
    InquiryMediaType,
    InquiryMessage,
    InquiryMessageSource,
)
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
from app.telegram.cashout_bot.api import TelegramBotFailureClass

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


def user(
    user_id: int,
    username: str,
    role: UserRole,
) -> User:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    return User(
        id=user_id,
        username=username,
        password_hash="not-used",
        role=role,
        is_active=True,
        staff_color="#2563EB",
        created_at=timestamp,
        updated_at=timestamp,
    )


@pytest_asyncio.fixture(autouse=True)
async def reset_database(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-1001234567890")
    get_settings.cache_clear()
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSessionFactory() as session, session.begin():
        session.add(user(10, "default_coadmin", UserRole.COADMIN))
        session.add(
            MediaAsset(
                id=1,
                coadmin_id=10,
                storage_key="evidence/venmo.png",
                original_filename="venmo.png",
                mime_type="image/png",
                size_bytes=8,
                checksum_sha256="a" * 64,
                created_by_user_id=10,
            )
        )
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()
    get_settings.cache_clear()


async def seed_legacy_attempt(
    *,
    request_id: int = 100,
    attempt_id: int = 501,
    attempt_number: int = 1,
    request_status: VenmoConfirmationStatus = VenmoConfirmationStatus.PENDING,
    attempt_status: VenmoConfirmationAttemptStatus = (
        VenmoConfirmationAttemptStatus.FAILED_TO_SEND
    ),
    last_error: str | None = "Telegram Bot API request timed out",
    telegram_message_id: int | None = None,
    next_retry_at: datetime | None = None,
    delivery_lease_until: datetime | None = None,
) -> None:
    async with TestSessionFactory() as session, session.begin():
        session.add(
            VenmoConfirmationRequest(
                id=request_id,
                coadmin_id=10,
                screenshot_media_asset_id=1,
                status=request_status,
            )
        )
        session.add(
            VenmoConfirmationAttempt(
                id=attempt_id,
                request_id=request_id,
                attempt_number=attempt_number,
                status=attempt_status,
                telegram_chat_id=-1001234567890 if telegram_message_id else None,
                telegram_message_id=telegram_message_id,
                last_error=last_error,
                next_retry_at=next_retry_at,
                delivery_lease_until=delivery_lease_until,
            )
        )


@pytest.mark.asyncio
async def test_old_timeout_failure_becomes_scheduled_for_retry() -> None:
    now = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    await seed_legacy_attempt()

    rows = await recover_legacy_venmo_confirmations(
        apply=True,
        session_factory=TestSessionFactory,
        now=now,
    )

    assert len(rows) == 1
    assert rows[0].action == "schedule_retry"
    assert rows[0].classification == TelegramBotFailureClass.RETRYABLE.value
    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, 501)
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.PENDING
    assert attempt.next_retry_at == now.replace(tzinfo=None)
    assert attempt.delivery_attempts == 1
    assert attempt.delivery_lease_until is None


@pytest.mark.asyncio
async def test_permanent_failure_is_skipped() -> None:
    await seed_legacy_attempt(last_error="Bad Request: chat not found")

    rows = await recover_legacy_venmo_confirmations(
        apply=True,
        session_factory=TestSessionFactory,
    )

    assert rows[0].action == "skip"
    assert rows[0].reason == "non_retryable_error"
    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, 501)
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.FAILED_TO_SEND


@pytest.mark.asyncio
async def test_already_posted_attempt_is_skipped() -> None:
    await seed_legacy_attempt(
        attempt_status=VenmoConfirmationAttemptStatus.POSTED,
        telegram_message_id=777,
    )

    rows = await recover_legacy_venmo_confirmations(
        apply=True,
        session_factory=TestSessionFactory,
    )

    assert rows == []


@pytest.mark.asyncio
async def test_local_caption_reconciliation_links_existing_message() -> None:
    now = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    await seed_legacy_attempt()
    async with TestSessionFactory() as session, session.begin():
        session.add(
            InquiryMessage(
                id=900,
                telegram_chat_id=-1001234567890,
                telegram_message_id=8801,
                telegram_sender_id=99,
                caption=(
                    "Confirmation request #100\n"
                    "Attempt #1\n"
                    "Was this evidence received/accepted?"
                ),
                message_date=now,
                direction=InquiryDirection.OUTBOUND,
                message_source=InquiryMessageSource.TELEGRAM_EXTERNAL,
                media_type=InquiryMediaType.PHOTO,
                media_download_status=InquiryMediaDownloadStatus.NOT_APPLICABLE,
            )
        )

    rows = await recover_legacy_venmo_confirmations(
        apply=True,
        session_factory=TestSessionFactory,
        now=now,
    )

    assert rows[0].action == "link_existing_message"
    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, 501)
        events = list(
            await session.scalars(
                select(VenmoConfirmationEvent).where(
                    VenmoConfirmationEvent.event_type
                    == VenmoConfirmationEventType.LEGACY_RECOVERY
                )
            )
        )
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.POSTED
    assert attempt.telegram_message_id == 8801
    assert attempt.next_retry_at is None
    assert len(events) == 1
    assert events[0].payload["action"] == "link_existing_message"


@pytest.mark.asyncio
async def test_request_with_newer_attempt_is_skipped() -> None:
    await seed_legacy_attempt()
    async with TestSessionFactory() as session, session.begin():
        session.add(
            VenmoConfirmationAttempt(
                id=502,
                request_id=100,
                attempt_number=2,
                status=VenmoConfirmationAttemptStatus.PENDING,
            )
        )

    rows = await recover_legacy_venmo_confirmations(
        apply=True,
        session_factory=TestSessionFactory,
    )

    assert rows == []
    async with TestSessionFactory() as session:
        old_attempt = await session.get(VenmoConfirmationAttempt, 501)
    assert old_attempt is not None
    assert old_attempt.status == VenmoConfirmationAttemptStatus.FAILED_TO_SEND


@pytest.mark.asyncio
async def test_already_scheduled_attempt_is_idempotently_skipped() -> None:
    await seed_legacy_attempt(next_retry_at=datetime(2026, 8, 15, 10, 0, tzinfo=UTC))

    rows = await recover_legacy_venmo_confirmations(
        apply=True,
        session_factory=TestSessionFactory,
    )

    assert rows[0].action == "skip"
    assert rows[0].reason == "already_scheduled"


@pytest.mark.asyncio
async def test_active_lease_prevents_unsafe_recovery() -> None:
    await seed_legacy_attempt(
        delivery_lease_until=datetime(2026, 8, 15, 10, 1, tzinfo=UTC)
    )

    rows = await recover_legacy_venmo_confirmations(
        apply=True,
        session_factory=TestSessionFactory,
        now=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
    )

    assert rows[0].action == "skip"
    assert rows[0].reason == "active_lease"


@pytest.mark.asyncio
async def test_running_backfill_twice_creates_no_duplicate_scheduling() -> None:
    now = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    await seed_legacy_attempt()

    first = await recover_legacy_venmo_confirmations(
        apply=True,
        session_factory=TestSessionFactory,
        now=now,
    )
    second = await recover_legacy_venmo_confirmations(
        apply=True,
        session_factory=TestSessionFactory,
        now=now + timedelta(seconds=10),
    )

    assert first[0].action == "schedule_retry"
    assert second == []
    async with TestSessionFactory() as session:
        events = list(
            await session.scalars(
                select(VenmoConfirmationEvent).where(
                    VenmoConfirmationEvent.event_type
                    == VenmoConfirmationEventType.LEGACY_RECOVERY
                )
            )
        )
    assert len(events) == 1


@pytest.mark.asyncio
async def test_audit_event_is_written() -> None:
    now = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    await seed_legacy_attempt(last_error="Telegram Bot API request timed out")

    await recover_legacy_venmo_confirmations(
        apply=True,
        session_factory=TestSessionFactory,
        now=now,
    )

    async with TestSessionFactory() as session:
        event = await session.scalar(
            select(VenmoConfirmationEvent).where(
                VenmoConfirmationEvent.event_type
                == VenmoConfirmationEventType.LEGACY_RECOVERY
            )
        )
    assert event is not None
    assert event.payload["previous_attempt_status"] == "failed_to_send"
    assert event.payload["previous_error"] == "Telegram Bot API request timed out"
    assert event.payload["classification"] == "retryable"
    assert event.payload["scheduled_retry_at"] == now.isoformat()


@pytest.mark.asyncio
async def test_dry_run_prints_intended_action_without_mutation() -> None:
    await seed_legacy_attempt()

    rows = await recover_legacy_venmo_confirmations(
        apply=False,
        session_factory=TestSessionFactory,
        now=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
    )
    output = format_recovery_rows(rows, apply=False)

    assert "mode=dry-run" in output
    assert "request_id=100" in output
    assert "attempt_id=501" in output
    assert "classification=retryable" in output
    assert "action=schedule_retry" in output
    async with TestSessionFactory() as session:
        attempt = await session.get(VenmoConfirmationAttempt, 501)
    assert attempt is not None
    assert attempt.status == VenmoConfirmationAttemptStatus.FAILED_TO_SEND

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.notification import NotificationType
from app.models.user import User, UserRole
from app.models.venmo_confirmation import (
    VenmoConfirmationAttemptStatus,
    VenmoConfirmationEvent,
    VenmoConfirmationInquiryStatus,
    VenmoConfirmationStatus,
)
from app.services.notification import NotificationService
from app.services.venmo_confirmation import (
    VenmoConfirmationNotFoundError,
    VenmoConfirmationService,
)
from app.services.workflow_settings import (
    WorkflowSettingsAuthorizationError,
    WorkflowSettingsService,
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


ADMIN = make_user(1, "admin", UserRole.ADMIN)
COADMIN_A = make_user(10, "coadmin_a", UserRole.COADMIN)
COADMIN_B = make_user(11, "coadmin_b", UserRole.COADMIN)
STAFF_A = make_user(42, "sarah", UserRole.STAFF, coadmin_id=10)
STAFF_B = make_user(84, "alex", UserRole.STAFF, coadmin_id=11)


@pytest_asyncio.fixture(autouse=True)
async def reset_database() -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSessionFactory() as session:
        session.add_all(
            [
                make_user(1, "admin", UserRole.ADMIN),
                make_user(10, "coadmin_a", UserRole.COADMIN),
                make_user(11, "coadmin_b", UserRole.COADMIN),
                make_user(42, "sarah", UserRole.STAFF, coadmin_id=10),
                make_user(84, "alex", UserRole.STAFF, coadmin_id=11),
            ]
        )
        await session.commit()
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_coadmin_telegram_settings_are_scoped_by_owner() -> None:
    async with TestSessionFactory() as session, session.begin():
        service = WorkflowSettingsService(session)
        first = await service.upsert_for_coadmin(
            coadmin_id=10,
            cashout_group_id=-1001,
            venmo_confirmation_group_id=-1001,
            actor=ADMIN,
        )
        second = await service.upsert_for_coadmin(
            coadmin_id=11,
            cashout_group_id=-2001,
            venmo_confirmation_group_id=-2001,
            actor=ADMIN,
        )
        visible = await service.get_for_coadmin(coadmin_id=10, actor=STAFF_A)

    assert first.cashout_group_id == -1001
    assert first.venmo_confirmation_group_id == -1001
    assert second.cashout_group_id == -2001
    assert second.venmo_confirmation_group_id == -2001
    assert visible is not None
    assert visible.coadmin_id == 10
    async with TestSessionFactory() as session:
        with pytest.raises(WorkflowSettingsAuthorizationError):
            await WorkflowSettingsService(session).get_for_coadmin(
                coadmin_id=11,
                actor=STAFF_A,
            )


@pytest.mark.asyncio
async def test_coadmin_telegram_settings_allow_split_workflow_groups() -> None:
    async with TestSessionFactory() as session, session.begin():
        service = WorkflowSettingsService(session)
        stored = await service.upsert_for_coadmin(
            coadmin_id=10,
            cashout_group_id=-1001,
            venmo_confirmation_group_id=-1002,
            actor=ADMIN,
        )

    assert stored.cashout_group_id == -1001
    assert stored.venmo_confirmation_group_id == -1002


@pytest.mark.asyncio
async def test_venmo_request_persists_with_coadmin_and_staff_owner() -> None:
    async with TestSessionFactory() as session, session.begin():
        service = VenmoConfirmationService(session)
        media = await service.create_media_asset(
            coadmin_id=10,
            storage_key="venmo/10/original.png",
            original_filename="original.png",
            mime_type="image/png",
            size_bytes=1234,
            checksum_sha256="a" * 64,
            actor=STAFF_A,
        )
        request = await service.create_request(
            actor=STAFF_A,
            screenshot_media_asset_id=media.id,
            payment_note="Customer paid",
            metadata={"reference": "VENMO-1"},
        )

    assert request.coadmin_id == 10
    assert request.requested_by_staff_id == 42
    assert request.screenshot_media_asset_id == media.id
    assert request.status == VenmoConfirmationStatus.PENDING


@pytest.mark.asyncio
async def test_venmo_attempts_track_resend_history_without_new_request() -> None:
    async with TestSessionFactory() as session, session.begin():
        service = VenmoConfirmationService(session)
        media = await service.create_media_asset(
            coadmin_id=10,
            storage_key="venmo/10/resend.png",
            original_filename="resend.png",
            mime_type="image/png",
            size_bytes=999,
            checksum_sha256="b" * 64,
            actor=STAFF_A,
        )
        request = await service.create_request(
            actor=STAFF_A,
            screenshot_media_asset_id=media.id,
        )
        attempt_one = await service.create_attempt(request_id=request.id, coadmin_id=10)
        inquiry = await service.mark_attempt_not_received(
            attempt_id=attempt_one.id,
            coadmin_id=10,
        )
        attempt_two = await service.create_attempt(request_id=request.id, coadmin_id=10)
        confirmed = await service.mark_confirmed(
            attempt_id=attempt_two.id,
            coadmin_id=10,
            telegram_user_id=555,
            telegram_username="checker",
            display_name="Checker",
        )

    assert attempt_one.request_id == request.id
    assert attempt_one.attempt_number == 1
    assert attempt_one.status == VenmoConfirmationAttemptStatus.NOT_RECEIVED
    assert inquiry.request_id == request.id
    assert inquiry.source_attempt_id == attempt_one.id
    assert inquiry.status == VenmoConfirmationInquiryStatus.OPEN
    assert attempt_two.request_id == request.id
    assert attempt_two.attempt_number == 2
    assert attempt_two.status == VenmoConfirmationAttemptStatus.CONFIRMED
    assert confirmed.id == request.id
    assert confirmed.status == VenmoConfirmationStatus.CONFIRMED
    async with TestSessionFactory() as session:
        event_types = [
            event.event_type.value
            for event in (
                await session.scalars(
                    select(VenmoConfirmationEvent).order_by(VenmoConfirmationEvent.id)
                )
            ).all()
        ]
        notifications = await NotificationService(session).list_unread(STAFF_A)
    assert event_types == [
        "request_created",
        "attempt_created",
        "not_received",
        "inquiry_created",
        "attempt_created",
        "confirmed",
    ]
    assert len(notifications) == 1
    assert notifications[0].type == NotificationType.VENMO_CONFIRMATION_CONFIRMED


@pytest.mark.asyncio
async def test_venmo_inquiry_dismiss_preserves_history() -> None:
    async with TestSessionFactory() as session, session.begin():
        service = VenmoConfirmationService(session)
        media = await service.create_media_asset(
            coadmin_id=10,
            storage_key="venmo/10/dismiss.png",
            original_filename="dismiss.png",
            mime_type="image/png",
            size_bytes=100,
            checksum_sha256="c" * 64,
            actor=STAFF_A,
        )
        request = await service.create_request(
            actor=STAFF_A,
            screenshot_media_asset_id=media.id,
        )
        attempt = await service.create_attempt(request_id=request.id, coadmin_id=10)
        inquiry = await service.mark_attempt_not_received(
            attempt_id=attempt.id,
            coadmin_id=10,
        )
        dismissed = await service.dismiss_inquiry(
            inquiry_id=inquiry.id,
            coadmin_id=10,
            actor=STAFF_A,
        )

    assert dismissed.status == VenmoConfirmationInquiryStatus.DISMISSED
    assert dismissed.dismissed_at is not None
    assert dismissed.dismissed_by_staff_id == STAFF_A.id


@pytest.mark.asyncio
async def test_cross_coadmin_venmo_lookup_and_actions_fail_safely() -> None:
    async with TestSessionFactory() as session, session.begin():
        service = VenmoConfirmationService(session)
        media = await service.create_media_asset(
            coadmin_id=10,
            storage_key="venmo/10/scoped.png",
            original_filename="scoped.png",
            mime_type="image/png",
            size_bytes=100,
            checksum_sha256="d" * 64,
            actor=STAFF_A,
        )
        request = await service.create_request(
            actor=STAFF_A,
            screenshot_media_asset_id=media.id,
        )
        attempt = await service.create_attempt(request_id=request.id, coadmin_id=10)

    async with TestSessionFactory() as session:
        service = VenmoConfirmationService(session)
        with pytest.raises(VenmoConfirmationNotFoundError):
            await service.get_request_for_coadmin(request.id, 11)
        with pytest.raises(VenmoConfirmationNotFoundError):
            await service.mark_attempt_not_received(attempt_id=attempt.id, coadmin_id=11)

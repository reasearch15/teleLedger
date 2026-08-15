from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, or_, select

from app.db.repositories.base import BaseRepository
from app.models.media_asset import MediaAsset
from app.models.venmo_confirmation import (
    VenmoConfirmationAttempt,
    VenmoConfirmationAttemptStatus,
    VenmoConfirmationEvent,
    VenmoConfirmationInquiry,
    VenmoConfirmationRequest,
)


class VenmoConfirmationRepository(BaseRepository[VenmoConfirmationRequest]):
    """Scoped persistence helpers for Venmo confirmation workflows."""

    async def add_request(
        self,
        request: VenmoConfirmationRequest,
    ) -> VenmoConfirmationRequest:
        self._session.add(request)
        await self._session.flush()
        return request

    async def get_by_id(
        self,
        request_id: int,
        *,
        for_update: bool = False,
    ) -> VenmoConfirmationRequest | None:
        statement = select(VenmoConfirmationRequest).where(
            VenmoConfirmationRequest.id == request_id
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def count_requests_for_media_asset(self, media_asset_id: int) -> int:
        value = await self._session.scalar(
            select(func.count())
            .select_from(VenmoConfirmationRequest)
            .where(VenmoConfirmationRequest.screenshot_media_asset_id == media_asset_id)
        )
        return int(value or 0)

    async def get_request_for_coadmin(
        self,
        request_id: int,
        coadmin_id: int,
        *,
        for_update: bool = False,
    ) -> VenmoConfirmationRequest | None:
        statement = select(VenmoConfirmationRequest).where(
            VenmoConfirmationRequest.id == request_id,
            VenmoConfirmationRequest.coadmin_id == coadmin_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_requests_for_coadmin(
        self,
        coadmin_id: int,
        *,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: int | None = None,
    ) -> list[VenmoConfirmationRequest]:
        conditions = [VenmoConfirmationRequest.coadmin_id == coadmin_id]
        if cursor_created_at is not None and cursor_id is not None:
            conditions.append(
                or_(
                    VenmoConfirmationRequest.created_at < cursor_created_at,
                    and_(
                        VenmoConfirmationRequest.created_at == cursor_created_at,
                        VenmoConfirmationRequest.id < cursor_id,
                    ),
                )
            )
        statement = (
            select(VenmoConfirmationRequest)
            .where(*conditions)
            .order_by(
                VenmoConfirmationRequest.created_at.desc(),
                VenmoConfirmationRequest.id.desc(),
            )
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def list_requests(
        self,
        *,
        limit: int = 50,
        cursor_created_at: datetime | None = None,
        cursor_id: int | None = None,
    ) -> list[VenmoConfirmationRequest]:
        conditions = []
        if cursor_created_at is not None and cursor_id is not None:
            conditions.append(
                or_(
                    VenmoConfirmationRequest.created_at < cursor_created_at,
                    and_(
                        VenmoConfirmationRequest.created_at == cursor_created_at,
                        VenmoConfirmationRequest.id < cursor_id,
                    ),
                )
            )
        statement = (
            select(VenmoConfirmationRequest)
            .where(*conditions)
            .order_by(
                VenmoConfirmationRequest.created_at.desc(),
                VenmoConfirmationRequest.id.desc(),
            )
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def add_attempt(
        self,
        attempt: VenmoConfirmationAttempt,
    ) -> VenmoConfirmationAttempt:
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def next_attempt_number(self, request_id: int) -> int:
        value = await self._session.scalar(
            select(func.coalesce(func.max(VenmoConfirmationAttempt.attempt_number), 0)).where(
                VenmoConfirmationAttempt.request_id == request_id
            )
        )
        return int(value or 0) + 1

    async def get_attempt_for_coadmin(
        self,
        attempt_id: int,
        coadmin_id: int,
        *,
        for_update: bool = False,
    ) -> VenmoConfirmationAttempt | None:
        statement = (
            select(VenmoConfirmationAttempt)
            .join(
                VenmoConfirmationRequest,
                VenmoConfirmationAttempt.request_id == VenmoConfirmationRequest.id,
            )
            .where(
                VenmoConfirmationAttempt.id == attempt_id,
                VenmoConfirmationRequest.coadmin_id == coadmin_id,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_attempt_by_id(
        self,
        attempt_id: int,
        *,
        for_update: bool = False,
    ) -> VenmoConfirmationAttempt | None:
        statement = select(VenmoConfirmationAttempt).where(
            VenmoConfirmationAttempt.id == attempt_id
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_attempt_by_request_number(
        self,
        request_id: int,
        attempt_number: int,
        *,
        for_update: bool = False,
    ) -> VenmoConfirmationAttempt | None:
        statement = select(VenmoConfirmationAttempt).where(
            VenmoConfirmationAttempt.request_id == request_id,
            VenmoConfirmationAttempt.attempt_number == attempt_number,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def add_inquiry(
        self,
        inquiry: VenmoConfirmationInquiry,
    ) -> VenmoConfirmationInquiry:
        self._session.add(inquiry)
        await self._session.flush()
        return inquiry

    async def get_inquiry_for_coadmin(
        self,
        inquiry_id: int,
        coadmin_id: int,
        *,
        for_update: bool = False,
    ) -> VenmoConfirmationInquiry | None:
        statement = (
            select(VenmoConfirmationInquiry)
            .join(
                VenmoConfirmationRequest,
                VenmoConfirmationInquiry.request_id == VenmoConfirmationRequest.id,
            )
            .where(
                VenmoConfirmationInquiry.id == inquiry_id,
                VenmoConfirmationRequest.coadmin_id == coadmin_id,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def add_event(self, event: VenmoConfirmationEvent) -> VenmoConfirmationEvent:
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_attempts(
        self,
        request_id: int,
        *,
        for_update: bool = False,
    ) -> list[VenmoConfirmationAttempt]:
        statement = (
            select(VenmoConfirmationAttempt)
            .where(VenmoConfirmationAttempt.request_id == request_id)
            .order_by(VenmoConfirmationAttempt.attempt_number.asc())
        )
        if for_update:
            statement = statement.with_for_update()
        return list((await self._session.scalars(statement)).all())

    async def latest_attempt_for_request(
        self,
        request_id: int,
    ) -> VenmoConfirmationAttempt | None:
        statement = (
            select(VenmoConfirmationAttempt)
            .where(VenmoConfirmationAttempt.request_id == request_id)
            .order_by(
                VenmoConfirmationAttempt.attempt_number.desc(),
                VenmoConfirmationAttempt.id.desc(),
            )
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def claim_next_due_delivery(
        self,
        now: datetime,
    ) -> tuple[VenmoConfirmationRequest, MediaAsset, VenmoConfirmationAttempt] | None:
        statement = (
            select(VenmoConfirmationRequest, MediaAsset, VenmoConfirmationAttempt)
            .join(
                VenmoConfirmationAttempt,
                VenmoConfirmationAttempt.request_id == VenmoConfirmationRequest.id,
            )
            .join(
                MediaAsset,
                MediaAsset.id == VenmoConfirmationRequest.screenshot_media_asset_id,
            )
            .where(
                VenmoConfirmationAttempt.status == VenmoConfirmationAttemptStatus.PENDING,
                VenmoConfirmationAttempt.telegram_message_id.is_(None),
                or_(
                    VenmoConfirmationAttempt.next_retry_at.is_(None),
                    VenmoConfirmationAttempt.next_retry_at <= now,
                ),
                or_(
                    VenmoConfirmationAttempt.delivery_lease_until.is_(None),
                    VenmoConfirmationAttempt.delivery_lease_until <= now,
                ),
            )
            .order_by(
                VenmoConfirmationAttempt.next_retry_at.asc().nullsfirst(),
                VenmoConfirmationAttempt.id.asc(),
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return row[0], row[1], row[2]

    async def list_inquiries(self, request_id: int) -> list[VenmoConfirmationInquiry]:
        statement = (
            select(VenmoConfirmationInquiry)
            .where(VenmoConfirmationInquiry.request_id == request_id)
            .order_by(VenmoConfirmationInquiry.created_at.asc(), VenmoConfirmationInquiry.id.asc())
        )
        return list((await self._session.scalars(statement)).all())

    async def list_events(self, request_id: int) -> list[VenmoConfirmationEvent]:
        statement = (
            select(VenmoConfirmationEvent)
            .where(VenmoConfirmationEvent.request_id == request_id)
            .order_by(VenmoConfirmationEvent.created_at.asc(), VenmoConfirmationEvent.id.asc())
        )
        return list((await self._session.scalars(statement)).all())

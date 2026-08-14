from __future__ import annotations

from sqlalchemy import func, select

from app.db.repositories.base import BaseRepository
from app.models.venmo_confirmation import (
    VenmoConfirmationAttempt,
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

    async def get_by_id(self, request_id: int) -> VenmoConfirmationRequest | None:
        statement = select(VenmoConfirmationRequest).where(
            VenmoConfirmationRequest.id == request_id
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

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
        offset: int = 0,
    ) -> list[VenmoConfirmationRequest]:
        statement = (
            select(VenmoConfirmationRequest)
            .where(VenmoConfirmationRequest.coadmin_id == coadmin_id)
            .order_by(
                VenmoConfirmationRequest.created_at.desc(),
                VenmoConfirmationRequest.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.scalars(statement)).all())

    async def list_requests(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[VenmoConfirmationRequest]:
        statement = (
            select(VenmoConfirmationRequest)
            .order_by(
                VenmoConfirmationRequest.created_at.desc(),
                VenmoConfirmationRequest.id.desc(),
            )
            .limit(limit)
            .offset(offset)
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

    async def list_attempts(self, request_id: int) -> list[VenmoConfirmationAttempt]:
        statement = (
            select(VenmoConfirmationAttempt)
            .where(VenmoConfirmationAttempt.request_id == request_id)
            .order_by(VenmoConfirmationAttempt.attempt_number.asc())
        )
        return list((await self._session.scalars(statement)).all())

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

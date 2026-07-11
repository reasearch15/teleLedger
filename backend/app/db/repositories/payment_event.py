from dataclasses import dataclass
from datetime import date, datetime, time
from time import perf_counter
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, load_only
from sqlalchemy.sql.elements import ColumnElement

from app.db.repositories.base import BaseRepository
from app.models.payment_dismissal import PaymentEventCoadminDismissal
from app.models.payment_event import PaymentEvent, PaymentStatus
from app.models.telegram_message import TelegramMessage
from app.models.user import User


@dataclass(frozen=True, slots=True)
class StaffIdentity:
    """Minimal stable staff identity embedded in payment list rows."""

    id: int
    username: str
    color: str


@dataclass(frozen=True, slots=True)
class PaymentListItem:
    """Payment row with optional claimant and completer identities."""

    payment: PaymentEvent
    claimed_by_staff: StaffIdentity | None
    completed_by_staff: StaffIdentity | None
    coadmin_dismissals: list["PaymentDismissalIdentity"]
    can_dismiss: bool = False
    eligible_coadmin_count: int = 0
    declined_coadmin_count: int = 0


@dataclass(frozen=True, slots=True)
class PaymentDismissalIdentity:
    """Coadmin dismissal identity embedded in payment list rows."""

    coadmin_id: int
    coadmin_username: str | None
    dismissed_by_staff_id: int | None
    dismissed_by_staff_username: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PaymentListPage:
    """One bounded result page plus measured database phases."""

    items: list[PaymentListItem]
    total: int | None
    has_more: bool
    connection_acquisition_ms: float
    list_query_ms: float
    count_query_ms: float
    has_more_ms: float


class PaymentEventRepository(BaseRepository[PaymentEvent]):
    """Persistence operations for parsed payment events."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def add(self, payment_event: PaymentEvent) -> PaymentEvent:
        """Stage a payment event and assign database-generated values."""
        self._session.add(payment_event)
        await self._session.flush()
        return payment_event

    async def get_by_id(self, payment_event_id: int) -> PaymentEvent | None:
        """Find a payment event by its internal identifier."""
        statement = select(PaymentEvent).where(PaymentEvent.id == payment_event_id)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, payment_event_id: int) -> PaymentEvent | None:
        """Lock and return one payment event for a state transition."""
        statement = (
            select(PaymentEvent)
            .where(PaymentEvent.id == payment_event_id)
            .with_for_update()
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_payments(
        self,
        *,
        status: PaymentStatus | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 7,
        offset: int = 0,
        include_total: bool = False,
        visible_to_staff_id: int | None = None,
        visible_to_coadmin_id: int | None = None,
        history_staff_id: int | None = None,
        active_only: bool = False,
        history_only: bool = False,
        include_dismissals: bool = False,
        exclude_all_coadmins_declined: bool = False,
        exclude_declined_review_dismissed: bool = False,
        admin_review_only: bool = False,
    ) -> PaymentListPage:
        """Return one lightweight page without counting unless requested."""
        conditions = self._list_conditions(
            status=status,
            search=search,
            date_from=date_from,
            date_to=date_to,
            visible_to_staff_id=visible_to_staff_id,
            visible_to_coadmin_id=visible_to_coadmin_id,
            history_staff_id=history_staff_id,
            active_only=active_only,
            history_only=history_only,
            exclude_all_coadmins_declined=exclude_all_coadmins_declined,
            exclude_declined_review_dismissed=exclude_declined_review_dismissed,
            admin_review_only=admin_review_only,
        )
        claimed_staff = aliased(User, name="claimed_staff")
        completed_staff = aliased(User, name="completed_staff")
        statement = (
            select(
                PaymentEvent,
                claimed_staff.id,
                claimed_staff.username,
                claimed_staff.staff_color,
                completed_staff.id,
                completed_staff.username,
                completed_staff.staff_color,
            )
            .join(
                TelegramMessage,
                PaymentEvent.telegram_message_id == TelegramMessage.id,
            )
            .options(
                load_only(
                    PaymentEvent.id,
                    PaymentEvent.telegram_message_id,
                    PaymentEvent.recipient_tag,
                    PaymentEvent.amount,
                    PaymentEvent.payment_sender_name,
                    PaymentEvent.payment_datetime,
                    PaymentEvent.total_in,
                    PaymentEvent.total_out,
                    PaymentEvent.status,
                    PaymentEvent.claimed_by_staff_id,
                    PaymentEvent.claimed_at,
                    PaymentEvent.completed_by_staff_id,
                    PaymentEvent.completed_at,
                    PaymentEvent.parser_confidence,
                    PaymentEvent.all_coadmins_declined_at,
                    PaymentEvent.declined_review_dismissed_at,
                    PaymentEvent.created_at,
                    PaymentEvent.updated_at,
                )
            )
            .outerjoin(
                claimed_staff,
                PaymentEvent.claimed_by_staff_id == claimed_staff.id,
            )
            .outerjoin(
                completed_staff,
                PaymentEvent.completed_by_staff_id == completed_staff.id,
            )
            .where(*conditions)
        )
        statement = statement.order_by(*self._list_order_by(
            history_only=history_only,
            history_staff_id=history_staff_id,
            admin_review_only=admin_review_only,
        )).limit(limit + 1).offset(offset)

        acquisition_started_at = perf_counter()
        await self._session.connection()
        connection_acquisition_ms = (
            perf_counter() - acquisition_started_at
        ) * 1000

        list_started_at = perf_counter()
        rows = (await self._session.execute(statement)).all()
        list_query_ms = (perf_counter() - list_started_at) * 1000

        has_more_started_at = perf_counter()
        has_more = len(rows) > limit
        payment_ids = [row[0].id for row in rows[:limit]]
        dismissals_by_payment = (
            await self._dismissals_by_payment(payment_ids)
            if include_dismissals
            else {}
        )
        items = [
            PaymentListItem(
                payment=row[0],
                claimed_by_staff=(
                    StaffIdentity(id=row[1], username=row[2], color=row[3])
                    if row[1] is not None
                    else None
                ),
                completed_by_staff=(
                    StaffIdentity(id=row[4], username=row[5], color=row[6])
                    if row[4] is not None
                    else None
                ),
                coadmin_dismissals=dismissals_by_payment.get(row[0].id, []),
            )
            for row in rows[:limit]
        ]
        has_more_ms = (perf_counter() - has_more_started_at) * 1000

        count_query_ms = 0.0
        total: int | None = None
        if include_total:
            count_statement = (
                select(func.count(PaymentEvent.id))
                .select_from(PaymentEvent)
                .where(*conditions)
            )
            count_started_at = perf_counter()
            total = int(await self._session.scalar(count_statement) or 0)
            count_query_ms = (perf_counter() - count_started_at) * 1000

        return PaymentListPage(
            items=items,
            total=total,
            has_more=has_more,
            connection_acquisition_ms=connection_acquisition_ms,
            list_query_ms=list_query_ms,
            count_query_ms=count_query_ms,
            has_more_ms=has_more_ms,
        )

    async def _dismissals_by_payment(
        self,
        payment_ids: list[int],
    ) -> dict[int, list[PaymentDismissalIdentity]]:
        if not payment_ids:
            return {}
        coadmin = aliased(User, name="dismissal_coadmin")
        staff = aliased(User, name="dismissal_staff")
        statement = (
            select(
                PaymentEventCoadminDismissal.payment_event_id,
                PaymentEventCoadminDismissal.coadmin_id,
                coadmin.username,
                PaymentEventCoadminDismissal.dismissed_by_staff_id,
                staff.username,
                PaymentEventCoadminDismissal.created_at,
            )
            .outerjoin(coadmin, PaymentEventCoadminDismissal.coadmin_id == coadmin.id)
            .outerjoin(
                staff,
                PaymentEventCoadminDismissal.dismissed_by_staff_id == staff.id,
            )
            .where(PaymentEventCoadminDismissal.payment_event_id.in_(payment_ids))
            .order_by(PaymentEventCoadminDismissal.created_at.asc())
        )
        rows = (await self._session.execute(statement)).all()
        dismissals: dict[int, list[PaymentDismissalIdentity]] = {}
        for row in rows:
            dismissals.setdefault(row[0], []).append(
                PaymentDismissalIdentity(
                    coadmin_id=row[1],
                    coadmin_username=row[2],
                    dismissed_by_staff_id=row[3],
                    dismissed_by_staff_username=row[4],
                    created_at=row[5],
                )
            )
        return dismissals

    @staticmethod
    def _list_conditions(
        *,
        status: PaymentStatus | None,
        search: str | None,
        date_from: date | None,
        date_to: date | None,
        visible_to_staff_id: int | None,
        visible_to_coadmin_id: int | None,
        history_staff_id: int | None,
        active_only: bool,
        history_only: bool,
        exclude_all_coadmins_declined: bool,
        exclude_declined_review_dismissed: bool,
        admin_review_only: bool,
    ) -> tuple[ColumnElement[bool], ...]:
        """Build reusable list/count predicates without changing filter semantics."""
        conditions: list[ColumnElement[bool]] = []

        if status is not None:
            conditions.append(PaymentEvent.status == status)

        if visible_to_staff_id is not None:
            visible_pending_condition: ColumnElement[bool] = (
                PaymentEvent.status == PaymentStatus.PENDING
            )
            if visible_to_coadmin_id is not None:
                visible_pending_condition = (
                    visible_pending_condition
                    & ~exists()
                    .where(
                        PaymentEventCoadminDismissal.payment_event_id
                        == PaymentEvent.id
                    )
                    .where(
                        PaymentEventCoadminDismissal.coadmin_id
                        == visible_to_coadmin_id
                    )
                )
            conditions.append(
                or_(
                    visible_pending_condition,
                    (
                        (PaymentEvent.status == PaymentStatus.IN_PROGRESS)
                        & (
                            PaymentEvent.claimed_by_staff_id
                            == visible_to_staff_id
                        )
                    ),
                )
            )

        if history_staff_id is not None:
            conditions.append(
                or_(
                    PaymentEvent.claimed_by_staff_id == history_staff_id,
                    PaymentEvent.completed_by_staff_id == history_staff_id,
                )
            )

        if active_only:
            conditions.append(
                PaymentEvent.status.in_(
                    (PaymentStatus.PENDING, PaymentStatus.IN_PROGRESS)
                )
            )

        if history_only:
            conditions.append(
                or_(
                    PaymentEvent.claimed_by_staff_id.is_not(None),
                    PaymentEvent.completed_by_staff_id.is_not(None),
                )
            )

        if search:
            escaped_search = (
                search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            search_pattern = f"%{escaped_search}%"
            conditions.append(
                or_(
                    PaymentEvent.recipient_tag.ilike(search_pattern, escape="\\"),
                    PaymentEvent.payment_sender_name.ilike(search_pattern, escape="\\"),
                    PaymentEvent.raw_text.ilike(search_pattern, escape="\\"),
                )
            )

        if date_from is not None:
            conditions.append(
                PaymentEvent.payment_datetime >= datetime.combine(date_from, time.min)
            )

        if date_to is not None:
            inclusive_end = datetime.combine(date_to, time.max)
            conditions.append(PaymentEvent.payment_datetime <= inclusive_end)

        if exclude_all_coadmins_declined:
            conditions.append(PaymentEvent.all_coadmins_declined_at.is_(None))

        if exclude_declined_review_dismissed:
            conditions.append(PaymentEvent.declined_review_dismissed_at.is_(None))

        if admin_review_only:
            conditions.append(PaymentEvent.all_coadmins_declined_at.is_not(None))
            conditions.append(PaymentEvent.declined_review_dismissed_at.is_(None))

        return tuple(conditions)

    @staticmethod
    def _list_order_by(
        *,
        history_only: bool,
        history_staff_id: int | None,
        admin_review_only: bool = False,
    ) -> tuple[Any, ...]:
        """Choose list ordering based on whether this is a history query."""
        if admin_review_only:
            return (
                PaymentEvent.all_coadmins_declined_at.desc(),
                PaymentEvent.id.desc(),
            )
        if history_only or history_staff_id is not None:
            return (
                PaymentEvent.completed_at.desc().nulls_last(),
                PaymentEvent.updated_at.desc(),
                PaymentEvent.id.desc(),
            )
        return (
            TelegramMessage.received_at.desc(),
            PaymentEvent.payment_datetime.desc(),
            TelegramMessage.telegram_message_id.desc(),
            PaymentEvent.id.desc(),
        )

    async def flush(self, payment_event: PaymentEvent) -> None:
        """Flush changes and load the database-managed update timestamp."""
        await self._session.flush()
        await self._session.refresh(payment_event, attribute_names=["updated_at"])

    async def delete(self, payment_event: PaymentEvent) -> None:
        """Permanently remove one payment event and related cascade rows."""
        await self._session.delete(payment_event)
        await self._session.flush()

    async def count_coadmin_dismissals_for_active_coadmins(
        self,
        payment_event_id: int,
        active_coadmin_ids: list[int],
    ) -> int:
        """Count unique eligible coadmin dismissals for one payment."""
        if not active_coadmin_ids:
            return 0
        result = await self._session.scalar(
            select(
                func.count(func.distinct(PaymentEventCoadminDismissal.coadmin_id))
            ).where(
                PaymentEventCoadminDismissal.payment_event_id == payment_event_id,
                PaymentEventCoadminDismissal.coadmin_id.in_(active_coadmin_ids),
            )
        )
        return int(result or 0)

    async def declined_coadmin_ids_by_payment(
        self,
        payment_ids: list[int],
        eligible_coadmin_ids: list[int],
    ) -> dict[int, list[int]]:
        """Return unique declining coadmin IDs keyed by payment."""
        if not payment_ids or not eligible_coadmin_ids:
            return {}
        statement = (
            select(
                PaymentEventCoadminDismissal.payment_event_id,
                PaymentEventCoadminDismissal.coadmin_id,
            )
            .where(
                PaymentEventCoadminDismissal.payment_event_id.in_(payment_ids),
                PaymentEventCoadminDismissal.coadmin_id.in_(eligible_coadmin_ids),
            )
            .distinct()
        )
        rows = (await self._session.execute(statement)).all()
        declined_by_payment: dict[int, list[int]] = {}
        for payment_id, coadmin_id in rows:
            declined_by_payment.setdefault(payment_id, []).append(coadmin_id)
        return declined_by_payment

    async def get_by_telegram_message_id(
        self,
        telegram_message_id: int,
    ) -> list[PaymentEvent]:
        """Return payment events extracted from a stored Telegram message."""
        statement = (
            select(PaymentEvent)
            .where(PaymentEvent.telegram_message_id == telegram_message_id)
            .order_by(PaymentEvent.id)
        )
        result = await self._session.scalars(statement)
        return list(result)

    async def get_one_by_telegram_message_id(
        self,
        telegram_message_id: int,
    ) -> PaymentEvent | None:
        """Return the single payment extracted from a stored Telegram message."""
        statement = select(PaymentEvent).where(
            PaymentEvent.telegram_message_id == telegram_message_id
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

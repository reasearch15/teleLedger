from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.cashout import CashoutRequest, CashoutStatus
from app.models.ledger_adjustment import LedgerAdjustment, LedgerAdjustmentType
from app.models.payment_event import PaymentEvent, PaymentStatus
from app.models.staff_settlement import (
    StaffSettlement,
    StaffSettlementAuditAction,
    StaffSettlementAuditLog,
    StaffSettlementStatus,
)
from app.models.user import User, UserRole
from app.services.base import ApplicationService
from app.websocket.events import LiveEventType, event_broker

ZERO = Decimal("0.00")
BUSINESS_TIMEZONE = "Asia/Kathmandu"
BUSINESS_ZONE = ZoneInfo(BUSINESS_TIMEZONE)
CALCULATION_OPEN_BALANCE = "open_balance"
CALCULATION_CUSTOM_RANGE = "custom_range"
CALCULATION_ROLLING_ACTIVITY = "rolling_activity"
REQUEST_MODE_OPEN_BALANCE = "open_balance"
REQUEST_MODE_LAST_12_HOURS = "last_12_hours"
REQUEST_MODE_CUSTOM_RANGE = "custom_range"
ROLLING_HOURS = 12


class LedgerAuthorizationError(Exception):
    """Raised when a non-admin attempts a ledger operation."""


class LedgerStateConflictError(Exception):
    """Raised when a settlement workflow transition is invalid."""


class SettlementNotFoundError(Exception):
    """Raised when a settlement record does not exist."""


class StaffNotFoundError(Exception):
    """Raised when a staff user does not exist."""


class CoadminNotFoundError(Exception):
    """Raised when a coadmin user does not exist."""


@dataclass(frozen=True, slots=True)
class LedgerDateRange:
    start: datetime | None
    end_exclusive: datetime | None
    calculation_type: str = CALCULATION_OPEN_BALANCE
    timezone: str = BUSINESS_TIMEZONE
    period_start: datetime | None = None
    period_end: datetime | None = None
    includes_settled: bool = False
    rolling_hours: int | None = None
    generated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LedgerHistoryCursor:
    created_at: datetime
    row_id: int


@dataclass(frozen=True, slots=True)
class LedgerItem:
    staff_id: int
    staff_username: str
    staff_color: str
    coadmin_id: int | None
    coadmin_username: str
    payment_total: Decimal
    adjustment_total: Decimal
    total_in: Decimal
    total_out: Decimal
    settled_amount: Decimal
    net: Decimal
    payments_count: int
    cashouts_count: int
    settlements_count: int


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    payment_total: Decimal
    adjustment_total: Decimal
    total_in: Decimal
    total_out: Decimal
    settled_amount: Decimal
    net: Decimal


@dataclass(frozen=True, slots=True)
class CoadminLedgerSummary:
    coadmin_id: int | None
    coadmin_username: str
    payment_total: Decimal
    adjustment_total: Decimal
    total_in: Decimal
    total_out: Decimal
    settled_amount: Decimal
    net: Decimal
    staff_count: int
    payments_count: int
    cashouts_count: int
    settlements_count: int


@dataclass(frozen=True, slots=True)
class LedgerReport:
    items: list[LedgerItem]
    coadmin_summaries: list[CoadminLedgerSummary]
    summary: LedgerSummary
    calculation_type: str
    timezone: str
    period_start: datetime | None
    period_end: datetime | None
    includes_settled: bool
    rolling_hours: int | None
    generated_at: datetime | None


@dataclass(frozen=True, slots=True)
class LedgerPaymentDrilldownItem:
    id: int
    staff_id: int
    staff_username: str
    amount: Decimal
    status: PaymentStatus
    completed_at: datetime | None
    settlement_id: int | None
    recipient_tag: str
    payment_sender_name: str


@dataclass(frozen=True, slots=True)
class LedgerCashoutDrilldownItem:
    id: int
    staff_id: int
    staff_username: str
    amount: Decimal
    status: CashoutStatus
    created_at: datetime
    completed_at: datetime | None
    settlement_id: int | None
    player_tag: str
    request_number: str | None


@dataclass(frozen=True, slots=True)
class LedgerAdjustmentDrilldownItem:
    id: int
    staff_id: int
    staff_username: str
    amount_delta: Decimal
    created_at: datetime
    settlement_id: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class LedgerDrilldownReport:
    payments: list[LedgerPaymentDrilldownItem]
    cashouts: list[LedgerCashoutDrilldownItem]
    adjustments: list[LedgerAdjustmentDrilldownItem]
    calculation_type: str
    timezone: str
    period_start: datetime | None
    period_end: datetime | None
    includes_settled: bool
    rolling_hours: int | None
    generated_at: datetime | None


@dataclass(frozen=True, slots=True)
class SettlementRecord:
    settlement: StaffSettlement
    staff_username: str
    staff_color: str
    coadmin_username: str | None
    created_by_admin_username: str
    claimed_by_admin_username: str | None
    completed_by_admin_username: str | None
    payment_ids: list[int]
    cashout_ids: list[int]
    adjustment_ids: list[int]


@dataclass(frozen=True, slots=True)
class LedgerAdjustmentRecord:
    adjustment: LedgerAdjustment
    staff_username: str
    staff_color: str
    created_by_admin_username: str | None


@dataclass(frozen=True, slots=True)
class LedgerAdjustmentListPage:
    items: list[LedgerAdjustmentRecord]
    has_more: bool
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class SettlementListPage:
    items: list[SettlementRecord]
    has_more: bool
    next_cursor: str | None


class LedgerService(ApplicationService):
    """Admin-only staff ledger aggregation and settlement workflow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_ledger(
        self,
        *,
        date_from: date | None,
        date_to: date | None,
        calculation_mode: str | None = None,
        actor: User,
    ) -> LedgerReport:
        self._require_admin(actor)
        date_range = self._report_date_range(
            date_from=date_from,
            date_to=date_to,
            calculation_mode=calculation_mode,
        )
        return await self._ledger_report(date_range)

    async def get_ledger_drilldown(
        self,
        *,
        date_from: date | None,
        date_to: date | None,
        calculation_mode: str | None = None,
        staff_id: int | None = None,
        actor: User,
    ) -> LedgerDrilldownReport:
        self._require_admin(actor)
        date_range = self._report_date_range(
            date_from=date_from,
            date_to=date_to,
            calculation_mode=calculation_mode,
        )
        if date_range.calculation_type == CALCULATION_OPEN_BALANCE:
            raise LedgerStateConflictError(
                "Drilldown is available for historical activity modes."
            )
        return LedgerDrilldownReport(
            payments=await self._payment_drilldown(date_range, staff_id),
            cashouts=await self._cashout_drilldown(date_range, staff_id),
            adjustments=await self._adjustment_drilldown(date_range, staff_id),
            calculation_type=date_range.calculation_type,
            timezone=date_range.timezone,
            period_start=date_range.period_start,
            period_end=date_range.period_end,
            includes_settled=date_range.includes_settled,
            rolling_hours=date_range.rolling_hours,
            generated_at=date_range.generated_at,
        )

    async def settle_staff(
        self,
        *,
        staff_id: int,
        date_from: date | None,
        date_to: date | None,
        notes: str | None,
        actor: User,
    ) -> StaffSettlement:
        self._require_admin(actor)
        date_range = self._date_range(date_from, date_to)
        async with self._session.begin():
            staff = await self._get_staff_for_update(staff_id)
            item = await self._ledger_item_for_staff(staff, date_range)
            if item.net <= ZERO:
                raise LedgerStateConflictError("Nothing to settle.")
            payment_ids = await self._unsettled_payment_ids_for_staff(
                staff.id,
                date_range,
            )
            cashout_ids = await self._unsettled_cashout_ids_for_staff(
                staff.id,
                date_range,
            )
            adjustment_ids = await self._unsettled_adjustment_ids_for_staff(
                staff.id,
                date_range,
            )

            now = datetime.now(UTC)
            settlement = StaffSettlement(
                staff_id=staff.id,
                amount=item.net,
                status=StaffSettlementStatus.DONE,
                completed_by_admin_id=actor.id,
                completed_at=now,
                created_by_admin_id=actor.id,
                notes=notes,
            )
            self._session.add(settlement)
            await self._session.flush()
            if payment_ids:
                await self._session.execute(
                    update(PaymentEvent)
                    .where(
                        PaymentEvent.id.in_(payment_ids),
                        PaymentEvent.settlement_id.is_(None),
                    )
                    .values(settlement_id=settlement.id)
                )
            if cashout_ids:
                await self._session.execute(
                    update(CashoutRequest)
                    .where(
                        CashoutRequest.id.in_(cashout_ids),
                        CashoutRequest.settlement_id.is_(None),
                    )
                    .values(settlement_id=settlement.id)
                )
            if adjustment_ids:
                await self._session.execute(
                    update(LedgerAdjustment)
                    .where(
                        LedgerAdjustment.id.in_(adjustment_ids),
                        LedgerAdjustment.settlement_id.is_(None),
                    )
                    .values(settlement_id=settlement.id)
                )
            await self._add_audit(
                settlement,
                actor=actor,
                action=StaffSettlementAuditAction.CREATED,
                previous_status=None,
                metadata={
                    "date_from": date_from.isoformat() if date_from else None,
                    "date_to": date_to.isoformat() if date_to else None,
                    "total_in": str(item.total_in),
                    "total_out": str(item.total_out),
                    "net_settled": str(item.net),
                    "payment_ids": payment_ids,
                    "cashout_ids": cashout_ids,
                    "adjustment_ids": adjustment_ids,
                },
            )
            await self._add_audit(
                settlement,
                actor=actor,
                action=StaffSettlementAuditAction.DONE,
                previous_status=StaffSettlementStatus.PENDING,
                metadata={"immediate": True},
            )
            await self._session.refresh(settlement)
        await event_broker.publish(
            LiveEventType.SETTLEMENT_CREATED,
            settlement_id=settlement.id,
        )
        await event_broker.publish(
            LiveEventType.SETTLEMENT_DONE,
            settlement_id=settlement.id,
        )
        await event_broker.publish(LiveEventType.LEDGER_CHANGED)
        return settlement

    async def settle_coadmin(
        self,
        *,
        coadmin_id: int,
        date_from: date | None,
        date_to: date | None,
        notes: str | None,
        actor: User,
    ) -> StaffSettlement:
        self._require_admin(actor)
        date_range = self._date_range(date_from, date_to)
        async with self._session.begin():
            coadmin = await self._get_coadmin_for_update(coadmin_id)
            staff_rows = (
                await self._session.execute(
                    select(User)
                    .where(
                        User.role == UserRole.STAFF,
                        User.is_active.is_(True),
                        User.coadmin_id == coadmin.id,
                    )
                    .order_by(User.username.asc())
                    .with_for_update()
                )
            ).scalars().all()
            staff_ids = [staff.id for staff in staff_rows]
            if not staff_ids:
                raise LedgerStateConflictError("Nothing to settle.")

            items = [
                await self._ledger_item_for_staff(staff, date_range, coadmin.username)
                for staff in staff_rows
            ]
            amount = self._money(sum((item.net for item in items), ZERO))
            if amount <= ZERO:
                raise LedgerStateConflictError("Nothing to settle.")

            payment_ids = await self._unsettled_payment_ids_for_staff_ids(
                staff_ids,
                date_range,
            )
            cashout_ids = await self._unsettled_cashout_ids_for_staff_ids(
                staff_ids,
                date_range,
            )
            adjustment_ids = await self._unsettled_adjustment_ids_for_staff_ids(
                staff_ids,
                date_range,
            )

            now = datetime.now(UTC)
            settlement = StaffSettlement(
                staff_id=None,
                coadmin_id=coadmin.id,
                scope="coadmin",
                amount=amount,
                status=StaffSettlementStatus.DONE,
                completed_by_admin_id=actor.id,
                completed_at=now,
                created_by_admin_id=actor.id,
                notes=notes,
            )
            self._session.add(settlement)
            await self._session.flush()
            if payment_ids:
                await self._session.execute(
                    update(PaymentEvent)
                    .where(
                        PaymentEvent.id.in_(payment_ids),
                        PaymentEvent.settlement_id.is_(None),
                    )
                    .values(settlement_id=settlement.id)
                )
            if cashout_ids:
                await self._session.execute(
                    update(CashoutRequest)
                    .where(
                        CashoutRequest.id.in_(cashout_ids),
                        CashoutRequest.settlement_id.is_(None),
                    )
                    .values(settlement_id=settlement.id)
                )
            if adjustment_ids:
                await self._session.execute(
                    update(LedgerAdjustment)
                    .where(
                        LedgerAdjustment.id.in_(adjustment_ids),
                        LedgerAdjustment.settlement_id.is_(None),
                    )
                    .values(settlement_id=settlement.id)
                )
            await self._add_audit(
                settlement,
                actor=actor,
                action=StaffSettlementAuditAction.CREATED,
                previous_status=None,
                metadata={
                    "scope": "coadmin",
                    "coadmin_id": coadmin.id,
                    "coadmin_username": coadmin.username,
                    "staff_ids": staff_ids,
                    "date_from": date_from.isoformat() if date_from else None,
                    "date_to": date_to.isoformat() if date_to else None,
                    "total_in": str(sum((item.total_in for item in items), ZERO)),
                    "total_out": str(sum((item.total_out for item in items), ZERO)),
                    "net_settled": str(amount),
                    "payment_ids": payment_ids,
                    "cashout_ids": cashout_ids,
                    "adjustment_ids": adjustment_ids,
                },
            )
            await self._add_audit(
                settlement,
                actor=actor,
                action=StaffSettlementAuditAction.DONE,
                previous_status=StaffSettlementStatus.PENDING,
                metadata={"immediate": True, "scope": "coadmin"},
            )
            await self._session.refresh(settlement)
        await event_broker.publish(
            LiveEventType.SETTLEMENT_CREATED,
            settlement_id=settlement.id,
        )
        await event_broker.publish(
            LiveEventType.SETTLEMENT_DONE,
            settlement_id=settlement.id,
        )
        await event_broker.publish(LiveEventType.LEDGER_CHANGED)
        return settlement

    async def adjust_total_in(
        self,
        *,
        staff_id: int,
        new_total_in: Decimal,
        reason: str,
        actor: User,
    ) -> LedgerAdjustment:
        self._require_admin(actor)
        clean_reason = reason.strip()
        if not clean_reason:
            raise LedgerStateConflictError("Reason is required.")
        new_total_in = self._money(new_total_in)
        async with self._session.begin():
            staff = await self._get_staff_for_update(staff_id)
            item = await self._ledger_item_for_staff(
                staff,
                LedgerDateRange(start=None, end_exclusive=None),
            )
            previous_total_in = self._money(item.total_in)
            adjustment = LedgerAdjustment(
                staff_id=staff.id,
                type=LedgerAdjustmentType.TOTAL_IN_ADJUSTMENT,
                amount_delta=self._money(new_total_in - previous_total_in),
                previous_total_in=previous_total_in,
                new_total_in=new_total_in,
                reason=clean_reason,
                created_by_admin_id=actor.id,
            )
            self._session.add(adjustment)
            await self._session.flush()
            await self._session.refresh(adjustment)
        await event_broker.publish(LiveEventType.LEDGER_CHANGED)
        return adjustment

    async def list_adjustments(
        self,
        *,
        staff_id: int | None,
        coadmin_id: int | None,
        date_from: date | None,
        date_to: date | None,
        include_deleted: bool,
        limit: int,
        offset: int,
        cursor: str | None,
        actor: User,
    ) -> LedgerAdjustmentListPage:
        self._require_admin(actor)
        date_range = self._date_range(date_from, date_to)
        history_cursor = self._parse_history_cursor(cursor)
        staff = aliased(User, name="adjustment_staff")
        creator = aliased(User, name="adjustment_creator")
        conditions = []
        if staff_id is not None:
            conditions.append(LedgerAdjustment.staff_id == staff_id)
        elif not include_deleted:
            conditions.append(LedgerAdjustment.staff_id.is_not(None))
        if coadmin_id is not None:
            conditions.append(staff.coadmin_id == coadmin_id)
        if date_range.start is not None:
            conditions.append(LedgerAdjustment.created_at >= date_range.start)
        if date_range.end_exclusive is not None:
            conditions.append(LedgerAdjustment.created_at < date_range.end_exclusive)
        if history_cursor is not None:
            conditions.append(
                or_(
                    LedgerAdjustment.created_at < history_cursor.created_at,
                    (
                        (LedgerAdjustment.created_at == history_cursor.created_at)
                        & (LedgerAdjustment.id < history_cursor.row_id)
                    ),
                )
            )
        statement = (
            select(
                LedgerAdjustment,
                staff.username,
                staff.staff_color,
                creator.username,
            )
            .outerjoin(staff, LedgerAdjustment.staff_id == staff.id)
            .outerjoin(creator, LedgerAdjustment.created_by_admin_id == creator.id)
            .where(*conditions)
            .order_by(LedgerAdjustment.created_at.desc(), LedgerAdjustment.id.desc())
            .limit(limit + 1)
        )
        if history_cursor is None and offset:
            statement = statement.offset(offset)
        rows = (await self._session.execute(statement)).all()
        items = [
            LedgerAdjustmentRecord(
                adjustment=row[0],
                staff_username=row[1] or "Deleted Staff",
                staff_color=row[2] or "#64748B",
                created_by_admin_username=row[3],
            )
            for row in rows[:limit]
        ]
        return LedgerAdjustmentListPage(
            items=items,
            has_more=len(rows) > limit,
            next_cursor=self._next_history_cursor(
                items[-1].adjustment.created_at,
                int(items[-1].adjustment.id),
            )
            if len(rows) > limit and items
            else None,
        )

    async def claim_settlement(
        self,
        settlement_id: int,
        actor: User,
    ) -> StaffSettlement:
        self._require_admin(actor)
        async with self._session.begin():
            settlement = await self._get_settlement_for_update(settlement_id)
            if settlement.status != StaffSettlementStatus.PENDING:
                raise LedgerStateConflictError("Only pending settlements can be claimed.")
            previous_status = settlement.status
            settlement.status = StaffSettlementStatus.CLAIMED
            settlement.claimed_by_admin_id = actor.id
            settlement.claimed_at = datetime.now(UTC)
            await self._add_audit(
                settlement,
                actor=actor,
                action=StaffSettlementAuditAction.CLAIMED,
                previous_status=previous_status,
                metadata=None,
            )
            await self._session.refresh(settlement)
        await event_broker.publish(LiveEventType.LEDGER_CHANGED)
        return settlement

    async def complete_settlement(
        self,
        settlement_id: int,
        actor: User,
    ) -> StaffSettlement:
        self._require_admin(actor)
        async with self._session.begin():
            settlement = await self._get_settlement_for_update(settlement_id)
            if settlement.status not in (
                StaffSettlementStatus.PENDING,
                StaffSettlementStatus.CLAIMED,
            ):
                raise LedgerStateConflictError(
                    "Only pending or claimed settlements can be completed."
                )
            previous_status = settlement.status
            settlement.status = StaffSettlementStatus.DONE
            settlement.completed_by_admin_id = actor.id
            settlement.completed_at = datetime.now(UTC)
            await self._add_audit(
                settlement,
                actor=actor,
                action=StaffSettlementAuditAction.DONE,
                previous_status=previous_status,
                metadata=None,
            )
            await self._session.refresh(settlement)
        await event_broker.publish(
            LiveEventType.SETTLEMENT_DONE,
            settlement_id=settlement.id,
        )
        await event_broker.publish(LiveEventType.LEDGER_CHANGED)
        return settlement

    async def cancel_settlement(
        self,
        settlement_id: int,
        actor: User,
    ) -> StaffSettlement:
        self._require_admin(actor)
        async with self._session.begin():
            settlement = await self._get_settlement_for_update(settlement_id)
            if settlement.status not in (
                StaffSettlementStatus.PENDING,
                StaffSettlementStatus.CLAIMED,
            ):
                raise LedgerStateConflictError(
                    "Only pending or claimed settlements can be cancelled."
                )
            previous_status = settlement.status
            settlement.status = StaffSettlementStatus.CANCELLED
            await self._add_audit(
                settlement,
                actor=actor,
                action=StaffSettlementAuditAction.CANCELLED,
                previous_status=previous_status,
                metadata=None,
            )
            await self._session.refresh(settlement)
        await event_broker.publish(LiveEventType.LEDGER_CHANGED)
        return settlement

    async def list_settlements(
        self,
        *,
        staff_id: int | None,
        coadmin_id: int | None,
        status: StaffSettlementStatus | None,
        date_from: date | None,
        date_to: date | None,
        include_deleted: bool,
        limit: int,
        offset: int,
        cursor: str | None,
        actor: User,
    ) -> SettlementListPage:
        self._require_admin(actor)
        date_range = self._date_range(date_from, date_to)
        history_cursor = self._parse_history_cursor(cursor)
        staff = aliased(User, name="settlement_staff")
        coadmin = aliased(User, name="settlement_coadmin")
        creator = aliased(User, name="settlement_creator")
        claimer = aliased(User, name="settlement_claimer")
        completer = aliased(User, name="settlement_completer")
        conditions = []
        if staff_id is not None:
            conditions.append(StaffSettlement.staff_id == staff_id)
        elif not include_deleted:
            conditions.append(
                or_(
                    StaffSettlement.staff_id.is_not(None),
                    StaffSettlement.scope == "coadmin",
                )
            )
        if status is not None:
            conditions.append(StaffSettlement.status == status)
        if coadmin_id is not None:
            conditions.append(
                or_(
                    StaffSettlement.coadmin_id == coadmin_id,
                    staff.coadmin_id == coadmin_id,
                )
            )
        if date_range.start is not None:
            conditions.append(StaffSettlement.completed_at >= date_range.start)
        if date_range.end_exclusive is not None:
            conditions.append(StaffSettlement.completed_at < date_range.end_exclusive)
        if history_cursor is not None:
            conditions.append(
                or_(
                    StaffSettlement.created_at < history_cursor.created_at,
                    (
                        (StaffSettlement.created_at == history_cursor.created_at)
                        & (StaffSettlement.id < history_cursor.row_id)
                    ),
                )
            )
        statement = (
            select(
                StaffSettlement,
                staff.username,
                staff.staff_color,
                coadmin.username,
                creator.username,
                claimer.username,
                completer.username,
            )
            .outerjoin(staff, StaffSettlement.staff_id == staff.id)
            .outerjoin(coadmin, StaffSettlement.coadmin_id == coadmin.id)
            .join(creator, StaffSettlement.created_by_admin_id == creator.id)
            .outerjoin(claimer, StaffSettlement.claimed_by_admin_id == claimer.id)
            .outerjoin(completer, StaffSettlement.completed_by_admin_id == completer.id)
            .where(*conditions)
            .order_by(StaffSettlement.created_at.desc(), StaffSettlement.id.desc())
            .limit(limit + 1)
        )
        if history_cursor is None and offset:
            statement = statement.offset(offset)
        rows = (await self._session.execute(statement)).all()
        transaction_ids = await self._settlement_transaction_ids(
            [int(row[0].id) for row in rows[:limit]]
        )
        items = [
            SettlementRecord(
                settlement=row[0],
                staff_username=row[1] or "Deleted Staff",
                staff_color=row[2] or "#64748B",
                coadmin_username=row[3],
                created_by_admin_username=row[4],
                claimed_by_admin_username=row[5],
                completed_by_admin_username=row[6],
                payment_ids=transaction_ids[int(row[0].id)][0],
                cashout_ids=transaction_ids[int(row[0].id)][1],
                adjustment_ids=transaction_ids[int(row[0].id)][2],
            )
            for row in rows[:limit]
        ]
        return SettlementListPage(
            items=items,
            has_more=len(rows) > limit,
            next_cursor=self._next_history_cursor(
                items[-1].settlement.created_at,
                int(items[-1].settlement.id),
            )
            if len(rows) > limit and items
            else None,
        )

    async def get_settlement_record(
        self,
        settlement_id: int,
        actor: User,
    ) -> SettlementRecord:
        self._require_admin(actor)
        staff = aliased(User, name="settlement_staff")
        coadmin = aliased(User, name="settlement_coadmin")
        creator = aliased(User, name="settlement_creator")
        claimer = aliased(User, name="settlement_claimer")
        completer = aliased(User, name="settlement_completer")
        row = (
            await self._session.execute(
                select(
                    StaffSettlement,
                    staff.username,
                    staff.staff_color,
                    coadmin.username,
                    creator.username,
                    claimer.username,
                    completer.username,
                )
                .outerjoin(staff, StaffSettlement.staff_id == staff.id)
                .outerjoin(coadmin, StaffSettlement.coadmin_id == coadmin.id)
                .join(creator, StaffSettlement.created_by_admin_id == creator.id)
                .outerjoin(claimer, StaffSettlement.claimed_by_admin_id == claimer.id)
                .outerjoin(
                    completer,
                    StaffSettlement.completed_by_admin_id == completer.id,
                )
                .where(StaffSettlement.id == settlement_id)
            )
        ).one_or_none()
        if row is None:
            raise SettlementNotFoundError(f"Settlement {settlement_id} was not found")
        transaction_ids = await self._settlement_transaction_ids([settlement_id])
        return SettlementRecord(
            settlement=row[0],
            staff_username=row[1] or "Deleted Staff",
            staff_color=row[2] or "#64748B",
            coadmin_username=row[3],
            created_by_admin_username=row[4],
            claimed_by_admin_username=row[5],
            completed_by_admin_username=row[6],
            payment_ids=transaction_ids[settlement_id][0],
            cashout_ids=transaction_ids[settlement_id][1],
            adjustment_ids=transaction_ids[settlement_id][2],
        )

    async def _ledger_report(self, date_range: LedgerDateRange) -> LedgerReport:
        coadmin = aliased(User, name="ledger_coadmin")
        staff_rows = (
            await self._session.execute(
                select(User.id, User.username, User.staff_color, User.coadmin_id, coadmin.username)
                .outerjoin(coadmin, User.coadmin_id == coadmin.id)
                .where(User.role == UserRole.STAFF, User.is_active.is_(True))
                .order_by(coadmin.username.asc().nulls_last(), User.username.asc())
            )
        ).all()
        payment_totals = await self._payment_totals(date_range)
        adjustment_totals = await self._adjustment_totals(date_range)
        cashout_totals = await self._cashout_totals(date_range)
        settlement_counts = await self._settlement_counts(date_range)
        items: list[LedgerItem] = []
        for staff_id, username, color, coadmin_id, coadmin_username in staff_rows:
            total_in, payments_count = payment_totals.get(staff_id, (ZERO, 0))
            payment_total = total_in
            adjustment_total = adjustment_totals.get(staff_id, ZERO)
            total_in += adjustment_total
            total_out, cashouts_count = cashout_totals.get(staff_id, (ZERO, 0))
            settlements_count = settlement_counts.get(staff_id, 0)
            items.append(
                LedgerItem(
                    staff_id=staff_id,
                    staff_username=username,
                    staff_color=color,
                    coadmin_id=coadmin_id,
                    coadmin_username=coadmin_username or "default_coadmin",
                    payment_total=payment_total,
                    adjustment_total=adjustment_total,
                    total_in=total_in,
                    total_out=total_out,
                    settled_amount=ZERO,
                    net=total_in - total_out,
                    payments_count=payments_count,
                    cashouts_count=cashouts_count,
                    settlements_count=settlements_count,
                )
            )
        coadmin_summaries = self._coadmin_summaries(items)
        return LedgerReport(
            items=items,
            coadmin_summaries=coadmin_summaries,
            summary=LedgerSummary(
                payment_total=sum((item.payment_total for item in items), ZERO),
                adjustment_total=sum((item.adjustment_total for item in items), ZERO),
                total_in=sum((item.total_in for item in items), ZERO),
                total_out=sum((item.total_out for item in items), ZERO),
                settled_amount=sum((item.settled_amount for item in items), ZERO),
                net=sum((item.net for item in items), ZERO),
            ),
            calculation_type=date_range.calculation_type,
            timezone=date_range.timezone,
            period_start=date_range.period_start,
            period_end=date_range.period_end,
            includes_settled=date_range.includes_settled,
            rolling_hours=date_range.rolling_hours,
            generated_at=date_range.generated_at,
        )

    async def _ledger_item_for_staff(
        self,
        staff: User,
        date_range: LedgerDateRange,
        coadmin_username: str | None = None,
    ) -> LedgerItem:
        payment_totals = await self._payment_totals(date_range, staff.id)
        adjustment_totals = await self._adjustment_totals(date_range, staff.id)
        cashout_totals = await self._cashout_totals(date_range, staff.id)
        settlement_counts = await self._settlement_counts(date_range, staff.id)
        total_in, payments_count = payment_totals.get(staff.id, (ZERO, 0))
        payment_total = total_in
        adjustment_total = adjustment_totals.get(staff.id, ZERO)
        total_in += adjustment_total
        total_out, cashouts_count = cashout_totals.get(staff.id, (ZERO, 0))
        settlements_count = settlement_counts.get(staff.id, 0)
        return LedgerItem(
            staff_id=staff.id,
            staff_username=staff.username,
            staff_color=staff.staff_color,
            coadmin_id=staff.coadmin_id,
            coadmin_username=coadmin_username or "default_coadmin",
            payment_total=payment_total,
            adjustment_total=adjustment_total,
            total_in=total_in,
            total_out=total_out,
            settled_amount=ZERO,
            net=total_in - total_out,
            payments_count=payments_count,
            cashouts_count=cashouts_count,
            settlements_count=settlements_count,
        )

    @staticmethod
    def _coadmin_summaries(items: list[LedgerItem]) -> list[CoadminLedgerSummary]:
        grouped: dict[tuple[int | None, str], list[LedgerItem]] = {}
        for item in items:
            grouped.setdefault((item.coadmin_id, item.coadmin_username), []).append(item)
        return [
            CoadminLedgerSummary(
                coadmin_id=coadmin_id,
                coadmin_username=coadmin_username,
                payment_total=sum((item.payment_total for item in coadmin_items), ZERO),
                adjustment_total=sum(
                    (item.adjustment_total for item in coadmin_items),
                    ZERO,
                ),
                total_in=sum((item.total_in for item in coadmin_items), ZERO),
                total_out=sum((item.total_out for item in coadmin_items), ZERO),
                settled_amount=sum(
                    (item.settled_amount for item in coadmin_items),
                    ZERO,
                ),
                net=sum((item.net for item in coadmin_items), ZERO),
                staff_count=len(coadmin_items),
                payments_count=sum(item.payments_count for item in coadmin_items),
                cashouts_count=sum(item.cashouts_count for item in coadmin_items),
                settlements_count=sum(
                    item.settlements_count for item in coadmin_items
                ),
            )
            for (coadmin_id, coadmin_username), coadmin_items in sorted(
                grouped.items(),
                key=lambda row: row[0][1].lower(),
            )
        ]

    async def _payment_totals(
        self,
        date_range: LedgerDateRange,
        staff_id: int | None = None,
    ) -> dict[int, tuple[Decimal, int]]:
        conditions = [
            PaymentEvent.status == PaymentStatus.DONE,
            PaymentEvent.completed_by_staff_id.is_not(None),
        ]
        if not date_range.includes_settled:
            conditions.append(PaymentEvent.settlement_id.is_(None))
        if staff_id is not None:
            conditions.append(PaymentEvent.completed_by_staff_id == staff_id)
        self._apply_completed_at_filter(conditions, PaymentEvent.completed_at, date_range)
        statement = (
            select(
                PaymentEvent.completed_by_staff_id,
                func.coalesce(func.sum(PaymentEvent.amount), ZERO),
                func.count(PaymentEvent.id),
            )
            .where(*conditions)
            .group_by(PaymentEvent.completed_by_staff_id)
        )
        return {
            int(row[0]): (self._money(row[1]), int(row[2]))
            for row in (await self._session.execute(statement)).all()
            if row[0] is not None
        }

    async def _cashout_totals(
        self,
        date_range: LedgerDateRange,
        staff_id: int | None = None,
    ) -> dict[int, tuple[Decimal, int]]:
        conditions = [
            CashoutRequest.status == CashoutStatus.COMPLETED,
        ]
        if not date_range.includes_settled:
            conditions.append(CashoutRequest.settlement_id.is_(None))
        if staff_id is not None:
            conditions.append(CashoutRequest.created_by_staff_id == staff_id)
        self._apply_completed_at_filter(conditions, CashoutRequest.completed_at, date_range)
        statement = (
            select(
                CashoutRequest.created_by_staff_id,
                func.coalesce(func.sum(CashoutRequest.amount), ZERO),
                func.count(CashoutRequest.id),
            )
            .where(*conditions)
            .group_by(CashoutRequest.created_by_staff_id)
        )
        return {
            int(row[0]): (self._money(row[1]), int(row[2]))
            for row in (await self._session.execute(statement)).all()
            if row[0] is not None
        }

    async def _adjustment_totals(
        self,
        date_range: LedgerDateRange,
        staff_id: int | None = None,
    ) -> dict[int, Decimal]:
        conditions = [
            LedgerAdjustment.type == LedgerAdjustmentType.TOTAL_IN_ADJUSTMENT,
            LedgerAdjustment.staff_id.is_not(None),
        ]
        if not date_range.includes_settled:
            conditions.append(LedgerAdjustment.settlement_id.is_(None))
        if staff_id is not None:
            conditions.append(LedgerAdjustment.staff_id == staff_id)
        self._apply_completed_at_filter(conditions, LedgerAdjustment.created_at, date_range)
        statement = (
            select(
                LedgerAdjustment.staff_id,
                func.coalesce(func.sum(LedgerAdjustment.amount_delta), ZERO),
            )
            .where(*conditions)
            .group_by(LedgerAdjustment.staff_id)
        )
        return {
            int(row[0]): self._money(row[1])
            for row in (await self._session.execute(statement)).all()
            if row[0] is not None
        }

    async def _payment_drilldown(
        self,
        date_range: LedgerDateRange,
        staff_id: int | None,
    ) -> list[LedgerPaymentDrilldownItem]:
        staff = aliased(User, name="payment_drilldown_staff")
        conditions = [
            PaymentEvent.status == PaymentStatus.DONE,
            PaymentEvent.completed_by_staff_id.is_not(None),
        ]
        if staff_id is not None:
            conditions.append(PaymentEvent.completed_by_staff_id == staff_id)
        self._apply_completed_at_filter(conditions, PaymentEvent.completed_at, date_range)
        rows = (
            await self._session.execute(
                select(
                    PaymentEvent.id,
                    PaymentEvent.completed_by_staff_id,
                    staff.username,
                    PaymentEvent.amount,
                    PaymentEvent.status,
                    PaymentEvent.completed_at,
                    PaymentEvent.settlement_id,
                    PaymentEvent.recipient_tag,
                    PaymentEvent.payment_sender_name,
                )
                .join(staff, PaymentEvent.completed_by_staff_id == staff.id)
                .where(*conditions)
                .order_by(PaymentEvent.completed_at.asc(), PaymentEvent.id.asc())
            )
        ).all()
        return [
            LedgerPaymentDrilldownItem(
                id=int(row[0]),
                staff_id=int(row[1]),
                staff_username=row[2],
                amount=self._money(row[3]),
                status=row[4],
                completed_at=row[5],
                settlement_id=row[6],
                recipient_tag=row[7],
                payment_sender_name=row[8],
            )
            for row in rows
            if row[1] is not None
        ]

    async def _cashout_drilldown(
        self,
        date_range: LedgerDateRange,
        staff_id: int | None,
    ) -> list[LedgerCashoutDrilldownItem]:
        staff = aliased(User, name="cashout_drilldown_staff")
        conditions = [
            CashoutRequest.status == CashoutStatus.COMPLETED,
            CashoutRequest.created_by_staff_id.is_not(None),
        ]
        if staff_id is not None:
            conditions.append(CashoutRequest.created_by_staff_id == staff_id)
        self._apply_completed_at_filter(conditions, CashoutRequest.completed_at, date_range)
        rows = (
            await self._session.execute(
                select(
                    CashoutRequest.id,
                    CashoutRequest.created_by_staff_id,
                    staff.username,
                    CashoutRequest.amount,
                    CashoutRequest.status,
                    CashoutRequest.created_at,
                    CashoutRequest.completed_at,
                    CashoutRequest.settlement_id,
                    CashoutRequest.player_tag,
                    CashoutRequest.request_number,
                )
                .join(staff, CashoutRequest.created_by_staff_id == staff.id)
                .where(*conditions)
                .order_by(CashoutRequest.completed_at.asc(), CashoutRequest.id.asc())
            )
        ).all()
        return [
            LedgerCashoutDrilldownItem(
                id=int(row[0]),
                staff_id=int(row[1]),
                staff_username=row[2],
                amount=self._money(row[3]),
                status=row[4],
                created_at=row[5],
                completed_at=row[6],
                settlement_id=row[7],
                player_tag=row[8],
                request_number=row[9],
            )
            for row in rows
            if row[1] is not None
        ]

    async def _adjustment_drilldown(
        self,
        date_range: LedgerDateRange,
        staff_id: int | None,
    ) -> list[LedgerAdjustmentDrilldownItem]:
        staff = aliased(User, name="adjustment_drilldown_staff")
        conditions = [
            LedgerAdjustment.type == LedgerAdjustmentType.TOTAL_IN_ADJUSTMENT,
            LedgerAdjustment.staff_id.is_not(None),
        ]
        if staff_id is not None:
            conditions.append(LedgerAdjustment.staff_id == staff_id)
        self._apply_completed_at_filter(conditions, LedgerAdjustment.created_at, date_range)
        rows = (
            await self._session.execute(
                select(
                    LedgerAdjustment.id,
                    LedgerAdjustment.staff_id,
                    staff.username,
                    LedgerAdjustment.amount_delta,
                    LedgerAdjustment.created_at,
                    LedgerAdjustment.settlement_id,
                    LedgerAdjustment.reason,
                )
                .join(staff, LedgerAdjustment.staff_id == staff.id)
                .where(*conditions)
                .order_by(LedgerAdjustment.created_at.asc(), LedgerAdjustment.id.asc())
            )
        ).all()
        return [
            LedgerAdjustmentDrilldownItem(
                id=int(row[0]),
                staff_id=int(row[1]),
                staff_username=row[2],
                amount_delta=self._money(row[3]),
                created_at=row[4],
                settlement_id=row[5],
                reason=row[6],
            )
            for row in rows
            if row[1] is not None
        ]

    async def _settlement_counts(
        self,
        date_range: LedgerDateRange,
        staff_id: int | None = None,
    ) -> dict[int, int]:
        conditions = [StaffSettlement.status == StaffSettlementStatus.DONE]
        if staff_id is not None:
            conditions.append(StaffSettlement.staff_id == staff_id)
        self._apply_completed_at_filter(conditions, StaffSettlement.completed_at, date_range)
        statement = (
            select(
                StaffSettlement.staff_id,
                func.count(StaffSettlement.id),
            )
            .where(*conditions)
            .group_by(StaffSettlement.staff_id)
        )
        return {
            int(row[0]): int(row[1])
            for row in (await self._session.execute(statement)).all()
            if row[0] is not None
        }

    async def _unsettled_payment_ids_for_staff(
        self,
        staff_id: int,
        date_range: LedgerDateRange,
    ) -> list[int]:
        conditions = [
            PaymentEvent.status == PaymentStatus.DONE,
            PaymentEvent.completed_by_staff_id == staff_id,
            PaymentEvent.settlement_id.is_(None),
        ]
        self._apply_completed_at_filter(conditions, PaymentEvent.completed_at, date_range)
        statement = (
            select(PaymentEvent.id)
            .where(*conditions)
            .order_by(PaymentEvent.completed_at.asc(), PaymentEvent.id.asc())
            .with_for_update()
        )
        return [int(row) for row in await self._session.scalars(statement)]

    async def _unsettled_payment_ids_for_staff_ids(
        self,
        staff_ids: list[int],
        date_range: LedgerDateRange,
    ) -> list[int]:
        if not staff_ids:
            return []
        conditions = [
            PaymentEvent.status == PaymentStatus.DONE,
            PaymentEvent.completed_by_staff_id.in_(staff_ids),
            PaymentEvent.settlement_id.is_(None),
        ]
        self._apply_completed_at_filter(conditions, PaymentEvent.completed_at, date_range)
        statement = (
            select(PaymentEvent.id)
            .where(*conditions)
            .order_by(PaymentEvent.completed_at.asc(), PaymentEvent.id.asc())
            .with_for_update()
        )
        return [int(row) for row in await self._session.scalars(statement)]

    async def _unsettled_cashout_ids_for_staff(
        self,
        staff_id: int,
        date_range: LedgerDateRange,
    ) -> list[int]:
        conditions = [
            CashoutRequest.status == CashoutStatus.COMPLETED,
            CashoutRequest.created_by_staff_id == staff_id,
            CashoutRequest.settlement_id.is_(None),
        ]
        self._apply_completed_at_filter(conditions, CashoutRequest.completed_at, date_range)
        statement = (
            select(CashoutRequest.id)
            .where(*conditions)
            .order_by(CashoutRequest.completed_at.asc(), CashoutRequest.id.asc())
            .with_for_update()
        )
        return [int(row) for row in await self._session.scalars(statement)]

    async def _unsettled_cashout_ids_for_staff_ids(
        self,
        staff_ids: list[int],
        date_range: LedgerDateRange,
    ) -> list[int]:
        if not staff_ids:
            return []
        conditions = [
            CashoutRequest.status == CashoutStatus.COMPLETED,
            CashoutRequest.created_by_staff_id.in_(staff_ids),
            CashoutRequest.settlement_id.is_(None),
        ]
        self._apply_completed_at_filter(conditions, CashoutRequest.completed_at, date_range)
        statement = (
            select(CashoutRequest.id)
            .where(*conditions)
            .order_by(CashoutRequest.completed_at.asc(), CashoutRequest.id.asc())
            .with_for_update()
        )
        return [int(row) for row in await self._session.scalars(statement)]

    async def _unsettled_adjustment_ids_for_staff(
        self,
        staff_id: int,
        date_range: LedgerDateRange,
    ) -> list[int]:
        conditions = [
            LedgerAdjustment.type == LedgerAdjustmentType.TOTAL_IN_ADJUSTMENT,
            LedgerAdjustment.staff_id == staff_id,
            LedgerAdjustment.settlement_id.is_(None),
        ]
        self._apply_completed_at_filter(conditions, LedgerAdjustment.created_at, date_range)
        statement = (
            select(LedgerAdjustment.id)
            .where(*conditions)
            .order_by(LedgerAdjustment.created_at.asc(), LedgerAdjustment.id.asc())
            .with_for_update()
        )
        return [int(row) for row in await self._session.scalars(statement)]

    async def _unsettled_adjustment_ids_for_staff_ids(
        self,
        staff_ids: list[int],
        date_range: LedgerDateRange,
    ) -> list[int]:
        if not staff_ids:
            return []
        conditions = [
            LedgerAdjustment.type == LedgerAdjustmentType.TOTAL_IN_ADJUSTMENT,
            LedgerAdjustment.staff_id.in_(staff_ids),
            LedgerAdjustment.settlement_id.is_(None),
        ]
        self._apply_completed_at_filter(conditions, LedgerAdjustment.created_at, date_range)
        statement = (
            select(LedgerAdjustment.id)
            .where(*conditions)
            .order_by(LedgerAdjustment.created_at.asc(), LedgerAdjustment.id.asc())
            .with_for_update()
        )
        return [int(row) for row in await self._session.scalars(statement)]

    async def _settlement_transaction_ids(
        self,
        settlement_ids: list[int],
    ) -> dict[int, tuple[list[int], list[int], list[int]]]:
        transaction_ids: dict[int, tuple[list[int], list[int], list[int]]] = {
            settlement_id: ([], [], []) for settlement_id in settlement_ids
        }
        if not settlement_ids:
            return transaction_ids
        payment_rows = (
            await self._session.execute(
                select(PaymentEvent.settlement_id, PaymentEvent.id)
                .where(PaymentEvent.settlement_id.in_(settlement_ids))
                .order_by(PaymentEvent.id.asc())
            )
        ).all()
        for settlement_id, payment_id in payment_rows:
            if settlement_id is not None:
                transaction_ids[int(settlement_id)][0].append(int(payment_id))
        cashout_rows = (
            await self._session.execute(
                select(CashoutRequest.settlement_id, CashoutRequest.id)
                .where(CashoutRequest.settlement_id.in_(settlement_ids))
                .order_by(CashoutRequest.id.asc())
            )
        ).all()
        for settlement_id, cashout_id in cashout_rows:
            if settlement_id is not None:
                transaction_ids[int(settlement_id)][1].append(int(cashout_id))
        adjustment_rows = (
            await self._session.execute(
                select(LedgerAdjustment.settlement_id, LedgerAdjustment.id)
                .where(LedgerAdjustment.settlement_id.in_(settlement_ids))
                .order_by(LedgerAdjustment.id.asc())
            )
        ).all()
        for settlement_id, adjustment_id in adjustment_rows:
            if settlement_id is not None:
                transaction_ids[int(settlement_id)][2].append(int(adjustment_id))
        return transaction_ids

    async def _get_staff_for_update(self, staff_id: int) -> User:
        staff = (
            await self._session.execute(
                select(User)
                .where(User.id == staff_id, User.role == UserRole.STAFF)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if staff is None:
            raise StaffNotFoundError(f"Staff user {staff_id} was not found")
        return staff

    async def _get_coadmin_for_update(self, coadmin_id: int) -> User:
        coadmin = (
            await self._session.execute(
                select(User)
                .where(User.id == coadmin_id, User.role == UserRole.COADMIN)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if coadmin is None:
            raise CoadminNotFoundError(f"Coadmin user {coadmin_id} was not found")
        return coadmin

    async def _get_settlement_for_update(self, settlement_id: int) -> StaffSettlement:
        settlement = (
            await self._session.execute(
                select(StaffSettlement).where(StaffSettlement.id == settlement_id).with_for_update()
            )
        ).scalar_one_or_none()
        if settlement is None:
            raise SettlementNotFoundError(f"Settlement {settlement_id} was not found")
        return settlement

    async def _add_audit(
        self,
        settlement: StaffSettlement,
        *,
        actor: User,
        action: StaffSettlementAuditAction,
        previous_status: StaffSettlementStatus | None,
        metadata: dict[str, object] | None,
    ) -> None:
        self._session.add(
            StaffSettlementAuditLog(
                settlement_id=settlement.id,
                actor_user_id=actor.id,
                action=action,
                previous_status=previous_status,
                new_status=settlement.status,
                metadata_json=metadata,
            )
        )
        await self._session.flush()

    @staticmethod
    def _date_range(
        date_from: date | None,
        date_to: date | None,
        *,
        historical_activity: bool = False,
    ) -> LedgerDateRange:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise LedgerStateConflictError("date_from must not be after date_to")
        period_start = (
            datetime.combine(date_from, time.min, tzinfo=BUSINESS_ZONE)
            if date_from is not None
            else None
        )
        period_end = (
            datetime.combine(
                date_to + timedelta(days=1),
                time.min,
                tzinfo=BUSINESS_ZONE,
            )
            if date_to is not None
            else None
        )
        start = period_start.astimezone(UTC) if period_start is not None else None
        end_exclusive = (
            period_end.astimezone(UTC) if period_end is not None
            else None
        )
        return LedgerDateRange(
            start=start,
            end_exclusive=end_exclusive,
            calculation_type=(
                CALCULATION_CUSTOM_RANGE
                if historical_activity
                else CALCULATION_OPEN_BALANCE
            ),
            period_start=period_start,
            period_end=period_end,
            includes_settled=historical_activity,
            generated_at=None,
        )

    def _report_date_range(
        self,
        *,
        date_from: date | None,
        date_to: date | None,
        calculation_mode: str | None,
    ) -> LedgerDateRange:
        mode = calculation_mode or (
            REQUEST_MODE_CUSTOM_RANGE
            if date_from is not None or date_to is not None
            else REQUEST_MODE_OPEN_BALANCE
        )
        if mode == REQUEST_MODE_OPEN_BALANCE:
            return self._date_range(date_from, date_to)
        if mode == REQUEST_MODE_CUSTOM_RANGE:
            return self._date_range(date_from, date_to, historical_activity=True)
        if mode == REQUEST_MODE_LAST_12_HOURS:
            generated_at = self._now_utc()
            start = generated_at - timedelta(hours=ROLLING_HOURS)
            return LedgerDateRange(
                start=start,
                end_exclusive=generated_at,
                calculation_type=CALCULATION_ROLLING_ACTIVITY,
                period_start=start.astimezone(BUSINESS_ZONE),
                period_end=generated_at.astimezone(BUSINESS_ZONE),
                includes_settled=True,
                rolling_hours=ROLLING_HOURS,
                generated_at=generated_at,
            )
        raise LedgerStateConflictError("Invalid calculation_mode.")

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _parse_history_cursor(cursor: str | None) -> LedgerHistoryCursor | None:
        if not cursor:
            return None
        try:
            raw_created_at, raw_row_id = cursor.split("|", 1)
            created_at = datetime.fromisoformat(raw_created_at)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            return LedgerHistoryCursor(created_at=created_at, row_id=int(raw_row_id))
        except (TypeError, ValueError) as error:
            raise LedgerStateConflictError("Invalid cursor.") from error

    @staticmethod
    def _next_history_cursor(created_at: datetime, row_id: int) -> str:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return f"{created_at.isoformat()}|{row_id}"

    @staticmethod
    def _apply_completed_at_filter(
        conditions: list[Any],
        column: Any,
        date_range: LedgerDateRange,
    ) -> None:
        if date_range.start is not None:
            conditions.append(column >= date_range.start)
        if date_range.end_exclusive is not None:
            conditions.append(column < date_range.end_exclusive)

    @staticmethod
    def _money(value: object) -> Decimal:
        if isinstance(value, Decimal):
            return value.quantize(Decimal("0.01"))
        return Decimal(str(value)).quantize(Decimal("0.01"))

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.role != UserRole.ADMIN:
            raise LedgerAuthorizationError("Administrator access is required.")

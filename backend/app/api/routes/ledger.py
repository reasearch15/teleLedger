from datetime import date
from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.dependencies import CurrentUser, DatabaseSession
from app.models.staff_settlement import StaffSettlementStatus
from app.schemas.ledger import (
    CreateLedgerAdjustmentRequest,
    CreateSettlementRequest,
    LedgerAdjustmentListResponse,
    LedgerAdjustmentResponse,
    LedgerItemResponse,
    LedgerResponse,
    LedgerSummaryResponse,
    SettlementListResponse,
    SettlementResponse,
)
from app.services.ledger import (
    LedgerAdjustmentListPage,
    LedgerAdjustmentRecord,
    LedgerAuthorizationError,
    LedgerReport,
    LedgerService,
    LedgerStateConflictError,
    SettlementListPage,
    SettlementNotFoundError,
    SettlementRecord,
    StaffNotFoundError,
)

router = APIRouter(prefix="/api/admin", tags=["admin ledger"])
SettlementId = Annotated[int, Path(gt=0)]
StaffId = Annotated[int, Path(gt=0)]


@router.get("/ledger", response_model=LedgerResponse)
async def get_admin_ledger(
    session: DatabaseSession,
    current_user: CurrentUser,
    date_from: date | None = None,
    date_to: date | None = None,
) -> LedgerResponse:
    service = LedgerService(session)
    try:
        report = await service.get_ledger(
            date_from=date_from,
            date_to=date_to,
            actor=current_user,
        )
    except Exception as error:
        _raise_ledger_error(error)
    return _serialize_ledger(report)


@router.post(
    "/ledger/staff/{staff_id}/settlements",
    response_model=SettlementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_staff_settlement(
    staff_id: StaffId,
    request: CreateSettlementRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
    date_from: date | None = None,
    date_to: date | None = None,
) -> SettlementResponse:
    service = LedgerService(session)
    try:
        settlement = await service.settle_staff(
            staff_id=staff_id,
            date_from=date_from,
            date_to=date_to,
            notes=request.notes,
            actor=current_user,
        )
        record = await service.get_settlement_record(settlement.id, current_user)
    except Exception as error:
        _raise_ledger_error(error)
    return _serialize_settlement(record)


@router.post(
    "/ledger/staff/{staff_id}/adjustments",
    response_model=LedgerAdjustmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_total_in_adjustment(
    staff_id: StaffId,
    request: CreateLedgerAdjustmentRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> LedgerAdjustmentResponse:
    service = LedgerService(session)
    try:
        adjustment = await service.adjust_total_in(
            staff_id=staff_id,
            new_total_in=request.new_total_in,
            reason=request.reason,
            actor=current_user,
        )
        page = await service.list_adjustments(
            staff_id=staff_id,
            date_from=None,
            date_to=None,
            include_deleted=False,
            limit=1,
            offset=0,
            actor=current_user,
        )
    except Exception as error:
        _raise_ledger_error(error)
    for record in page.items:
        if record.adjustment.id == adjustment.id:
            return _serialize_adjustment(record)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Adjustment was created but could not be loaded.",
    )


@router.get("/ledger/adjustments", response_model=LedgerAdjustmentListResponse)
async def list_ledger_adjustments(
    session: DatabaseSession,
    current_user: CurrentUser,
    staff_id: Annotated[int | None, Query(gt=0)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    include_deleted: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LedgerAdjustmentListResponse:
    service = LedgerService(session)
    try:
        page = await service.list_adjustments(
            staff_id=staff_id,
            date_from=date_from,
            date_to=date_to,
            include_deleted=include_deleted,
            limit=limit,
            offset=offset,
            actor=current_user,
        )
    except Exception as error:
        _raise_ledger_error(error)
    return _serialize_adjustment_page(page, limit=limit, offset=offset)


@router.get("/settlements", response_model=SettlementListResponse)
async def list_settlements(
    session: DatabaseSession,
    current_user: CurrentUser,
    staff_id: Annotated[int | None, Query(gt=0)] = None,
    settlement_status: Annotated[
        StaffSettlementStatus | None,
        Query(alias="status"),
    ] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    include_deleted: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SettlementListResponse:
    service = LedgerService(session)
    try:
        page = await service.list_settlements(
            staff_id=staff_id,
            status=settlement_status,
            date_from=date_from,
            date_to=date_to,
            include_deleted=include_deleted,
            limit=limit,
            offset=offset,
            actor=current_user,
        )
    except Exception as error:
        _raise_ledger_error(error)
    return _serialize_settlement_page(page, limit=limit, offset=offset)


@router.post("/settlements/{settlement_id}/claim", response_model=SettlementResponse)
async def claim_settlement(
    settlement_id: SettlementId,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> SettlementResponse:
    service = LedgerService(session)
    try:
        settlement = await service.claim_settlement(settlement_id, current_user)
        record = await service.get_settlement_record(settlement.id, current_user)
    except Exception as error:
        _raise_ledger_error(error)
    return _serialize_settlement(record)


@router.post("/settlements/{settlement_id}/done", response_model=SettlementResponse)
async def complete_settlement(
    settlement_id: SettlementId,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> SettlementResponse:
    service = LedgerService(session)
    try:
        settlement = await service.complete_settlement(settlement_id, current_user)
        record = await service.get_settlement_record(settlement.id, current_user)
    except Exception as error:
        _raise_ledger_error(error)
    return _serialize_settlement(record)


@router.post("/settlements/{settlement_id}/cancel", response_model=SettlementResponse)
async def cancel_settlement(
    settlement_id: SettlementId,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> SettlementResponse:
    service = LedgerService(session)
    try:
        settlement = await service.cancel_settlement(settlement_id, current_user)
        record = await service.get_settlement_record(settlement.id, current_user)
    except Exception as error:
        _raise_ledger_error(error)
    return _serialize_settlement(record)


def _serialize_ledger(report: LedgerReport) -> LedgerResponse:
    return LedgerResponse(
        items=[
            LedgerItemResponse(
                staff_id=item.staff_id,
                staff_username=item.staff_username,
                staff_color=item.staff_color,
                total_in=item.total_in,
                total_out=item.total_out,
                settled_amount=item.settled_amount,
                net=item.net,
                payments_count=item.payments_count,
                cashouts_count=item.cashouts_count,
                settlements_count=item.settlements_count,
            )
            for item in report.items
        ],
        summary=LedgerSummaryResponse(
            total_in=report.summary.total_in,
            total_out=report.summary.total_out,
            settled_amount=report.summary.settled_amount,
            net=report.summary.net,
        ),
    )


def _serialize_settlement_page(
    page: SettlementListPage,
    *,
    limit: int,
    offset: int,
) -> SettlementListResponse:
    return SettlementListResponse(
        items=[_serialize_settlement(item) for item in page.items],
        limit=limit,
        offset=offset,
        has_more=page.has_more,
    )


def _serialize_settlement(record: SettlementRecord) -> SettlementResponse:
    settlement = record.settlement
    return SettlementResponse(
        id=settlement.id,
        staff_id=settlement.staff_id,
        staff_username=record.staff_username,
        staff_color=record.staff_color,
        amount=settlement.amount,
        status=settlement.status,
        claimed_by_admin_id=settlement.claimed_by_admin_id,
        claimed_by_admin_username=record.claimed_by_admin_username,
        claimed_at=settlement.claimed_at,
        completed_by_admin_id=settlement.completed_by_admin_id,
        completed_by_admin_username=record.completed_by_admin_username,
        completed_at=settlement.completed_at,
        created_by_admin_id=settlement.created_by_admin_id,
        created_by_admin_username=record.created_by_admin_username,
        created_at=settlement.created_at,
        updated_at=settlement.updated_at,
        notes=settlement.notes,
        payment_ids=record.payment_ids,
        cashout_ids=record.cashout_ids,
        adjustment_ids=record.adjustment_ids,
    )


def _serialize_adjustment_page(
    page: LedgerAdjustmentListPage,
    *,
    limit: int,
    offset: int,
) -> LedgerAdjustmentListResponse:
    return LedgerAdjustmentListResponse(
        items=[_serialize_adjustment(item) for item in page.items],
        limit=limit,
        offset=offset,
        has_more=page.has_more,
    )


def _serialize_adjustment(record: LedgerAdjustmentRecord) -> LedgerAdjustmentResponse:
    adjustment = record.adjustment
    return LedgerAdjustmentResponse(
        id=adjustment.id,
        staff_id=adjustment.staff_id,
        staff_username=record.staff_username,
        staff_color=record.staff_color,
        type=adjustment.type.value,
        amount_delta=adjustment.amount_delta,
        previous_total_in=adjustment.previous_total_in,
        new_total_in=adjustment.new_total_in,
        reason=adjustment.reason,
        created_by_admin_id=adjustment.created_by_admin_id,
        created_by_admin_username=record.created_by_admin_username,
        settlement_id=adjustment.settlement_id,
        created_at=adjustment.created_at,
    )


def _raise_ledger_error(error: Exception) -> NoReturn:
    if isinstance(error, LedgerAuthorizationError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    if isinstance(error, StaffNotFoundError | SettlementNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    if isinstance(error, LedgerStateConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    raise error

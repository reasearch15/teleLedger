from typing import Annotated, NoReturn

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.dependencies import (
    CashoutServiceDependency,
    CurrentUser,
    DatabaseSession,
)
from app.db.repositories.cashout import CashoutListPage
from app.db.retry import run_read_with_retry
from app.models.cashout import CashoutRequest, CashoutStatus, CashoutTelegramStatus
from app.schemas.cashout import (
    CashoutAuditResponse,
    CashoutListResponse,
    CashoutResponse,
    CashoutStaffResponse,
    CreateCashoutRequest,
    UpdateCashoutNotesRequest,
)
from app.services.cashout import (
    CashoutAuthorizationError,
    CashoutIdempotencyConflictError,
    CashoutNotFoundError,
    CashoutService,
    CashoutStateConflictError,
)

router = APIRouter(prefix="/api/cashouts", tags=["cashouts"])
CashoutId = Annotated[int, Path(gt=0)]


@router.post("", response_model=CashoutResponse, status_code=status.HTTP_201_CREATED)
async def create_cashout(
    request: CreateCashoutRequest,
    current_user: CurrentUser,
    service: CashoutServiceDependency,
) -> CashoutResponse:
    """Create one durable, idempotent staff cashout request."""
    try:
        cashout = await service.create(
            player_tag=request.player_tag,
            amount=request.amount,
            notes=request.notes,
            idempotency_key=request.idempotency_key,
            actor=current_user,
        )
    except CashoutAuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    except CashoutIdempotencyConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return _serialize_cashout(
        cashout,
        requester=CashoutStaffResponse(
            id=current_user.id,
            username=current_user.username,
            color=current_user.staff_color,
        ),
    )


@router.get("", response_model=CashoutListResponse)
async def list_cashouts(
    session: DatabaseSession,
    current_user: CurrentUser,
    cashout_status: Annotated[CashoutStatus | None, Query(alias="status")] = None,
    telegram_status: CashoutTelegramStatus | None = None,
    search: Annotated[str | None, Query(max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CashoutListResponse:
    """List own staff history or all requests for an administrator."""
    page = await run_read_with_retry(
        lambda read_session: CashoutService(read_session).list_requests(
            status=cashout_status,
            telegram_status=telegram_status,
            search=search,
            limit=limit,
            offset=offset,
            current_user=current_user,
        ),
        session=session,
        operation_name="cashouts.list",
    )
    return _serialize_page(page, limit=limit, offset=offset)


@router.patch("/{cashout_id}/notes", response_model=CashoutResponse)
async def update_cashout_notes(
    cashout_id: CashoutId,
    request: UpdateCashoutNotesRequest,
    current_user: CurrentUser,
    service: CashoutServiceDependency,
) -> CashoutResponse:
    try:
        cashout = await service.update_notes(cashout_id, request.notes, current_user)
    except Exception as error:
        _raise_workflow_error(error)
    return _serialize_cashout(cashout)


@router.post("/{cashout_id}/complete", response_model=CashoutResponse)
async def complete_cashout(
    cashout_id: CashoutId,
    current_user: CurrentUser,
    service: CashoutServiceDependency,
) -> CashoutResponse:
    try:
        cashout = await service.complete(cashout_id, current_user)
    except Exception as error:
        _raise_workflow_error(error)
    return _serialize_cashout(
        cashout,
        completer=CashoutStaffResponse(
            id=current_user.id,
            username=current_user.username,
            color=current_user.staff_color,
        ),
    )


@router.post("/{cashout_id}/cancel", response_model=CashoutResponse)
async def cancel_cashout(
    cashout_id: CashoutId,
    current_user: CurrentUser,
    service: CashoutServiceDependency,
) -> CashoutResponse:
    try:
        cashout = await service.cancel(cashout_id, current_user)
    except Exception as error:
        _raise_workflow_error(error)
    return _serialize_cashout(cashout)


@router.post("/{cashout_id}/retry-telegram", response_model=CashoutResponse)
async def retry_cashout_telegram(
    cashout_id: CashoutId,
    current_user: CurrentUser,
    service: CashoutServiceDependency,
) -> CashoutResponse:
    try:
        cashout = await service.retry_telegram(cashout_id, current_user)
    except Exception as error:
        _raise_workflow_error(error)
    return _serialize_cashout(cashout)


@router.get("/{cashout_id}/audit", response_model=list[CashoutAuditResponse])
async def list_cashout_audit(
    cashout_id: CashoutId,
    current_user: CurrentUser,
    service: CashoutServiceDependency,
) -> list[CashoutAuditResponse]:
    try:
        records = await service.list_audit(cashout_id, current_user)
    except Exception as error:
        _raise_workflow_error(error)
    return [
        CashoutAuditResponse(
            id=record.audit.id,
            cashout_request_id=record.audit.cashout_request_id,
            action=record.audit.action,
            actor_user_id=record.audit.actor_user_id,
            actor_username=record.actor_username,
            previous_value=record.audit.previous_value,
            new_value=record.audit.new_value,
            created_at=record.audit.created_at,
        )
        for record in records
    ]


def _serialize_page(
    page: CashoutListPage,
    *,
    limit: int,
    offset: int,
) -> CashoutListResponse:
    return CashoutListResponse(
        items=[
            _serialize_cashout(
                item.cashout,
                requester=item.requested_by,
                completer=item.completed_by,
            )
            for item in page.items
        ],
        limit=limit,
        offset=offset,
        has_more=page.has_more,
    )


def _serialize_cashout(
    cashout: CashoutRequest,
    *,
    requester: object | None = None,
    completer: object | None = None,
) -> CashoutResponse:
    response = CashoutResponse.model_validate(cashout)
    return response.model_copy(
        update={
            "requested_by": (
                CashoutStaffResponse.model_validate(requester)
                if requester is not None
                else None
            ),
            "completed_by": (
                CashoutStaffResponse.model_validate(completer)
                if completer is not None
                else None
            ),
        }
    )


def _raise_workflow_error(error: Exception) -> NoReturn:
    if isinstance(error, CashoutNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    if isinstance(error, CashoutAuthorizationError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    if isinstance(error, CashoutStateConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    raise error

from collections.abc import Awaitable
from datetime import date
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Response, status

from app.api.dependencies import CurrentUser, DatabaseSession, PaymentServiceDependency
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories.payment_event import PaymentListPage
from app.db.retry import run_read_with_retry
from app.models.payment_event import PaymentEvent, PaymentStatus
from app.schemas.payment import (
    AssignPaymentRequest,
    PaymentAuditResponse,
    PaymentEventResponse,
    PaymentListItemResponse,
    PaymentListResponse,
    StaffIdentityResponse,
)
from app.services.payment import (
    AssignmentStaffNotFoundError,
    InvalidPaymentFilterError,
    PaymentAuthorizationError,
    PaymentNotFoundError,
    PaymentService,
    PaymentStateConflictError,
)

router = APIRouter(prefix="/api/payments", tags=["payments"])
PaymentId = Annotated[int, Path(gt=0)]
logger = get_logger(__name__)
settings = get_settings()


@router.get("", response_model=PaymentListResponse)
async def list_payments(
    session: DatabaseSession,
    current_user: CurrentUser,
    payment_status: Annotated[PaymentStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=255)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 7,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_total: bool = False,
    active_only: bool = False,
) -> Response:
    """Return a bounded, lightweight payment page with optional filters."""
    started_at = perf_counter()
    try:
        page = await run_read_with_retry(
            lambda read_session: PaymentService(read_session).list_payments(
                status=payment_status,
                search=search,
                date_from=date_from,
                date_to=date_to,
                limit=limit,
                offset=offset,
                include_total=include_total,
                active_only=active_only,
                current_user=current_user,
            ),
            session=session,
            operation_name="payments.list",
        )
    except InvalidPaymentFilterError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    serialization_started_at = perf_counter()
    response_model = _serialize_payment_page(page, limit=limit, offset=offset)
    response_body = response_model.model_dump_json()
    serialization_ms = (perf_counter() - serialization_started_at) * 1000

    if settings.environment == "development":
        logger.info(
            "payments_list_timing",
            extra={
                "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                "connection_acquisition_ms": round(
                    page.connection_acquisition_ms,
                    2,
                ),
                "list_query_ms": round(page.list_query_ms, 2),
                "count_query_ms": round(page.count_query_ms, 2),
                "has_more_ms": round(page.has_more_ms, 4),
                "serialization_ms": round(serialization_ms, 2),
                "limit": limit,
                "offset": offset,
                "total": page.total,
                "include_total": include_total,
            },
        )

    return Response(content=response_body, media_type="application/json")


@router.get("/my-history", response_model=PaymentListResponse)
async def list_my_payment_history(
    session: DatabaseSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_total: bool = False,
) -> Response:
    """Return payments claimed or completed by the current staff user."""
    try:
        page = await run_read_with_retry(
            lambda read_session: PaymentService(read_session).list_my_history(
                limit=limit,
                offset=offset,
                include_total=include_total,
                current_user=current_user,
            ),
            session=session,
            operation_name="payments.my_history",
        )
    except PaymentAuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error

    response_model = _serialize_payment_page(page, limit=limit, offset=offset)
    return Response(
        content=response_model.model_dump_json(),
        media_type="application/json",
    )


@router.get("/history", response_model=PaymentListResponse)
async def list_payment_history(
    session: DatabaseSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    include_total: bool = False,
) -> Response:
    """Return all claimed and completed payments for administrators."""
    try:
        page = await run_read_with_retry(
            lambda read_session: PaymentService(read_session).list_history(
                limit=limit,
                offset=offset,
                include_total=include_total,
                current_user=current_user,
            ),
            session=session,
            operation_name="payments.history",
        )
    except PaymentAuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error

    response_model = _serialize_payment_page(page, limit=limit, offset=offset)
    return Response(
        content=response_model.model_dump_json(),
        media_type="application/json",
    )


@router.post("/{payment_id}/claim", response_model=PaymentEventResponse)
async def claim_payment(
    payment_id: PaymentId,
    service: PaymentServiceDependency,
    current_user: CurrentUser,
) -> PaymentEventResponse:
    """Claim a pending payment for a staff member."""
    payment = await _run_action(service.claim(payment_id, current_user))
    return PaymentEventResponse.model_validate(payment)


@router.post("/{payment_id}/done", response_model=PaymentEventResponse)
async def mark_payment_done(
    payment_id: PaymentId,
    service: PaymentServiceDependency,
    current_user: CurrentUser,
) -> PaymentEventResponse:
    """Mark a pending or in-progress payment as done."""
    payment = await _run_action(service.mark_done(payment_id, current_user))
    return PaymentEventResponse.model_validate(payment)


@router.post("/{payment_id}/unclaim", response_model=PaymentEventResponse)
async def unclaim_payment(
    payment_id: PaymentId,
    service: PaymentServiceDependency,
    current_user: CurrentUser,
) -> PaymentEventResponse:
    """Return an in-progress payment to pending."""
    payment = await _run_action(service.unclaim(payment_id, current_user))
    return PaymentEventResponse.model_validate(payment)


@router.post(
    "/admin/{payment_id}/force-unclaim",
    response_model=PaymentEventResponse,
)
async def force_unclaim_payment(
    payment_id: PaymentId,
    service: PaymentServiceDependency,
    current_user: CurrentUser,
) -> PaymentEventResponse:
    payment = await _run_action(service.force_unclaim(payment_id, current_user))
    return PaymentEventResponse.model_validate(payment)


@router.post(
    "/admin/{payment_id}/reopen",
    response_model=PaymentEventResponse,
)
async def reopen_payment(
    payment_id: PaymentId,
    service: PaymentServiceDependency,
    current_user: CurrentUser,
) -> PaymentEventResponse:
    payment = await _run_action(service.reopen(payment_id, current_user))
    return PaymentEventResponse.model_validate(payment)


@router.post(
    "/admin/{payment_id}/assign",
    response_model=PaymentEventResponse,
)
async def assign_payment(
    payment_id: PaymentId,
    request: AssignPaymentRequest,
    service: PaymentServiceDependency,
    current_user: CurrentUser,
) -> PaymentEventResponse:
    try:
        payment = await service.assign(
            payment_id,
            request.staff_id,
            current_user,
        )
    except AssignmentStaffNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except PaymentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except PaymentStateConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except PaymentAuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    return PaymentEventResponse.model_validate(payment)


@router.get(
    "/admin/{payment_id}/audit",
    response_model=list[PaymentAuditResponse],
)
async def list_payment_audit(
    payment_id: PaymentId,
    service: PaymentServiceDependency,
    current_user: CurrentUser,
) -> list[PaymentAuditResponse]:
    try:
        records = await service.list_audit(payment_id, current_user)
    except PaymentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except PaymentAuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    return [
        PaymentAuditResponse(
            id=record.audit.id,
            payment_event_id=record.audit.payment_event_id,
            actor_user_id=record.audit.actor_user_id,
            actor_username=record.actor_username,
            subject_staff_id=record.audit.subject_staff_id,
            subject_username=record.subject_username,
            action=record.audit.action,
            from_status=record.audit.from_status,
            to_status=record.audit.to_status,
            created_at=record.audit.created_at,
        )
        for record in records
    ]


async def _run_action(action: Awaitable[PaymentEvent]) -> PaymentEvent:
    try:
        return await action
    except PaymentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except PaymentStateConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except PaymentAuthorizationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error


def _serialize_payment_page(
    page: PaymentListPage,
    *,
    limit: int,
    offset: int,
) -> PaymentListResponse:
    return PaymentListResponse(
        items=[
            PaymentListItemResponse.model_validate(item.payment).model_copy(
                update={
                    "claimed_by_staff": (
                        StaffIdentityResponse.model_validate(
                            item.claimed_by_staff
                        )
                        if item.claimed_by_staff is not None
                        else None
                    ),
                    "completed_by_staff": (
                        StaffIdentityResponse.model_validate(
                            item.completed_by_staff
                        )
                        if item.completed_by_staff is not None
                        else None
                    ),
                }
            )
            for item in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
        has_more=page.has_more,
    )

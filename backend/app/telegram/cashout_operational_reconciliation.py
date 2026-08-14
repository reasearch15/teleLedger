from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import or_, select

from app.core.logging import get_logger
from app.db.repositories.cashout_partial_pending import CashoutPartialPendingRepository
from app.db.session import SessionFactory
from app.models.cashout import CashoutRequest, CashoutStatus, CashoutTelegramStatus
from app.services.cashout_telegram import CashoutTelegramService

logger = get_logger(__name__)


class CashoutTerminalSyncGateway(Protocol):
    async def delete_message(self, *, chat_id: int, message_id: int) -> bool: ...

    async def edit_cashout_task_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class CashoutOperationalReconciliationResult:
    """Bounded operational repair counters for cashout support tooling."""

    scanned_cashouts: int
    expired_pending_deleted: int
    retryable_delivery_requeued: int
    terminal_cleanup_attempted: int
    terminal_cleanup_failed: int


async def reconcile_cashout_operational_state(
    *,
    limit: int = 50,
    stale_after_seconds: int = 120,
    cashout_id: int | None = None,
    coadmin_id: int | None = None,
    gateway: CashoutTerminalSyncGateway | None = None,
) -> CashoutOperationalReconciliationResult:
    """Repair or flag cashout operational state without changing financial truth."""
    now = datetime.now(UTC)
    async with SessionFactory() as session, session.begin():
        expired_pending_deleted = await CashoutPartialPendingRepository(
            session
        ).delete_expired(now)
        statement = _candidate_statement(
            now=now,
            stale_after_seconds=stale_after_seconds,
            cashout_id=cashout_id,
            coadmin_id=coadmin_id,
            limit=limit,
        )
        cashouts = list((await session.scalars(statement)).all())

        retryable_delivery_requeued = 0
        for cashout in cashouts:
            if _is_retryable_delivery(cashout):
                cashout.telegram_status = CashoutTelegramStatus.PENDING
                cashout.telegram_next_attempt_at = now
                retryable_delivery_requeued += 1
                logger.info(
                    "cashout_reconciliation_requeued_delivery",
                    extra={
                        "cashout_request_id": cashout.id,
                        "telegram_chat_id": cashout.telegram_chat_id,
                        "telegram_message_id": cashout.telegram_message_id,
                        "reconciliation_action": "requeue_delivery",
                        "reconciliation_result": "requeued",
                    },
                )

    terminal_cleanup_attempted = 0
    terminal_cleanup_failed = 0
    if gateway is not None:
        for cashout in cashouts:
            if not _needs_terminal_cleanup(cashout):
                continue
            terminal_cleanup_attempted += 1
            async with SessionFactory() as session:
                attached = await session.get(CashoutRequest, cashout.id)
                if attached is None:
                    continue
                service = CashoutTelegramService(session, gateway=gateway)
                if attached.status == CashoutStatus.CANCELLED:
                    status = await service.sync_cancelled_task(attached)
                else:
                    status = await service.sync_terminal_task(attached)
            if status == "failed":
                terminal_cleanup_failed += 1
            logger.info(
                "cashout_reconciliation_terminal_cleanup",
                extra={
                    "cashout_request_id": cashout.id,
                    "telegram_chat_id": cashout.telegram_chat_id,
                    "telegram_message_id": cashout.telegram_message_id,
                    "reconciliation_action": "terminal_cleanup",
                    "reconciliation_result": status,
                },
            )

    result = CashoutOperationalReconciliationResult(
        scanned_cashouts=len(cashouts),
        expired_pending_deleted=expired_pending_deleted,
        retryable_delivery_requeued=retryable_delivery_requeued,
        terminal_cleanup_attempted=terminal_cleanup_attempted,
        terminal_cleanup_failed=terminal_cleanup_failed,
    )
    logger.info(
        "cashout_operational_reconciliation_finished",
        extra={
            "total": result.scanned_cashouts,
            "expired_pending_count": result.expired_pending_deleted,
            "reconciliation_result": "finished",
        },
    )
    return result


def _candidate_statement(
    *,
    now: datetime,
    stale_after_seconds: int,
    cashout_id: int | None,
    coadmin_id: int | None,
    limit: int,
):
    stale_before = now - timedelta(seconds=stale_after_seconds)
    conditions = [
        or_(
            CashoutRequest.telegram_status == CashoutTelegramStatus.FAILED_TO_SEND,
            CashoutRequest.telegram_next_attempt_at < stale_before,
            CashoutRequest.telegram_message_id.is_(None),
            CashoutRequest.status.in_(
                (CashoutStatus.COMPLETED, CashoutStatus.CANCELLED)
            ),
        )
    ]
    if coadmin_id is not None:
        conditions.append(CashoutRequest.coadmin_id == coadmin_id)
    if cashout_id is not None:
        conditions.append(CashoutRequest.id == cashout_id)
    return (
        select(CashoutRequest)
        .where(*conditions)
        .order_by(CashoutRequest.updated_at.asc(), CashoutRequest.id.asc())
        .limit(limit)
    )


def _is_retryable_delivery(cashout: CashoutRequest) -> bool:
    return (
        cashout.status in (CashoutStatus.PENDING, CashoutStatus.FAILED_TO_SEND)
        and cashout.telegram_status == CashoutTelegramStatus.FAILED_TO_SEND
        and cashout.telegram_message_id is None
    )


def _needs_terminal_cleanup(cashout: CashoutRequest) -> bool:
    return (
        cashout.status in (CashoutStatus.COMPLETED, CashoutStatus.CANCELLED)
        and cashout.telegram_chat_id is not None
        and cashout.telegram_message_id is not None
    )

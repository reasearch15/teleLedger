from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.repositories.cashout import (
    CashoutAuditRecord,
    CashoutListPage,
    CashoutRepository,
)
from app.db.repositories.cashout_partial_pending import CashoutPartialPendingRepository
from app.models.cashout import (
    CashoutAuditAction,
    CashoutCompletionType,
    CashoutRequest,
    CashoutRequestAudit,
    CashoutStatus,
    CashoutTelegramStatus,
)
from app.models.user import User, UserRole
from app.services.base import ApplicationService
from app.telegram.peer_ids import chat_ids_equivalent
from app.websocket.events import LiveEventType, event_broker

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CashoutTelegramDeletionTarget:
    cashout_request_id: int
    telegram_chat_id: int | None
    telegram_message_id: int | None


@dataclass(frozen=True, slots=True)
class CashoutTelegramDeletionResult:
    status: str
    error: str | None = None


class CashoutNotFoundError(Exception):
    """Raised when a cashout request does not exist."""


class CashoutAuthorizationError(Exception):
    """Raised when an actor cannot access a cashout operation."""


class CashoutStateConflictError(Exception):
    """Raised for invalid cashout workflow transitions."""


class CashoutIdempotencyConflictError(Exception):
    """Raised when one submission key is reused with different data."""


class CashoutValidationError(Exception):
    """Raised when cashout payment data is invalid."""


class CashoutService(ApplicationService):
    """Cashout creation, history, administration, and audit workflow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = CashoutRepository(session)
        self._pending = CashoutPartialPendingRepository(session)

    async def create(
        self,
        *,
        player_tag: str,
        amount: Decimal,
        notes: str | None,
        idempotency_key: UUID,
        actor: User,
    ) -> CashoutRequest:
        self._require_staff(actor)
        if actor.coadmin_id is None:
            raise CashoutAuthorizationError(
                "Staff must be assigned to a coadmin before creating cashouts."
            )
        key = str(idempotency_key)
        cashout: CashoutRequest
        created = False
        try:
            async with self._session.begin():
                existing = await self._repository.get_by_idempotency_key(actor.id, key)
                if existing is not None:
                    self._verify_idempotent_payload(
                        existing,
                        player_tag=player_tag,
                        amount=amount,
                        notes=notes,
                    )
                    cashout = existing
                else:
                    cashout = await self._repository.add(
                        CashoutRequest(
                            request_number=None,
                            idempotency_key=key,
                            player_tag=player_tag,
                            amount=amount,
                            notes=notes,
                            status=CashoutStatus.PENDING,
                            telegram_status=CashoutTelegramStatus.PENDING,
                            telegram_random_id=self._telegram_random_id(actor.id, key),
                            created_by_staff_id=actor.id,
                            coadmin_id=actor.coadmin_id,
                        )
                    )
                    cashout.request_number = f"CR-{cashout.id:06d}"
                    await self._repository.add_audit(
                        CashoutRequestAudit(
                            cashout_request_id=cashout.id,
                            action=CashoutAuditAction.CREATED,
                            actor_user_id=actor.id,
                            previous_value=None,
                            new_value={
                                "request_number": cashout.request_number,
                                "player_tag": cashout.player_tag,
                                "amount": str(cashout.amount),
                                "notes": cashout.notes,
                                "status": cashout.status.value,
                                "telegram_status": cashout.telegram_status.value,
                                "coadmin_id": actor.coadmin_id,
                            },
                        )
                    )
                    await self._session.flush()
                    await self._session.refresh(cashout)
                    created = True
        except IntegrityError:
            await self._session.rollback()
            existing = await self._repository.get_by_idempotency_key(actor.id, key)
            if existing is None:
                raise
            self._verify_idempotent_payload(
                existing,
                player_tag=player_tag,
                amount=amount,
                notes=notes,
            )
            cashout = existing
            created = False
        if created:
            await event_broker.publish(
                LiveEventType.CASHOUT_CREATED,
                cashout_id=cashout.id,
            )
            await _attempt_immediate_cashout_delivery(cashout.id)
            await self._session.refresh(cashout)
        return cashout

    async def list_requests(
        self,
        *,
        status: CashoutStatus | None,
        telegram_status: CashoutTelegramStatus | None,
        search: str | None,
        limit: int,
        offset: int,
        current_user: User,
    ) -> CashoutListPage:
        normalized_search = search.strip() if search else None
        return await self._repository.list_requests(
            staff_id=(
                current_user.id
                if current_user.role == UserRole.STAFF
                else None
            ),
            status=status,
            telegram_status=telegram_status,
            search=normalized_search or None,
            limit=limit,
            offset=offset,
        )

    async def update_notes(
        self,
        cashout_id: int,
        notes: str | None,
        actor: User,
    ) -> CashoutRequest:
        async with self._session.begin():
            cashout = await self._get_locked(cashout_id)
            self._require_owner_or_admin(cashout, actor)
            if (
                actor.role != UserRole.ADMIN
                and cashout.status
                in (CashoutStatus.COMPLETED, CashoutStatus.CANCELLED)
            ):
                raise CashoutStateConflictError(
                    "Completed or cancelled cashouts cannot be edited."
                )
            previous_notes = cashout.notes
            cashout.notes = notes
            await self._record_audit(
                cashout,
                action=CashoutAuditAction.EDITED_NOTES,
                actor=actor,
                previous_value={"notes": previous_notes},
                new_value={"notes": notes},
            )
            await self._session.refresh(cashout)
        await event_broker.publish(
            LiveEventType.CASHOUT_NOTES_UPDATED,
            cashout_id=cashout.id,
        )
        await _sync_cashout_telegram_from_persisted_state(cashout)
        return cashout

    async def complete(self, cashout_id: int, actor: User) -> CashoutRequest:
        """Complete a cashout as a full payment for backward-compatible admin use."""
        return await self.complete_cashout(
            cashout_id,
            actor=actor,
            completion_type=CashoutCompletionType.FULL,
        )

    async def complete_cashout(
        self,
        cashout_id: int,
        *,
        actor: User,
        completion_type: CashoutCompletionType,
        actual_paid_amount: Decimal | None = None,
        actor_source: str = "atlas_admin",
        actor_identifier: str | None = None,
    ) -> CashoutRequest:
        self._require_admin(actor)
        async with self._session.begin():
            cashout = await self._get_locked(cashout_id)
            if cashout.status in (CashoutStatus.COMPLETED, CashoutStatus.CANCELLED):
                raise CashoutStateConflictError(
                    "This cashout is already completed or cancelled."
                )
            previous_status = cashout.status
            paid_amount = self._resolve_actual_paid_amount(
                cashout,
                completion_type=completion_type,
                actual_paid_amount=actual_paid_amount,
            )
            completed_at = datetime.now(UTC)
            transition = await self._session.execute(
                update(CashoutRequest)
                .where(
                    CashoutRequest.id == cashout.id,
                    CashoutRequest.status.in_(
                        (
                            CashoutStatus.PENDING,
                            CashoutStatus.SENT,
                            CashoutStatus.FAILED_TO_SEND,
                        )
                    ),
                )
                .values(
                    status=CashoutStatus.COMPLETED,
                    completion_type=completion_type,
                    actual_paid_amount=paid_amount,
                    completed_by_staff_id=actor.id,
                    completed_at=completed_at,
                    telegram_next_attempt_at=None,
                )
                .returning(CashoutRequest.id)
            )
            if transition.scalar_one_or_none() is None:
                raise CashoutStateConflictError(
                    "This cashout is already completed or cancelled."
                )
            await self._session.refresh(cashout)
            await self._record_audit(
                cashout,
                action=CashoutAuditAction.COMPLETED,
                actor=actor,
                previous_value={"status": previous_status.value},
                new_value={
                    "status": cashout.status.value,
                    "completed_by_staff_id": actor.id,
                    "requested_amount": str(cashout.amount),
                    "actual_paid_amount": str(paid_amount),
                    "completion_type": completion_type.value,
                    "actor_source": actor_source,
                    "actor_identifier": actor_identifier or str(actor.id),
                },
            )
            await self._session.refresh(cashout)
        await event_broker.publish(
            LiveEventType.CASHOUT_COMPLETED,
            cashout_id=cashout.id,
        )
        await event_broker.publish(LiveEventType.LEDGER_CHANGED)
        return cashout

    async def complete_partial(
        self,
        cashout_id: int,
        *,
        actual_paid_amount: Decimal,
        actor: User,
    ) -> CashoutRequest:
        """Complete a cashout as partial payment for bot/service callers."""
        return await self.complete_cashout(
            cashout_id,
            actor=actor,
            completion_type=CashoutCompletionType.PARTIAL,
            actual_paid_amount=actual_paid_amount,
        )

    async def complete_from_telegram(
        self,
        cashout_id: int,
        *,
        coadmin_id: int,
        expected_chat_id: int,
        completion_type: CashoutCompletionType,
        actual_paid_amount: Decimal | None = None,
        telegram_user_id: int | None = None,
        telegram_username: str | None = None,
    ) -> CashoutRequest:
        """Complete a cashout from a verified Telegram bot action."""
        async with self._session.begin():
            cashout = await self._repository.get_by_id_for_coadmin(
                cashout_id,
                coadmin_id,
                for_update=True,
            )
            if cashout is None:
                raise CashoutNotFoundError(f"Cashout request {cashout_id} was not found")
            if cashout.telegram_chat_id is None or not chat_ids_equivalent(
                cashout.telegram_chat_id,
                expected_chat_id,
            ):
                raise CashoutAuthorizationError("Telegram shared supergroup mismatch.")
            if cashout.status in (CashoutStatus.COMPLETED, CashoutStatus.CANCELLED):
                raise CashoutStateConflictError(
                    "This cashout is already completed or cancelled."
                )

            previous_status = cashout.status
            paid_amount = self._resolve_actual_paid_amount(
                cashout,
                completion_type=completion_type,
                actual_paid_amount=actual_paid_amount,
            )
            completed_at = datetime.now(UTC)
            transition = await self._session.execute(
                update(CashoutRequest)
                .where(
                    CashoutRequest.id == cashout.id,
                    CashoutRequest.coadmin_id == coadmin_id,
                    CashoutRequest.status.in_(
                        (
                            CashoutStatus.PENDING,
                            CashoutStatus.SENT,
                            CashoutStatus.FAILED_TO_SEND,
                        )
                    ),
                )
                .values(
                    status=CashoutStatus.COMPLETED,
                    completion_type=completion_type,
                    actual_paid_amount=paid_amount,
                    completed_by_staff_id=None,
                    completed_at=completed_at,
                    telegram_next_attempt_at=None,
                )
                .returning(CashoutRequest.id)
            )
            if transition.scalar_one_or_none() is None:
                raise CashoutStateConflictError(
                    "This cashout is already completed or cancelled."
                )
            await self._session.refresh(cashout)
            await self._repository.add_audit(
                CashoutRequestAudit(
                    cashout_request_id=cashout.id,
                    action=CashoutAuditAction.TELEGRAM_BOT_COMPLETED,
                    actor_user_id=None,
                    previous_value={"status": previous_status.value},
                    new_value={
                        "status": cashout.status.value,
                        "requested_amount": str(cashout.amount),
                        "actual_paid_amount": str(paid_amount),
                        "completion_type": completion_type.value,
                        "actor_source": "telegram_bot",
                        "telegram_user_id": telegram_user_id,
                        "telegram_username": telegram_username,
                    },
                )
            )
            await self._session.refresh(cashout)
        await event_broker.publish(
            LiveEventType.CASHOUT_COMPLETED,
            cashout_id=cashout.id,
        )
        await event_broker.publish(LiveEventType.LEDGER_CHANGED)
        return cashout

    async def cancel(self, cashout_id: int, actor: User) -> CashoutRequest:
        self._require_admin(actor)
        cancellation_status = "cancelled"
        async with self._session.begin():
            cashout = await self._get_locked(cashout_id)
            if cashout.status == CashoutStatus.COMPLETED:
                raise CashoutStateConflictError(
                    "This cashout is already completed or cancelled."
                )
            delete_target = CashoutTelegramDeletionTarget(
                cashout_request_id=cashout.id,
                telegram_chat_id=cashout.telegram_chat_id,
                telegram_message_id=cashout.telegram_message_id,
            )
            if cashout.status == CashoutStatus.CANCELLED:
                cancellation_status = "already_cancelled"
            else:
                previous_status = cashout.status
                cashout.status = CashoutStatus.CANCELLED
                cashout.cancelled_at = datetime.now(UTC)
                cashout.telegram_next_attempt_at = None
                await self._record_audit(
                    cashout,
                    action=CashoutAuditAction.CANCELLED,
                    actor=actor,
                    previous_value={"status": previous_status.value},
                    new_value={"status": cashout.status.value},
                )
            await self._pending.delete_for_cashout(cashout.id)
            await self._session.refresh(cashout)
        if cancellation_status == "cancelled":
            await event_broker.publish(
                LiveEventType.CASHOUT_CANCELLED,
                cashout_id=cashout.id,
            )
        delete_result = await _delete_cancelled_cashout_telegram_message(
            delete_target,
            cancellation_status=cancellation_status,
        )
        logger.info(
            "cashout_cancellation_processed",
            extra={
                "cashout_request_id": delete_target.cashout_request_id,
                "telegram_chat_id": delete_target.telegram_chat_id,
                "telegram_message_id": delete_target.telegram_message_id,
                "cancellation_status": cancellation_status,
                "telegram_delete_status": delete_result.status,
                "error": delete_result.error,
            },
        )
        return cashout

    async def retry_telegram(
        self,
        cashout_id: int,
        actor: User,
    ) -> CashoutRequest:
        self._require_admin(actor)
        async with self._session.begin():
            cashout = await self._get_locked(cashout_id)
            if cashout.telegram_status == CashoutTelegramStatus.SENT:
                raise CashoutStateConflictError("This cashout was already sent.")
            if cashout.status == CashoutStatus.CANCELLED:
                raise CashoutStateConflictError("Cancelled cashouts cannot be sent.")
            previous = {
                "telegram_status": cashout.telegram_status.value,
                "telegram_attempts": cashout.telegram_attempts,
            }
            cashout.telegram_status = CashoutTelegramStatus.PENDING
            cashout.telegram_next_attempt_at = datetime.now(UTC)
            cashout.telegram_last_error = None
            if cashout.status == CashoutStatus.FAILED_TO_SEND:
                cashout.status = CashoutStatus.PENDING
            await self._record_audit(
                cashout,
                action=CashoutAuditAction.TELEGRAM_RETRY,
                actor=actor,
                previous_value=previous,
                new_value={
                    "telegram_status": cashout.telegram_status.value,
                    "manual": True,
                },
            )
            await self._session.refresh(cashout)
        await _attempt_immediate_cashout_delivery(cashout.id)
        await self._session.refresh(cashout)
        return cashout

    async def list_audit(
        self,
        cashout_id: int,
        actor: User,
    ) -> list[CashoutAuditRecord]:
        self._require_admin(actor)
        cashout = await self._repository.get_by_id_for_update(cashout_id)
        if cashout is None:
            raise CashoutNotFoundError(f"Cashout request {cashout_id} was not found")
        return await self._repository.list_audit(cashout_id)

    async def _get_locked(self, cashout_id: int) -> CashoutRequest:
        cashout = await self._repository.get_by_id_for_update(cashout_id)
        if cashout is None:
            raise CashoutNotFoundError(f"Cashout request {cashout_id} was not found")
        return cashout

    async def _record_audit(
        self,
        cashout: CashoutRequest,
        *,
        action: CashoutAuditAction,
        actor: User | None,
        previous_value: dict[str, object] | None,
        new_value: dict[str, object] | None,
    ) -> None:
        await self._repository.add_audit(
            CashoutRequestAudit(
                cashout_request_id=cashout.id,
                action=action,
                actor_user_id=actor.id if actor is not None else None,
                previous_value=previous_value,
                new_value=new_value,
            )
        )

    @staticmethod
    def _require_staff(actor: User) -> None:
        if actor.role != UserRole.STAFF:
            raise CashoutAuthorizationError("Staff access is required.")

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.role != UserRole.ADMIN:
            raise CashoutAuthorizationError("Administrator access is required.")

    @staticmethod
    def _require_owner_or_admin(cashout: CashoutRequest, actor: User) -> None:
        if (
            actor.role != UserRole.ADMIN
            and cashout.created_by_staff_id != actor.id
        ):
            raise CashoutAuthorizationError(
                "You cannot edit another staff member's cashout."
            )

    @staticmethod
    def _verify_idempotent_payload(
        cashout: CashoutRequest,
        *,
        player_tag: str,
        amount: Decimal,
        notes: str | None,
    ) -> None:
        if (
            cashout.player_tag != player_tag
            or cashout.amount != amount
            or cashout.notes != notes
        ):
            raise CashoutIdempotencyConflictError(
                "This submission key was already used for another cashout."
            )

    @staticmethod
    def _resolve_actual_paid_amount(
        cashout: CashoutRequest,
        *,
        completion_type: CashoutCompletionType,
        actual_paid_amount: Decimal | None,
    ) -> Decimal:
        requested = Decimal(cashout.amount).quantize(Decimal("0.01"))
        if completion_type == CashoutCompletionType.FULL:
            return requested
        if actual_paid_amount is None:
            raise CashoutValidationError("Partial completion requires an actual paid amount.")
        paid = Decimal(actual_paid_amount).quantize(Decimal("0.01"))
        if paid <= Decimal("0.00"):
            raise CashoutValidationError("Partial amount must be greater than zero.")
        if paid >= requested:
            raise CashoutValidationError(
                "Partial amount must be less than the requested amount."
            )
        return paid

    @staticmethod
    def _telegram_random_id(staff_id: int, idempotency_key: str) -> int:
        digest = hashlib.sha256(
            f"{staff_id}:{idempotency_key}".encode()
        ).digest()
        return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1) or 1


async def _attempt_immediate_cashout_delivery(cashout_id: int) -> None:
    """Send one already-persisted cashout through the canonical Telegram path."""
    from app.core.config import get_settings
    from app.telegram.cashout_bot.api import TelegramBotApiGateway
    from app.telegram.cashout_delivery import deliver_cashout_by_id
    from app.telegram.peer_ids import normalize_telegram_chat_id

    settings = get_settings()
    chat_id = normalize_telegram_chat_id(settings.telegram_cashout_group_id)
    extra = {
        "cashout_request_id": cashout_id,
        "telegram_chat_id": chat_id,
    }
    if chat_id is None:
        logger.error("cashout_telegram_delivery_missing_group", extra=extra)
        return
    if settings.telegram_bot_token is None:
        logger.error("cashout_telegram_delivery_missing_bot_token", extra=extra)
        return
    try:
        async with TelegramBotApiGateway() as gateway:
            await deliver_cashout_by_id(
                cashout_id,
                telegram_chat_id=chat_id,
                bot_gateway=gateway,
            )
    except Exception:
        logger.exception("cashout_telegram_immediate_delivery_failed", extra=extra)


async def _sync_cashout_telegram_from_persisted_state(cashout: CashoutRequest) -> None:
    """Best-effort Telegram rebuild after a canonical DB change such as notes."""
    if cashout.telegram_chat_id is None or cashout.telegram_message_id is None:
        return
    try:
        from app.core.config import get_settings

        settings = get_settings()
        if settings.telegram_bot_token is None:
            return
        from app.db.session import SessionFactory
        from app.services.cashout_telegram import CashoutTelegramService
        from app.telegram.cashout_bot.api import TelegramBotApiGateway

        async with TelegramBotApiGateway() as gateway:
            async with SessionFactory() as session:
                attached = await session.get(CashoutRequest, cashout.id)
                if attached is None:
                    return
                await CashoutTelegramService(session, gateway=gateway).sync_persisted_task(
                    attached
                )
    except Exception:
        logger.exception(
            "cashout_telegram_notes_sync_failed",
            extra={
                "cashout_request_id": cashout.id,
                "telegram_chat_id": cashout.telegram_chat_id,
                "telegram_message_id": cashout.telegram_message_id,
            },
        )


async def _delete_cancelled_cashout_telegram_message(
    target: CashoutTelegramDeletionTarget,
    *,
    cancellation_status: str,
) -> CashoutTelegramDeletionResult:
    message_id = target.telegram_message_id
    chat_id = target.telegram_chat_id
    if message_id is None:
        logger.info(
            "cashout_telegram_delete_missing",
            extra={
                "cashout_request_id": target.cashout_request_id,
                "telegram_chat_id": chat_id,
                "cancellation_status": cancellation_status,
                "telegram_delete_status": "no_linked_message",
                "reason_ignored": "no_telegram_message_id",
            },
        )
        return CashoutTelegramDeletionResult("no_linked_message")

    try:
        from app.core.config import get_settings

        settings = get_settings()
        if settings.telegram_bot_token is not None:
            from app.db.session import SessionFactory
            from app.services.cashout_telegram import CashoutTelegramService
            from app.telegram.cashout_bot.api import TelegramBotApiGateway

            async with TelegramBotApiGateway() as gateway:
                async with SessionFactory() as session:
                    cashout = await session.get(
                        CashoutRequest,
                        target.cashout_request_id,
                    )
                    if cashout is None:
                        return CashoutTelegramDeletionResult("no_linked_message")
                    status = await CashoutTelegramService(
                        session,
                        gateway=gateway,
                    ).sync_cancelled_task(cashout)
                    return CashoutTelegramDeletionResult(status)

        from telethon.tl import types  # type: ignore[import-untyped]

        from app.telegram.client import create_telegram_client
        delete_chat_id = chat_id if chat_id is not None else settings.telegram_cashout_group_id
        if delete_chat_id is None:
            error = "missing_cashout_group_id"
            logger.warning(
                "cashout_telegram_delete_failed",
                extra={
                    "cashout_request_id": target.cashout_request_id,
                    "telegram_message_id": message_id,
                    "telegram_chat_id": chat_id,
                    "cancellation_status": cancellation_status,
                    "telegram_delete_status": "failed",
                    "reason_ignored": error,
                    "error": error,
                },
            )
            return CashoutTelegramDeletionResult("failed", error)

        client = create_telegram_client(settings)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                error = "telegram_session_unauthorized"
                logger.warning(
                    "cashout_telegram_delete_failed",
                    extra={
                        "cashout_request_id": target.cashout_request_id,
                        "telegram_message_id": message_id,
                        "telegram_chat_id": delete_chat_id,
                        "cancellation_status": cancellation_status,
                        "telegram_delete_status": "failed",
                        "reason_ignored": error,
                        "error": error,
                    },
                )
                return CashoutTelegramDeletionResult("failed", error)
            message = await client.get_messages(delete_chat_id, ids=message_id)
            if message is None or isinstance(message, types.MessageEmpty):
                logger.info(
                    "cashout_telegram_delete_missing",
                    extra={
                        "cashout_request_id": target.cashout_request_id,
                        "telegram_message_id": message_id,
                        "telegram_chat_id": delete_chat_id,
                        "cancellation_status": cancellation_status,
                        "telegram_delete_status": "already_missing",
                    },
                )
                await _mark_cancelled_cashout_inquiry_deleted(delete_chat_id, message_id)
                return CashoutTelegramDeletionResult("already_missing")
            await client.delete_messages(delete_chat_id, [message_id], revoke=True)
        finally:
            await client.disconnect()
    except Exception as error:
        logger.exception(
            "cashout_telegram_delete_failed",
            extra={
                "cashout_request_id": target.cashout_request_id,
                "telegram_message_id": message_id,
                "telegram_chat_id": chat_id,
                "cancellation_status": cancellation_status,
                "telegram_delete_status": "failed",
                "error": str(error),
            },
        )
        return CashoutTelegramDeletionResult("failed", str(error))

    logger.info(
        "cashout_telegram_delete_succeeded",
        extra={
            "cashout_request_id": target.cashout_request_id,
            "telegram_message_id": message_id,
            "telegram_chat_id": delete_chat_id,
            "cancellation_status": cancellation_status,
            "telegram_delete_status": "deleted",
        },
    )
    await _mark_cancelled_cashout_inquiry_deleted(delete_chat_id, message_id)
    return CashoutTelegramDeletionResult("deleted")


async def _mark_cancelled_cashout_inquiry_deleted(
    telegram_chat_id: int,
    telegram_message_id: int,
) -> None:
    from app.telegram.inquiry_ingestion import mark_inquiry_message_deleted
    from app.telegram.peer_ids import normalize_telegram_chat_id

    normalized_chat_id = normalize_telegram_chat_id(telegram_chat_id)
    if normalized_chat_id is not None:
        await mark_inquiry_message_deleted(
            telegram_chat_id=normalized_chat_id,
            telegram_message_id=telegram_message_id,
        )

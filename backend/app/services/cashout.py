from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.repositories.cashout import (
    CashoutAuditRecord,
    CashoutListPage,
    CashoutRepository,
)
from app.models.cashout import (
    CashoutAuditAction,
    CashoutRequest,
    CashoutRequestAudit,
    CashoutStatus,
    CashoutTelegramStatus,
)
from app.models.user import User, UserRole
from app.services.base import ApplicationService
from app.websocket.events import LiveEventType, event_broker

logger = get_logger(__name__)


class CashoutNotFoundError(Exception):
    """Raised when a cashout request does not exist."""


class CashoutAuthorizationError(Exception):
    """Raised when an actor cannot access a cashout operation."""


class CashoutStateConflictError(Exception):
    """Raised for invalid cashout workflow transitions."""


class CashoutIdempotencyConflictError(Exception):
    """Raised when one submission key is reused with different data."""


class CashoutService(ApplicationService):
    """Cashout creation, history, administration, and audit workflow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = CashoutRepository(session)

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
        return cashout

    async def complete(self, cashout_id: int, actor: User) -> CashoutRequest:
        self._require_admin(actor)
        async with self._session.begin():
            cashout = await self._get_locked(cashout_id)
            if cashout.status in (CashoutStatus.COMPLETED, CashoutStatus.CANCELLED):
                raise CashoutStateConflictError(
                    "This cashout is already completed or cancelled."
                )
            previous_status = cashout.status
            cashout.status = CashoutStatus.COMPLETED
            cashout.completed_by_staff_id = actor.id
            cashout.completed_at = datetime.now(UTC)
            await self._record_audit(
                cashout,
                action=CashoutAuditAction.COMPLETED,
                actor=actor,
                previous_value={"status": previous_status.value},
                new_value={
                    "status": cashout.status.value,
                    "completed_by_staff_id": actor.id,
                },
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
        async with self._session.begin():
            cashout = await self._get_locked(cashout_id)
            if cashout.status in (CashoutStatus.COMPLETED, CashoutStatus.CANCELLED):
                raise CashoutStateConflictError(
                    "This cashout is already completed or cancelled."
                )
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
            await self._session.refresh(cashout)
        await event_broker.publish(
            LiveEventType.CASHOUT_CANCELLED,
            cashout_id=cashout.id,
        )
        await _delete_cancelled_cashout_telegram_message(cashout)
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
    def _telegram_random_id(staff_id: int, idempotency_key: str) -> int:
        digest = hashlib.sha256(
            f"{staff_id}:{idempotency_key}".encode()
        ).digest()
        return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1) or 1


async def _delete_cancelled_cashout_telegram_message(cashout: CashoutRequest) -> None:
    message_id = cashout.telegram_message_id
    cashout_group_id: int | None = None
    if message_id is None:
        logger.info(
            "cashout_telegram_delete_missing",
            extra={
                "cashout_request_id": cashout.id,
                "reason_ignored": "no_telegram_message_id",
            },
        )
        return

    try:
        from telethon.tl import types  # type: ignore[import-untyped]

        from app.core.config import get_settings
        from app.telegram.client import create_telegram_client

        settings = get_settings()
        cashout_group_id = settings.telegram_cashout_group_id
        if cashout_group_id is None:
            logger.warning(
                "cashout_telegram_delete_failed",
                extra={
                    "cashout_request_id": cashout.id,
                    "telegram_message_id": message_id,
                    "reason_ignored": "missing_cashout_group_id",
                },
            )
            return

        client = create_telegram_client(settings)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                logger.warning(
                    "cashout_telegram_delete_failed",
                    extra={
                        "cashout_request_id": cashout.id,
                        "telegram_message_id": message_id,
                        "telegram_chat_id": cashout_group_id,
                        "reason_ignored": "telegram_session_unauthorized",
                    },
                )
                return
            message = await client.get_messages(cashout_group_id, ids=message_id)
            if message is None or isinstance(message, types.MessageEmpty):
                logger.info(
                    "cashout_telegram_delete_missing",
                    extra={
                        "cashout_request_id": cashout.id,
                        "telegram_message_id": message_id,
                        "telegram_chat_id": cashout_group_id,
                    },
                )
                return
            await client.delete_messages(cashout_group_id, [message_id], revoke=True)
        finally:
            await client.disconnect()
    except Exception:
        logger.exception(
            "cashout_telegram_delete_failed",
            extra={
                "cashout_request_id": cashout.id,
                "telegram_message_id": message_id,
                "telegram_chat_id": cashout_group_id,
            },
        )
        return

    logger.info(
        "cashout_telegram_delete_succeeded",
        extra={
            "cashout_request_id": cashout.id,
            "telegram_message_id": message_id,
            "telegram_chat_id": cashout_group_id,
        },
    )
    from app.telegram.inquiry_ingestion import mark_inquiry_message_deleted
    from app.telegram.peer_ids import normalize_telegram_chat_id

    normalized_chat_id = normalize_telegram_chat_id(cashout_group_id)
    if normalized_chat_id is not None:
        await mark_inquiry_message_deleted(
            telegram_chat_id=normalized_chat_id,
            telegram_message_id=message_id,
        )

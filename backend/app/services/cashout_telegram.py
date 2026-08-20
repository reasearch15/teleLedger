from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories.cashout import CashoutRepository
from app.db.repositories.cashout_partial_pending import CashoutPartialPendingRepository
from app.db.repositories.workflow_settings import CoadminTelegramWorkflowSettingsRepository
from app.models.cashout import (
    CashoutAuditAction,
    CashoutCompletionType,
    CashoutRequest,
    CashoutStatus,
)
from app.models.cashout_partial_pending import CashoutPartialPendingInput
from app.models.user import User
from app.services.cashout import (
    CashoutAuthorizationError,
    CashoutNotFoundError,
    CashoutService,
    CashoutStateConflictError,
    CashoutValidationError,
)
from app.telegram.cashout_bot.messages import (
    CashoutCallbackAction,
    CashoutTaskView,
    build_active_task_markup,
    decode_callback_data,
    format_cashout_task_card,
    format_partial_prompt_message,
)
from app.telegram.peer_ids import (
    authorize_configured_or_persisted_chat,
    chat_ids_equivalent,
    normalize_telegram_chat_id,
)
from app.telegram.staff_labels import format_actor_label

logger = get_logger(__name__)


class CashoutTelegramGateway(Protocol):
    """Injectable Telegram side effects for tests and runtime wiring."""

    async def answer_callback_query(
        self,
        *,
        query_id: int | str,
        text: str,
        alert: bool = False,
    ) -> None: ...

    async def edit_cashout_task_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
    ) -> None: ...

    async def delete_message(self, *, chat_id: int, message_id: int) -> bool: ...

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> int | None: ...

    async def send_cashout_task_message(
        self,
        *,
        chat_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]],
    ) -> int | None: ...

    async def get_updates(
        self,
        *,
        offset: int | None,
    ) -> list[object]: ...

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None: ...


@dataclass(frozen=True, slots=True)
class CashoutTelegramActionResult:
    """Outcome of one Telegram cashout bot action."""

    status: str
    cashout_id: int | None = None
    message: str | None = None
    cashout: CashoutRequest | None = None


class CashoutTelegramService:
    """Orchestrate Telegram cashout task interactions through CashoutService."""

    def __init__(
        self,
        session: object,
        *,
        gateway: CashoutTelegramGateway | None = None,
        partial_pending_ttl_seconds: int | None = None,
    ) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        assert isinstance(session, AsyncSession)
        self._session = session
        self._cashouts = CashoutRepository(session)
        self._pending = CashoutPartialPendingRepository(session)
        self._workflow = CoadminTelegramWorkflowSettingsRepository(session)
        self._cashout_service = CashoutService(session)
        self._gateway = gateway
        settings = get_settings()
        self._partial_pending_ttl_seconds = (
            partial_pending_ttl_seconds
            if partial_pending_ttl_seconds is not None
            else settings.cashout_partial_pending_ttl_seconds
        )

    async def handle_callback_query(
        self,
        *,
        query_id: int | str,
        callback_data: str,
        telegram_chat_id: int,
        telegram_user_id: int,
        telegram_username: str | None,
        message_id: int,
    ) -> CashoutTelegramActionResult:
        decoded = decode_callback_data(callback_data)
        if decoded is None:
            logger.info("cashout_bot_callback_invalid_format")
            await self._answer(query_id, "Unknown action.", alert=True)
            return CashoutTelegramActionResult(status="invalid_callback")

        cashout_id, action = decoded
        logger.info(
            "cashout_bot_callback_received",
            extra={
                "cashout_request_id": cashout_id,
                "telegram_chat_id": telegram_chat_id,
                "telegram_message_id": message_id,
                "telegram_user_id": telegram_user_id,
                "callback_action": action.value,
            },
        )
        try:
            async with self._session.begin():
                context = await self._resolve_callback_context(
                    cashout_id=cashout_id,
                    telegram_chat_id=telegram_chat_id,
                    message_id=message_id,
                )
        except CashoutAuthorizationError:
            logger.warning(
                "cashout_bot_callback_rejected",
                extra={
                    "cashout_request_id": cashout_id,
                    "telegram_chat_id": telegram_chat_id,
                    "telegram_message_id": message_id,
                    "telegram_user_id": telegram_user_id,
                    "callback_action": action.value,
                },
            )
            await self._answer(query_id, "This cashout is not available here.", alert=True)
            return CashoutTelegramActionResult(status="rejected", cashout_id=cashout_id)
        except CashoutNotFoundError:
            logger.warning(
                "cashout_bot_callback_not_found",
                extra={
                    "cashout_request_id": cashout_id,
                    "telegram_chat_id": telegram_chat_id,
                    "telegram_message_id": message_id,
                    "telegram_user_id": telegram_user_id,
                    "callback_action": action.value,
                },
            )
            await self._answer(query_id, "Cashout not found.", alert=True)
            return CashoutTelegramActionResult(status="not_found", cashout_id=cashout_id)

        cashout = context.cashout
        if cashout.status in (CashoutStatus.COMPLETED, CashoutStatus.CANCELLED):
            await self.sync_terminal_task(
                cashout,
                telegram_user_id=telegram_user_id,
                telegram_username=telegram_username,
            )
            label = (
                "already completed"
                if cashout.status == CashoutStatus.COMPLETED
                else "already cancelled"
            )
            await self._answer(query_id, f"This cashout is {label}.", alert=True)
            return CashoutTelegramActionResult(
                status=label.replace(" ", "_"),
                cashout_id=cashout.id,
                cashout=cashout,
            )

        if action == CashoutCallbackAction.FULL:
            return await self._handle_full_payment(
                query_id=query_id,
                cashout=cashout,
                coadmin_id=context.coadmin_id,
                expected_chat_id=context.expected_chat_id,
                telegram_user_id=telegram_user_id,
                telegram_username=telegram_username,
            )

        return await self._handle_partial_button(
            query_id=query_id,
            cashout=cashout,
            coadmin_id=context.coadmin_id,
            expected_chat_id=context.expected_chat_id,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
        )

    async def handle_partial_amount_message(
        self,
        *,
        telegram_chat_id: int,
        telegram_user_id: int,
        telegram_username: str | None,
        text: str,
    ) -> CashoutTelegramActionResult | None:
        normalized = text.strip()
        if not normalized:
            return None

        now = datetime.now(UTC)
        async with self._session.begin():
            pending = await self._pending.get_active_for_user_in_chat(
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                now=now,
                for_update=True,
            )
            if pending is None:
                return None

            if normalized.casefold() == "cancel":
                await self._pending.delete_for_cashout(pending.cashout_id)
                return CashoutTelegramActionResult(
                    status="partial_cancelled",
                    cashout_id=pending.cashout_id,
                    message="Partial payment entry cancelled.",
                )

            cashout = await self._cashouts.get_by_id_for_coadmin(
                pending.cashout_id,
                pending.coadmin_id,
                for_update=True,
            )
            if cashout is None:
                await self._pending.delete_for_cashout(pending.cashout_id)
                return CashoutTelegramActionResult(
                    status="not_found",
                    cashout_id=pending.cashout_id,
                )

            if cashout.status in (CashoutStatus.COMPLETED, CashoutStatus.CANCELLED):
                await self._pending.delete_for_cashout(pending.cashout_id)
                return CashoutTelegramActionResult(
                    status="terminal",
                    cashout_id=cashout.id,
                    message="This cashout is no longer open.",
                    cashout=cashout,
                )

            try:
                paid_amount = self._parse_partial_amount(normalized, cashout.amount)
            except CashoutValidationError as error:
                return CashoutTelegramActionResult(
                    status="invalid_amount",
                    cashout_id=cashout.id,
                    message=str(error),
                )

        try:
            completed = await self._cashout_service.complete_from_telegram(
                cashout.id,
                coadmin_id=pending.coadmin_id,
                expected_chat_id=pending.telegram_chat_id,
                completion_type=CashoutCompletionType.PARTIAL,
                actual_paid_amount=paid_amount,
                telegram_user_id=telegram_user_id,
                telegram_username=telegram_username,
            )
        except CashoutStateConflictError:
            async with self._session.begin():
                await self._pending.delete_for_cashout(cashout.id)
                refreshed = await self._cashouts.get_by_id_for_update(cashout.id)
            return CashoutTelegramActionResult(
                status="already_completed",
                cashout_id=cashout.id,
                cashout=refreshed,
            )
        async with self._session.begin():
            await self._pending.delete_for_cashout(cashout.id)

        await self.sync_terminal_task(
            completed,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
        )
        return CashoutTelegramActionResult(
            status="completed_partial",
            cashout_id=completed.id,
            cashout=completed,
        )

    async def sync_cancelled_task(
        self,
        cashout: CashoutRequest,
        *,
        prefer_delete: bool = True,
    ) -> str:
        """Delete or edit the Telegram task after Atlas cancellation."""
        chat_id = cashout.telegram_chat_id
        message_id = cashout.telegram_message_id
        if chat_id is None or message_id is None:
            return "no_linked_message"

        gateway = self._require_gateway()
        if prefer_delete:
            try:
                deleted = await gateway.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                logger.exception(
                    "cashout_telegram_delete_failed",
                    extra={
                        "cashout_request_id": cashout.id,
                        "telegram_chat_id": chat_id,
                        "telegram_message_id": message_id,
                        "telegram_delete_status": "failed",
                    },
                )
                deleted = False
            if deleted:
                logger.info(
                    "cashout_telegram_delete_succeeded",
                    extra={
                        "cashout_request_id": cashout.id,
                        "telegram_chat_id": chat_id,
                        "telegram_message_id": message_id,
                        "telegram_delete_status": "deleted",
                    },
                )
                return "deleted"

        view = await self._build_persisted_view(cashout)
        try:
            await gateway.edit_cashout_task_message(
                chat_id=chat_id,
                message_id=message_id,
                text=format_cashout_task_card(view),
                buttons=None,
            )
        except Exception:
            logger.exception(
                "cashout_telegram_cancel_edit_failed",
                extra={
                    "cashout_request_id": cashout.id,
                    "telegram_chat_id": chat_id,
                    "telegram_message_id": message_id,
                    "telegram_delete_status": "failed",
                },
            )
            return "failed"
        logger.info(
            "cashout_telegram_cancel_edit_succeeded",
            extra={
                "cashout_request_id": cashout.id,
                "telegram_chat_id": chat_id,
                "telegram_message_id": message_id,
                "telegram_delete_status": "edited_cancelled",
            },
        )
        return "edited_cancelled"

    async def render_active_task(
        self,
        cashout: CashoutRequest,
        *,
        requested_by: str,
    ) -> tuple[str, list[list[tuple[str, str]]]]:
        view = await self._build_persisted_view(
            cashout,
            requested_by=requested_by,
        )
        return (
            format_cashout_task_card(view),
            build_active_task_markup(cashout.id),
        )

    async def _handle_full_payment(
        self,
        *,
        query_id: int | str,
        cashout: CashoutRequest,
        coadmin_id: int,
        expected_chat_id: int,
        telegram_user_id: int,
        telegram_username: str | None,
    ) -> CashoutTelegramActionResult:
        try:
            completed = await self._cashout_service.complete_from_telegram(
                cashout.id,
                coadmin_id=coadmin_id,
                expected_chat_id=expected_chat_id,
                completion_type=CashoutCompletionType.FULL,
                telegram_user_id=telegram_user_id,
                telegram_username=telegram_username,
            )
        except CashoutStateConflictError:
            async with self._session.begin():
                refreshed = await self._cashouts.get_by_id_for_update(cashout.id)
            await self.sync_terminal_task(
                refreshed,
                telegram_user_id=telegram_user_id,
                telegram_username=telegram_username,
            )
            await self._answer(query_id, "This cashout is already completed or cancelled.")
            return CashoutTelegramActionResult(
                status="already_completed",
                cashout_id=cashout.id,
                cashout=refreshed,
            )
        async with self._session.begin():
            await self._pending.delete_for_cashout(cashout.id)

        await self.sync_terminal_task(
            completed,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
        )
        await self._answer(query_id, "Cashout completed (Full Payment).")
        logger.info(
            "cashout_bot_callback_completed_full",
            extra={
                "cashout_request_id": completed.id,
                "telegram_chat_id": expected_chat_id,
                "telegram_user_id": telegram_user_id,
            },
        )
        return CashoutTelegramActionResult(
            status="completed_full",
            cashout_id=completed.id,
            cashout=completed,
        )

    async def _handle_partial_button(
        self,
        *,
        query_id: int | str,
        cashout: CashoutRequest,
        coadmin_id: int,
        expected_chat_id: int,
        telegram_user_id: int,
        telegram_username: str | None,
    ) -> CashoutTelegramActionResult:
        del telegram_username
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self._partial_pending_ttl_seconds)
        prompt_message_id: int | None = None
        if cashout.request_number is not None:
            prompt_message_id = await self._send_prompt(
                chat_id=expected_chat_id,
                request_number=cashout.request_number,
            )

        async with self._session.begin():
            await self._pending.upsert_pending(
                CashoutPartialPendingInput(
                    cashout_id=cashout.id,
                    coadmin_id=coadmin_id,
                    telegram_user_id=telegram_user_id,
                    telegram_chat_id=expected_chat_id,
                    prompt_message_id=prompt_message_id,
                    expires_at=expires_at,
                )
            )

        await self._answer(
            query_id,
            f"Reply with the amount paid for {cashout.request_number}. Send cancel to abort.",
        )
        logger.info(
            "cashout_bot_partial_pending_created",
            extra={
                "cashout_request_id": cashout.id,
                "telegram_chat_id": expected_chat_id,
                "telegram_user_id": telegram_user_id,
            },
        )
        return CashoutTelegramActionResult(
            status="partial_pending",
            cashout_id=cashout.id,
            message="partial_pending",
        )

    async def _resolve_callback_context(
        self,
        *,
        cashout_id: int,
        telegram_chat_id: int,
        message_id: int,
    ) -> _CallbackContext:
        normalized_chat = normalize_telegram_chat_id(telegram_chat_id)
        if normalized_chat is None:
            raise CashoutAuthorizationError("invalid chat")

        cashout = await self._cashouts.get_by_telegram_message_for_update(
            telegram_message_id=message_id,
            telegram_chat_id=normalized_chat,
        )
        if cashout is None or cashout.id != cashout_id:
            raise CashoutNotFoundError(f"Cashout {cashout_id} was not found")

        if cashout.coadmin_id is None:
            raise CashoutAuthorizationError("cashout missing coadmin")

        configured_chat_id = await self._expected_chat_for_coadmin(cashout.coadmin_id)
        if not authorize_configured_or_persisted_chat(
            incoming_chat_id=normalized_chat,
            configured_chat_id=configured_chat_id,
            persisted_chat_id=cashout.telegram_chat_id,
        ):
            raise CashoutAuthorizationError("wrong group")

        if (
            cashout.telegram_chat_id is not None
            and not chat_ids_equivalent(cashout.telegram_chat_id, normalized_chat)
        ):
            raise CashoutAuthorizationError("chat mismatch")

        return _CallbackContext(
            cashout=cashout,
            coadmin_id=cashout.coadmin_id,
            expected_chat_id=normalized_chat,
        )

    async def _expected_chat_for_coadmin(self, coadmin_id: int) -> int | None:
        settings_row = await self._workflow.get_for_coadmin(coadmin_id)
        if settings_row is not None and settings_row.cashout_group_id is not None:
            return normalize_telegram_chat_id(settings_row.cashout_group_id)
        return normalize_telegram_chat_id(get_settings().telegram_cashout_group_id)

    async def sync_terminal_task(
        self,
        cashout: CashoutRequest | None,
        *,
        telegram_user_id: int | None = None,
        telegram_username: str | None = None,
    ) -> str:
        return await self.sync_persisted_task(
            cashout,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
        )

    async def sync_persisted_task(
        self,
        cashout: CashoutRequest | None,
        *,
        telegram_user_id: int | None = None,
        telegram_username: str | None = None,
    ) -> str:
        """Rebuild the Telegram cashout card from canonical persisted state."""
        if cashout is None:
            return "not_found"
        if cashout.telegram_chat_id is None or cashout.telegram_message_id is None:
            return "no_linked_message"
        view = await self._build_persisted_view(
            cashout,
            fallback_telegram_user_id=telegram_user_id,
            fallback_telegram_username=telegram_username,
        )
        text = format_cashout_task_card(view)
        buttons = (
            None
            if cashout.status in (CashoutStatus.COMPLETED, CashoutStatus.CANCELLED)
            else build_active_task_markup(cashout.id)
        )
        try:
            await self._require_gateway().edit_cashout_task_message(
                chat_id=cashout.telegram_chat_id,
                message_id=cashout.telegram_message_id,
                text=text,
                buttons=buttons,
            )
        except Exception as error:
            await self._record_terminal_sync_error(cashout, error)
            logger.exception(
                "cashout_telegram_persisted_sync_failed",
                extra={
                    "cashout_request_id": cashout.id,
                    "telegram_chat_id": cashout.telegram_chat_id,
                    "telegram_message_id": cashout.telegram_message_id,
                    "cashout_status": cashout.status.value,
                },
            )
            return "failed"
        await self._clear_terminal_sync_error(cashout)
        logger.info(
            "cashout_telegram_persisted_sync_succeeded",
            extra={
                "cashout_request_id": cashout.id,
                "telegram_chat_id": cashout.telegram_chat_id,
                "telegram_message_id": cashout.telegram_message_id,
                "cashout_status": cashout.status.value,
            },
        )
        if cashout.status in (CashoutStatus.COMPLETED, CashoutStatus.CANCELLED):
            return "edited_terminal"
        return "edited_active"

    async def _lookup_requested_by(self, staff_id: int | None) -> str:
        label = await self._staff_actor_label(staff_id)
        return label or ""

    async def _staff_actor_label(self, staff_id: int | None) -> str | None:
        if staff_id is None:
            return None
        username = await self._session.scalar(select(User.username).where(User.id == staff_id))
        return format_actor_label(username=username)

    async def _format_completed_by(
        self,
        cashout: CashoutRequest,
        *,
        fallback_telegram_user_id: int | None,
        fallback_telegram_username: str | None = None,
    ) -> str:
        staff_label = await self._staff_actor_label(cashout.completed_by_staff_id)
        if staff_label:
            return staff_label
        audit_label = await self._completed_by_from_audit(
            cashout.id,
            fallback_telegram_user_id=fallback_telegram_user_id,
            fallback_telegram_username=fallback_telegram_username,
        )
        if audit_label:
            return audit_label
        live_label = format_actor_label(
            telegram_username=fallback_telegram_username,
            telegram_user_id=fallback_telegram_user_id,
        )
        return live_label or "Telegram bot"

    async def _completed_by_from_audit(
        self,
        cashout_id: int,
        *,
        fallback_telegram_user_id: int | None,
        fallback_telegram_username: str | None,
    ) -> str | None:
        records = await self._cashouts.list_audit(cashout_id)
        for record in reversed(records):
            if record.audit.action != CashoutAuditAction.TELEGRAM_BOT_COMPLETED:
                continue
            payload = record.audit.new_value or {}
            return format_actor_label(
                username=record.actor_username,
                telegram_username=(
                    str(payload.get("telegram_username") or "")
                    or fallback_telegram_username
                ),
                telegram_user_id=(
                    payload.get("telegram_user_id")
                    if payload.get("telegram_user_id") is not None
                    else fallback_telegram_user_id
                ),
            )
        return None

    async def _lookup_cancelled_by(self, cashout_id: int) -> str | None:
        records = await self._cashouts.list_audit(cashout_id)
        for record in reversed(records):
            if record.audit.action == CashoutAuditAction.CANCELLED:
                return format_actor_label(username=record.actor_username)
        return None

    async def _build_persisted_view(
        self,
        cashout: CashoutRequest,
        *,
        requested_by: str | None = None,
        fallback_telegram_user_id: int | None = None,
        fallback_telegram_username: str | None = None,
    ) -> CashoutTaskView:
        creator = requested_by if requested_by is not None else await self._lookup_requested_by(
            cashout.created_by_staff_id
        )
        completed_by = None
        if cashout.status == CashoutStatus.COMPLETED:
            completed_by = await self._format_completed_by(
                cashout,
                fallback_telegram_user_id=fallback_telegram_user_id,
                fallback_telegram_username=fallback_telegram_username,
            )
        cancelled_by = None
        if cashout.status == CashoutStatus.CANCELLED:
            cancelled_by = await self._lookup_cancelled_by(cashout.id)
        return self._build_view(
            cashout,
            requested_by=creator,
            completed_by_label=completed_by,
            cancelled_by_label=cancelled_by,
        )

    @staticmethod
    def _build_view(
        cashout: CashoutRequest,
        *,
        requested_by: str,
        completed_by_label: str | None,
        cancelled_by_label: str | None = None,
    ) -> CashoutTaskView:
        if cashout.request_number is None:
            raise RuntimeError("Cashout request_number is required for Telegram rendering")
        return CashoutTaskView(
            cashout_id=cashout.id,
            request_number=cashout.request_number,
            player_tag=cashout.player_tag,
            requested_amount=Decimal(cashout.amount),
            status=cashout.status,
            requested_by=requested_by,
            created_at=cashout.created_at,
            notes=cashout.notes,
            completion_type=cashout.completion_type,
            actual_paid_amount=(
                Decimal(cashout.actual_paid_amount)
                if cashout.actual_paid_amount is not None
                else None
            ),
            completed_by_label=completed_by_label,
            completed_at=cashout.completed_at,
            cancelled_by_label=cancelled_by_label,
            cancelled_at=cashout.cancelled_at,
        )

    @staticmethod
    def _parse_partial_amount(raw: str, requested_amount: Decimal) -> Decimal:
        normalized = raw.strip().replace(",", "").replace("$", "")
        try:
            paid = Decimal(normalized).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as error:
            raise CashoutValidationError("Amount must be numeric.") from error
        if paid <= Decimal("0.00"):
            raise CashoutValidationError("Partial amount must be greater than zero.")
        requested = Decimal(requested_amount).quantize(Decimal("0.01"))
        if paid >= requested:
            raise CashoutValidationError(
                "Partial amount must be less than the requested amount. Use Full Payment instead."
            )
        return paid

    async def _answer(
        self,
        query_id: int | str,
        text: str,
        *,
        alert: bool = False,
    ) -> None:
        gateway = self._gateway
        if gateway is None:
            return
        try:
            await gateway.answer_callback_query(query_id=query_id, text=text, alert=alert)
        except Exception:
            logger.exception("cashout_bot_callback_answer_failed")

    async def _send_prompt(self, *, chat_id: int, request_number: str) -> int | None:
        gateway = self._gateway
        if gateway is None:
            return None
        return await gateway.send_message(
            chat_id=chat_id,
            text=format_partial_prompt_message(request_number),
        )

    def _require_gateway(self) -> CashoutTelegramGateway:
        if self._gateway is None:
            raise RuntimeError("CashoutTelegramGateway is required for Telegram mutations")
        return self._gateway

    async def _record_terminal_sync_error(
        self,
        cashout: CashoutRequest,
        error: Exception,
    ) -> None:
        cashout.telegram_last_error = f"terminal_sync_failed: {error}"[:2000]
        await self._session.commit()

    async def _clear_terminal_sync_error(self, cashout: CashoutRequest) -> None:
        if cashout.telegram_last_error and cashout.telegram_last_error.startswith(
            "terminal_sync_failed:"
        ):
            cashout.telegram_last_error = None
            await self._session.commit()


@dataclass(frozen=True, slots=True)
class _CallbackContext:
    cashout: CashoutRequest
    coadmin_id: int
    expected_chat_id: int

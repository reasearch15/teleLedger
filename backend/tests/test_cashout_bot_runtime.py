from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.cashout import (
    CashoutAuditAction,
    CashoutCompletionType,
    CashoutRequest,
    CashoutRequestAudit,
    CashoutStatus,
    CashoutTelegramStatus,
)
from app.models.cashout_partial_pending import CashoutPartialPendingInput
from app.models.user import User, UserRole
from app.models.workflow_settings import CoadminTelegramWorkflowSettings
from app.services.cashout_telegram import CashoutTelegramService
from app.telegram import (
    cashout_delivery,
    cashout_operational_reconciliation,
    cashout_reactions,
)
from app.telegram.cashout_bot.api import (
    TelegramBotApiError,
    TelegramBotApiGateway,
    TelegramBotFailureClass,
    TelegramBotUpdate,
)
from app.telegram.cashout_bot.messages import (
    CashoutCallbackAction,
    build_active_task_markup,
    encode_callback_data,
)
from app.telegram.cashout_bot.updates import run_cashout_bot_update_loop

test_engine = create_async_engine(
    "sqlite+aiosqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionFactory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class FakeBotGateway:
    def __init__(self, *, delete_ok: bool = True, edit_ok: bool = True) -> None:
        self.delete_ok = delete_ok
        self.edit_ok = edit_ok
        self.sent_cashouts: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.webhook_cleared = False

    async def send_cashout_task_message(
        self,
        *,
        chat_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]],
    ) -> int:
        self.sent_cashouts.append({"chat_id": chat_id, "text": text, "buttons": buttons})
        return 555

    async def answer_callback_query(
        self,
        *,
        query_id: int | str,
        text: str,
        alert: bool = False,
    ) -> None:
        self.answers.append({"query_id": query_id, "text": text, "alert": alert})

    async def edit_cashout_task_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
    ) -> None:
        if not self.edit_ok:
            raise RuntimeError("edit failed")
        self.edits.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text, "buttons": buttons}
        )

    async def delete_message(self, *, chat_id: int, message_id: int) -> bool:
        self.deletes.append({"chat_id": chat_id, "message_id": message_id})
        return self.delete_ok

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> int:
        self.messages.append(
            {"chat_id": chat_id, "text": text, "reply_to_message_id": reply_to_message_id}
        )
        return 777

    async def get_updates(self, *, offset: int | None) -> list[object]:
        del offset
        return []

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        del drop_pending_updates
        self.webhook_cleared = True


class OneCallbackThenCancelGateway(FakeBotGateway):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def get_updates(self, *, offset: int | None) -> list[TelegramBotUpdate]:
        self.calls += 1
        if self.calls > 1:
            raise asyncio.CancelledError
        assert offset is None
        return [
            TelegramBotUpdate(
                update_id=100,
                payload={
                    "callback_query": {
                        "id": "q-loop",
                        "from": {"id": 9001, "username": "operator"},
                        "message": {
                            "message_id": 555,
                            "chat": {"id": -1001234567890, "type": "supergroup"},
                        },
                        "data": encode_callback_data(1, CashoutCallbackAction.FULL),
                    }
                },
            )
        ]


@pytest_asyncio.fixture(autouse=True)
async def reset_database(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with TestSessionFactory() as session:
        session.add_all(
            [
                User(
                    id=1,
                    username="admin",
                    password_hash="not-used",
                    role=UserRole.ADMIN,
                    is_active=True,
                    staff_color="#111111",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
                User(
                    id=10,
                    username="coadmin",
                    password_hash="not-used",
                    role=UserRole.COADMIN,
                    is_active=True,
                    staff_color="#222222",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
                User(
                    id=11,
                    username="other_coadmin",
                    password_hash="not-used",
                    role=UserRole.COADMIN,
                    is_active=True,
                    staff_color="#333333",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
                User(
                    id=42,
                    username="sarah",
                    password_hash="not-used",
                    role=UserRole.STAFF,
                    is_active=True,
                    staff_color="#2563EB",
                    coadmin_id=10,
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ]
        )
        session.add_all(
            [
                CoadminTelegramWorkflowSettings(
                    coadmin_id=10,
                    cashout_group_id=-1001234567890,
                ),
                CoadminTelegramWorkflowSettings(
                    coadmin_id=11,
                    cashout_group_id=-1009999999999,
                ),
            ]
        )
        await session.commit()
    monkeypatch.setattr(cashout_delivery, "SessionFactory", TestSessionFactory)
    monkeypatch.setattr(
        cashout_operational_reconciliation,
        "SessionFactory",
        TestSessionFactory,
    )
    monkeypatch.setattr(cashout_reactions, "SessionFactory", TestSessionFactory)
    monkeypatch.setattr(
        "app.websocket.cross_process.notify_live_event",
        AsyncMock(return_value=None),
    )
    yield
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await test_engine.dispose()


async def seed_cashout(
    cashout_id: int = 1,
    *,
    status: CashoutStatus = CashoutStatus.SENT,
    telegram_status: CashoutTelegramStatus = CashoutTelegramStatus.SENT,
    coadmin_id: int = 10,
    telegram_chat_id: int = -1001234567890,
    telegram_message_id: int = 555,
) -> None:
    timestamp = datetime(2026, 7, 6, 20, 35, tzinfo=UTC)
    async with TestSessionFactory() as session:
        session.add(
            CashoutRequest(
                id=cashout_id,
                request_number=f"CR-{cashout_id:06d}",
                idempotency_key=f"00000000-0000-0000-0000-{cashout_id:012d}",
                player_tag="ABC12345",
                amount=Decimal("250.00"),
                notes="VIP Player",
                status=status,
                telegram_status=telegram_status,
                telegram_message_id=telegram_message_id,
                telegram_chat_id=telegram_chat_id,
                telegram_random_id=10_000 + cashout_id,
                telegram_sent_at=timestamp,
                created_by_staff_id=42,
                coadmin_id=coadmin_id,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
        session.add(
            CashoutRequestAudit(
                cashout_request_id=cashout_id,
                action=CashoutAuditAction.TELEGRAM_SENT,
                actor_user_id=None,
                previous_value=None,
                new_value={"telegram_message_id": telegram_message_id},
            )
        )
        await session.commit()


async def callback(action: CashoutCallbackAction, gateway: FakeBotGateway) -> Any:
    async with TestSessionFactory() as session:
        return await CashoutTelegramService(session, gateway=gateway).handle_callback_query(
            query_id="q1",
            callback_data=encode_callback_data(1, action),
            telegram_chat_id=-1001234567890,
            telegram_user_id=9001,
            telegram_username="operator",
            message_id=555,
        )


async def cashout() -> CashoutRequest:
    async with TestSessionFactory() as session:
        row = await session.get(CashoutRequest, 1)
        assert row is not None
        return row


async def submit_partial_amount(
    gateway: FakeBotGateway,
    *,
    telegram_user_id: int = 9001,
    text: str = "100",
) -> Any:
    async with TestSessionFactory() as session:
        return await CashoutTelegramService(
            session,
            gateway=gateway,
        ).handle_partial_amount_message(
            telegram_chat_id=-1001234567890,
            telegram_user_id=telegram_user_id,
            telegram_username="operator",
            text=text,
        )


@pytest.mark.asyncio
async def test_new_cashout_produces_bot_message_with_buttons() -> None:
    await seed_cashout(
        status=CashoutStatus.PENDING,
        telegram_status=CashoutTelegramStatus.PENDING,
        telegram_message_id=None,
    )
    gateway = FakeBotGateway()

    processed = await cashout_delivery.deliver_next_cashout(
        object(),
        "group",
        telegram_chat_id=-1001234567890,
        bot_gateway=gateway,
    )

    assert processed is True
    assert gateway.sent_cashouts[0]["chat_id"] == -1001234567890
    assert "Request ID: CR-000001" in gateway.sent_cashouts[0]["text"]
    assert "Requested Amount:" in gateway.sent_cashouts[0]["text"]
    assert gateway.sent_cashouts[0]["buttons"] == build_active_task_markup(1)
    assert (await cashout()).telegram_message_id == 555


@pytest.mark.asyncio
async def test_full_payment_uses_authoritative_service_and_renders_completed() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()

    result = await callback(CashoutCallbackAction.FULL, gateway)

    stored = await cashout()
    assert result.status == "completed_full"
    assert stored.status == CashoutStatus.COMPLETED
    assert stored.completion_type == CashoutCompletionType.FULL
    assert stored.actual_paid_amount == Decimal("250.00")
    assert gateway.edits[-1]["buttons"] is None
    assert "Completed - Full Payment" in gateway.edits[-1]["text"]
    assert "Actual Paid Amount:\n$250.00" in gateway.edits[-1]["text"]


@pytest.mark.asyncio
async def test_update_loop_routes_matching_callback_to_authoritative_service() -> None:
    await seed_cashout()
    gateway = OneCallbackThenCancelGateway()

    with pytest.raises(asyncio.CancelledError):
        await run_cashout_bot_update_loop(
            gateway,
            session_factory=TestSessionFactory,
            report=lambda _: None,
        )

    stored = await cashout()
    assert gateway.webhook_cleared is True
    assert stored.status == CashoutStatus.COMPLETED
    assert stored.completion_type == CashoutCompletionType.FULL
    assert gateway.answers[-1]["query_id"] == "q-loop"
    assert gateway.edits[-1]["buttons"] is None


@pytest.mark.asyncio
async def test_gateway_get_updates_read_timeout_returns_empty_poll() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("idle long poll", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = TelegramBotApiGateway(token="123:test-token", client=client)
        updates = await gateway.get_updates(offset=None)

    assert updates == []
    assert calls == 1


@pytest.mark.asyncio
async def test_update_loop_routes_callback_after_prior_read_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_cashout()
    get_updates_calls = 0

    async def fast_sleep(delay: float) -> None:
        assert delay >= 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal get_updates_calls
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "deleteWebhook":
            return httpx.Response(200, json={"ok": True, "result": True})
        if method == "getUpdates":
            get_updates_calls += 1
            if get_updates_calls == 1:
                raise httpx.ReadTimeout("idle long poll", request=request)
            if get_updates_calls == 2:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "result": [
                            {
                                "update_id": 100,
                                "callback_query": {
                                    "id": "q-after-timeout",
                                    "from": {"id": 9001, "username": "operator"},
                                    "message": {
                                        "message_id": 555,
                                        "chat": {
                                            "id": -1001234567890,
                                            "type": "supergroup",
                                        },
                                    },
                                    "data": encode_callback_data(
                                        1,
                                        CashoutCallbackAction.FULL,
                                    ),
                                },
                            }
                        ],
                    },
                )
            raise asyncio.CancelledError
        if method in {"answerCallbackQuery", "editMessageText"}:
            return httpx.Response(200, json={"ok": True, "result": True})
        raise AssertionError(f"Unexpected Bot API method {method}")

    monkeypatch.setattr("app.telegram.cashout_bot.updates.asyncio.sleep", fast_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = TelegramBotApiGateway(token="123:test-token", client=client)
        with pytest.raises(asyncio.CancelledError):
            await run_cashout_bot_update_loop(
                gateway,
                session_factory=TestSessionFactory,
                report=lambda _: None,
            )

    stored = await cashout()
    assert get_updates_calls == 3
    assert stored.status == CashoutStatus.COMPLETED
    assert stored.completion_type == CashoutCompletionType.FULL


@pytest.mark.asyncio
async def test_update_loop_fatal_bot_auth_error_still_fails() -> None:
    class AuthFailureGateway(FakeBotGateway):
        async def get_updates(self, *, offset: int | None) -> list[object]:
            del offset
            raise TelegramBotApiError(
                "Unauthorized",
                failure_class=TelegramBotFailureClass.CONFIGURATION,
                status_code=401,
            )

    with pytest.raises(TelegramBotApiError):
        await run_cashout_bot_update_loop(
            AuthFailureGateway(),
            session_factory=TestSessionFactory,
            report=lambda _: None,
        )


@pytest.mark.asyncio
async def test_httpx_bot_api_logs_redact_token(caplog: pytest.LogCaptureFixture) -> None:
    token = "123456:REAL_SECRET_TOKEN"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": []})

    caplog.set_level(logging.INFO, logger="httpx")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = TelegramBotApiGateway(token=token, client=client)
        await gateway.get_updates(offset=None)

    assert token not in caplog.text
    assert f"/bot{token}/" not in caplog.text
    assert "/bot<redacted-bot-token>/getUpdates" in caplog.text


@pytest.mark.asyncio
async def test_partial_button_enters_pending_amount_state() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()

    result = await callback(CashoutCallbackAction.PARTIAL, gateway)

    assert result.status == "partial_pending"
    assert "Enter the amount actually paid" in gateway.messages[-1]["text"]
    async with TestSessionFactory() as session:
        pending = await session.scalar(select(CashoutPartialPendingInput))
    assert pending is not None
    assert pending.cashout_id == 1
    assert pending.telegram_user_id == 9001


@pytest.mark.asyncio
async def test_valid_partial_amount_uses_authoritative_service() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()
    await callback(CashoutCallbackAction.PARTIAL, gateway)

    result = await submit_partial_amount(gateway)

    stored = await cashout()
    assert result is not None
    assert result.status == "completed_partial"
    assert stored.status == CashoutStatus.COMPLETED
    assert stored.completion_type == CashoutCompletionType.PARTIAL
    assert stored.actual_paid_amount == Decimal("100.00")
    assert "Completed - Partial Payment" in gateway.edits[-1]["text"]
    assert "Unpaid Difference:\n$150.00" in gateway.edits[-1]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", ["0", "-1", "250", "300", "abc"])
async def test_invalid_partial_amounts_are_rejected(amount: str) -> None:
    await seed_cashout()
    gateway = FakeBotGateway()
    await callback(CashoutCallbackAction.PARTIAL, gateway)

    result = await submit_partial_amount(gateway, text=amount)

    assert result is not None
    assert result.status == "invalid_amount"
    assert (await cashout()).status == CashoutStatus.SENT


@pytest.mark.asyncio
async def test_only_initiating_user_can_submit_partial_amount() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()
    await callback(CashoutCallbackAction.PARTIAL, gateway)

    result = await submit_partial_amount(gateway, telegram_user_id=1234)

    assert result is None
    assert (await cashout()).status == CashoutStatus.SENT


@pytest.mark.asyncio
async def test_expired_pending_partial_state_is_rejected() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()
    await callback(CashoutCallbackAction.PARTIAL, gateway)
    async with TestSessionFactory() as session, session.begin():
        pending = await session.scalar(select(CashoutPartialPendingInput))
        assert pending is not None
        pending.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    result = await submit_partial_amount(gateway)

    assert result is None
    assert (await cashout()).status == CashoutStatus.SENT


@pytest.mark.asyncio
async def test_terminal_cashout_cannot_be_completed_again() -> None:
    await seed_cashout(status=CashoutStatus.COMPLETED)
    gateway = FakeBotGateway()

    result = await callback(CashoutCallbackAction.FULL, gateway)

    assert result.status == "already_completed"
    assert "already completed" in gateway.answers[-1]["text"]


@pytest.mark.asyncio
async def test_full_vs_full_race_results_in_one_completion() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()

    first = await callback(CashoutCallbackAction.FULL, gateway)
    second = await callback(CashoutCallbackAction.FULL, gateway)

    assert first.status == "completed_full"
    assert second.status == "already_completed"
    async with TestSessionFactory() as session:
        actions = list(await session.scalars(select(CashoutRequestAudit.action)))
    assert actions.count(CashoutAuditAction.TELEGRAM_BOT_COMPLETED) == 1


@pytest.mark.asyncio
async def test_full_vs_partial_race_results_in_one_completion() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()
    await callback(CashoutCallbackAction.PARTIAL, gateway)
    full = await callback(CashoutCallbackAction.FULL, gateway)

    result = await submit_partial_amount(gateway)

    assert full.status == "completed_full"
    assert result is None
    assert (await cashout()).completion_type == CashoutCompletionType.FULL


@pytest.mark.asyncio
async def test_cancelled_cashout_cannot_be_completed_by_telegram() -> None:
    await seed_cashout(status=CashoutStatus.CANCELLED)
    gateway = FakeBotGateway()

    result = await callback(CashoutCallbackAction.FULL, gateway)

    assert result.status == "already_cancelled"
    assert "already cancelled" in gateway.answers[-1]["text"]


@pytest.mark.asyncio
async def test_atlas_cancellation_deletes_telegram_task() -> None:
    await seed_cashout(status=CashoutStatus.CANCELLED)
    gateway = FakeBotGateway(delete_ok=True)
    stored = await cashout()

    async with TestSessionFactory() as session:
        status = await CashoutTelegramService(session, gateway=gateway).sync_cancelled_task(stored)

    assert status == "deleted"
    assert gateway.deletes == [{"chat_id": -1001234567890, "message_id": 555}]


@pytest.mark.asyncio
async def test_delete_failure_falls_back_to_cancelled_edit() -> None:
    await seed_cashout(status=CashoutStatus.CANCELLED)
    gateway = FakeBotGateway(delete_ok=False)
    stored = await cashout()

    async with TestSessionFactory() as session:
        status = await CashoutTelegramService(session, gateway=gateway).sync_cancelled_task(stored)

    assert status == "edited_cancelled"
    assert "CASHOUT CANCELLED" in gateway.edits[-1]["text"]


@pytest.mark.asyncio
async def test_delete_and_edit_failure_is_controlled() -> None:
    await seed_cashout(status=CashoutStatus.CANCELLED)
    gateway = FakeBotGateway(delete_ok=False, edit_ok=False)
    stored = await cashout()

    async with TestSessionFactory() as session:
        status = await CashoutTelegramService(session, gateway=gateway).sync_cancelled_task(stored)

    assert status == "failed"
    assert (await cashout()).status == CashoutStatus.CANCELLED


@pytest.mark.asyncio
async def test_cross_coadmin_callback_is_rejected() -> None:
    await seed_cashout(coadmin_id=11)
    gateway = FakeBotGateway()

    result = await callback(CashoutCallbackAction.FULL, gateway)

    assert result.status == "rejected"
    assert (await cashout()).status == CashoutStatus.SENT


@pytest.mark.asyncio
async def test_wrong_telegram_group_is_rejected() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()

    async with TestSessionFactory() as session:
        result = await CashoutTelegramService(session, gateway=gateway).handle_callback_query(
            query_id="q1",
            callback_data=encode_callback_data(1, CashoutCallbackAction.FULL),
            telegram_chat_id=-1009999999999,
            telegram_user_id=9001,
            telegram_username="operator",
            message_id=555,
        )

    assert result.status == "not_found"
    assert (await cashout()).status == CashoutStatus.SENT


@pytest.mark.asyncio
async def test_callback_amount_manipulation_is_ignored() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()

    async with TestSessionFactory() as session:
        result = await CashoutTelegramService(session, gateway=gateway).handle_callback_query(
            query_id="q1",
            callback_data="c:1:full:0.01",
            telegram_chat_id=-1001234567890,
            telegram_user_id=9001,
            telegram_username="operator",
            message_id=555,
        )

    assert result.status == "invalid_callback"
    assert (await cashout()).status == CashoutStatus.SENT


@pytest.mark.asyncio
async def test_old_reaction_path_no_longer_completes_cashout() -> None:
    await seed_cashout()

    result = await cashout_reactions.complete_cashout_from_reaction(
        555,
        -1001234567890,
        -1001234567890,
    )

    assert result.completed is False
    assert result.reason == "reaction_completion_disabled"
    assert (await cashout()).status == CashoutStatus.SENT


@pytest.mark.asyncio
async def test_operational_reconciliation_expires_pending_without_financial_transition() -> None:
    await seed_cashout()
    async with TestSessionFactory() as session, session.begin():
        session.add(
            CashoutPartialPendingInput(
                cashout_id=1,
                coadmin_id=10,
                telegram_user_id=9001,
                telegram_chat_id=-1001234567890,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )

    first = await cashout_operational_reconciliation.reconcile_cashout_operational_state()
    second = await cashout_operational_reconciliation.reconcile_cashout_operational_state()

    assert first.expired_pending_deleted == 1
    assert second.expired_pending_deleted == 0
    assert (await cashout()).status == CashoutStatus.SENT


@pytest.mark.asyncio
async def test_operational_reconciliation_requeues_failed_delivery_idempotently() -> None:
    await seed_cashout(
        status=CashoutStatus.FAILED_TO_SEND,
        telegram_status=CashoutTelegramStatus.FAILED_TO_SEND,
        telegram_message_id=None,
    )

    first = await cashout_operational_reconciliation.reconcile_cashout_operational_state()
    second = await cashout_operational_reconciliation.reconcile_cashout_operational_state()

    stored = await cashout()
    assert first.retryable_delivery_requeued == 1
    assert second.retryable_delivery_requeued == 0
    assert stored.status == CashoutStatus.FAILED_TO_SEND
    assert stored.telegram_status == CashoutTelegramStatus.PENDING

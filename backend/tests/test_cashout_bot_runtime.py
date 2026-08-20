from __future__ import annotations

import asyncio
import json
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


class TwoCallbacksThenCancelGateway(FakeBotGateway):
    def __init__(self, *, edit_ok: bool = True) -> None:
        super().__init__(edit_ok=edit_ok)
        self.calls = 0

    async def get_updates(self, *, offset: int | None) -> list[TelegramBotUpdate]:
        self.calls += 1
        if self.calls == 1:
            assert offset is None
            return [
                TelegramBotUpdate(
                    update_id=100,
                    payload={
                        "callback_query": {
                            "id": "q-first",
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
        if self.calls == 2:
            assert offset == 101
            return [
                TelegramBotUpdate(
                    update_id=101,
                    payload={
                        "callback_query": {
                            "id": "q-second",
                            "from": {"id": 9002, "username": "operator_two"},
                            "message": {
                                "message_id": 556,
                                "chat": {"id": -1001234567890, "type": "supergroup"},
                            },
                            "data": encode_callback_data(2, CashoutCallbackAction.FULL),
                        }
                    },
                )
            ]
        raise asyncio.CancelledError


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


async def seed_completed_cashout(
    cashout_id: int = 1,
    *,
    completion_type: CashoutCompletionType = CashoutCompletionType.FULL,
    actual_paid_amount: Decimal = Decimal("250.00"),
    telegram_last_error: str | None = None,
) -> None:
    await seed_cashout(cashout_id)
    async with TestSessionFactory() as session, session.begin():
        stored = await session.get(CashoutRequest, cashout_id)
        assert stored is not None
        stored.status = CashoutStatus.COMPLETED
        stored.completion_type = completion_type
        stored.actual_paid_amount = actual_paid_amount
        stored.completed_at = datetime(2026, 7, 6, 20, 45, tzinfo=UTC)
        stored.telegram_last_error = telegram_last_error


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
    assert "✅✅ CASHOUT COMPLETED ✅✅" in gateway.edits[-1]["text"]
    assert "🟢 PAID IN FULL" in gateway.edits[-1]["text"]
    assert "Requested Amount: $250.00" in gateway.edits[-1]["text"]
    assert "Paid Amount: $250.00" in gateway.edits[-1]["text"]
    assert "✅ NO BALANCE REMAINING" in gateway.edits[-1]["text"]
    assert "Requested By: sarah" in gateway.edits[-1]["text"]
    assert "Completed By: @operator" in gateway.edits[-1]["text"]
    assert "Optional Notes:\nVIP Player" in gateway.edits[-1]["text"]
    assert gateway.edits[-1]["text"].count("Optional Notes:") == 1
    assert gateway.answers[-1]["text"] == "Cashout completed (Full Payment)."


@pytest.mark.asyncio
async def test_full_payment_edit_failure_does_not_undo_completion() -> None:
    await seed_cashout()
    gateway = FakeBotGateway(edit_ok=False)

    result = await callback(CashoutCallbackAction.FULL, gateway)

    stored = await cashout()
    assert result.status == "completed_full"
    assert stored.status == CashoutStatus.COMPLETED
    assert stored.completion_type == CashoutCompletionType.FULL
    assert stored.actual_paid_amount == Decimal("250.00")
    assert stored.telegram_status == CashoutTelegramStatus.SENT
    assert stored.telegram_last_error == "terminal_sync_failed: edit failed"
    assert gateway.answers[-1]["text"] == "Cashout completed (Full Payment)."


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
async def test_update_loop_survives_terminal_edit_failure_and_continues() -> None:
    await seed_cashout(1, telegram_message_id=555)
    await seed_cashout(2, telegram_message_id=556)
    gateway = TwoCallbacksThenCancelGateway(edit_ok=False)

    with pytest.raises(asyncio.CancelledError):
        await run_cashout_bot_update_loop(
            gateway,
            session_factory=TestSessionFactory,
            report=lambda _: None,
        )

    async with TestSessionFactory() as session:
        first = await session.get(CashoutRequest, 1)
        second = await session.get(CashoutRequest, 2)
    assert first is not None
    assert second is not None
    assert first.status == CashoutStatus.COMPLETED
    assert second.status == CashoutStatus.COMPLETED
    assert first.telegram_last_error == "terminal_sync_failed: edit failed"
    assert second.telegram_last_error == "terminal_sync_failed: edit failed"
    assert [answer["query_id"] for answer in gateway.answers] == ["q-first", "q-second"]


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
async def test_gateway_edit_removes_buttons_with_empty_inline_keyboard_payload() -> None:
    observed_payload: dict[str, Any] | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_payload
        assert request.url.path.endswith("/editMessageText")
        observed_payload = json.loads(request.content.decode())
        return httpx.Response(200, json={"ok": True, "result": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = TelegramBotApiGateway(token="123:test-token", client=client)
        await gateway.edit_cashout_task_message(
            chat_id=-1001234567890,
            message_id=555,
            text="CASHOUT COMPLETED",
            buttons=None,
        )

    assert observed_payload is not None
    assert observed_payload["reply_markup"] == {"inline_keyboard": []}


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
    class ConfigFailureGateway(FakeBotGateway):
        async def get_updates(self, *, offset: int | None) -> list[object]:
            del offset
            raise TelegramBotApiError(
                "Unauthorized",
                failure_class=TelegramBotFailureClass.CONFIGURATION,
                status_code=401,
            )

    with pytest.raises(TelegramBotApiError):
        await run_cashout_bot_update_loop(
            ConfigFailureGateway(),
            session_factory=TestSessionFactory,
            report=lambda _: None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 409])
async def test_gateway_bot_configuration_errors_are_fatal(status_code: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"ok": False, "description": "configuration failed"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = TelegramBotApiGateway(token="123:test-token", client=client)
        with pytest.raises(TelegramBotApiError) as error:
            await gateway.get_updates(offset=None)

    assert error.value.failure_class == TelegramBotFailureClass.CONFIGURATION
    assert error.value.status_code == status_code


@pytest.mark.asyncio
async def test_update_loop_retryable_transport_error_backs_off_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    class RetryThenCancelGateway(FakeBotGateway):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def get_updates(self, *, offset: int | None) -> list[object]:
            del offset
            self.calls += 1
            if self.calls == 1:
                raise TelegramBotApiError(
                    "Telegram Bot API transport error",
                    failure_class=TelegramBotFailureClass.RETRYABLE,
                )
            raise asyncio.CancelledError

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    gateway = RetryThenCancelGateway()
    monkeypatch.setattr("app.telegram.cashout_bot.updates.asyncio.sleep", record_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_cashout_bot_update_loop(
            gateway,
            session_factory=TestSessionFactory,
            report=lambda _: None,
        )

    assert gateway.calls == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_update_loop_exits_cleanly_only_when_cancelled() -> None:
    class CancelledGateway(FakeBotGateway):
        async def get_updates(self, *, offset: int | None) -> list[object]:
            del offset
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_cashout_bot_update_loop(
            CancelledGateway(),
            session_factory=TestSessionFactory,
            report=lambda _: None,
        )


@pytest.mark.asyncio
async def test_gateway_default_long_poll_timeout_stays_below_http_timeout() -> None:
    observed_timeout: int | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_timeout
        payload = json.loads(request.content.decode())
        observed_timeout = payload["timeout"]
        return httpx.Response(200, json={"ok": True, "result": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = TelegramBotApiGateway(token="123:test-token", client=client)
        await gateway.get_updates(offset=None)

    assert observed_timeout == 10


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
    assert gateway.edits[-1]["buttons"] is None
    assert "⚠️ CASHOUT PARTIALLY PAID ⚠️" in gateway.edits[-1]["text"]
    assert "🟡 PARTIAL PAYMENT" in gateway.edits[-1]["text"]
    assert "Requested Amount: $250.00" in gateway.edits[-1]["text"]
    assert "Paid Amount: $100.00" in gateway.edits[-1]["text"]
    assert "Remaining Amount: $150.00" in gateway.edits[-1]["text"]
    assert "⚠️ $150.00 STILL UNPAID" in gateway.edits[-1]["text"]
    assert "Requested By: sarah" in gateway.edits[-1]["text"]
    assert "Completed By: @operator" in gateway.edits[-1]["text"]
    assert "Optional Notes:\nVIP Player" in gateway.edits[-1]["text"]
    assert gateway.edits[-1]["text"].count("VIP Player") == 1


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
    await seed_completed_cashout()
    gateway = FakeBotGateway()

    result = await callback(CashoutCallbackAction.FULL, gateway)

    assert result.status == "already_completed"
    assert "already completed" in gateway.answers[-1]["text"]
    assert gateway.edits[-1]["buttons"] is None
    assert "🟢 PAID IN FULL" in gateway.edits[-1]["text"]
    async with TestSessionFactory() as session:
        actions = list(await session.scalars(select(CashoutRequestAudit.action)))
    assert actions.count(CashoutAuditAction.TELEGRAM_BOT_COMPLETED) == 0


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
async def test_stale_button_on_completed_cashout_repairs_message_without_new_completion() -> None:
    await seed_completed_cashout(telegram_last_error="terminal_sync_failed: previous")
    gateway = FakeBotGateway()

    result = await callback(CashoutCallbackAction.FULL, gateway)

    stored = await cashout()
    assert result.status == "already_completed"
    assert stored.status == CashoutStatus.COMPLETED
    assert stored.telegram_last_error is None
    assert gateway.edits[-1]["buttons"] is None
    assert "🟢 PAID IN FULL" in gateway.edits[-1]["text"]
    async with TestSessionFactory() as session:
        actions = list(await session.scalars(select(CashoutRequestAudit.action)))
    assert actions.count(CashoutAuditAction.TELEGRAM_BOT_COMPLETED) == 0


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
    assert "Requested By: sarah" in gateway.edits[-1]["text"]
    assert "Optional Notes:\nVIP Player" in gateway.edits[-1]["text"]


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
    await seed_cashout(coadmin_id=11, telegram_chat_id=-1009999999999)
    gateway = FakeBotGateway()

    result = await callback(CashoutCallbackAction.FULL, gateway)

    assert result.status in {"rejected", "not_found"}
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


@pytest.mark.asyncio
async def test_operational_reconciliation_repairs_completed_terminal_message() -> None:
    await seed_completed_cashout(telegram_last_error="terminal_sync_failed: object expected")
    await seed_completed_cashout(2, telegram_last_error="terminal_sync_failed: other")
    gateway = FakeBotGateway()

    result = await cashout_operational_reconciliation.reconcile_cashout_operational_state(
        cashout_id=1,
        gateway=gateway,
    )

    stored = await cashout()
    async with TestSessionFactory() as session:
        other = await session.get(CashoutRequest, 2)
    assert result.terminal_cleanup_attempted == 1
    assert result.terminal_cleanup_failed == 0
    assert stored.status == CashoutStatus.COMPLETED
    assert stored.telegram_status == CashoutTelegramStatus.SENT
    assert stored.telegram_last_error is None
    assert other is not None
    assert other.telegram_last_error == "terminal_sync_failed: other"
    assert gateway.edits[-1]["buttons"] is None
    assert gateway.edits[-1]["text"] == (
        "✅✅ CASHOUT COMPLETED ✅✅\n"
        "🟢 PAID IN FULL\n"
        "\n"
        "Request ID: CR-000001\n"
        "Tag: ABC12345\n"
        "\n"
        "Requested Amount: $250.00\n"
        "Paid Amount: $250.00\n"
        "\n"
        "✅ NO BALANCE REMAINING\n"
        "\n"
        "Requested By: sarah\n"
        "Completed By: Telegram bot\n"
        "Completed At: 2026-07-06 20:45 UTC\n"
        "\n"
        "Optional Notes:\n"
        "VIP Player"
    )


@pytest.mark.asyncio
async def test_initial_cashout_card_includes_creator_identity() -> None:
    await seed_cashout(
        status=CashoutStatus.PENDING,
        telegram_status=CashoutTelegramStatus.PENDING,
        telegram_message_id=None,
    )
    gateway = FakeBotGateway()

    await cashout_delivery.deliver_next_cashout(
        object(),
        "group",
        telegram_chat_id=-1001234567890,
        bot_gateway=gateway,
    )

    text = gateway.sent_cashouts[0]["text"]
    assert "Requested By:\nsarah" in text
    assert "Optional Notes:\nVIP Player" in text
    assert "Unknown" not in text


@pytest.mark.asyncio
async def test_claim_edit_preserves_note() -> None:
    await seed_cashout()
    async with TestSessionFactory() as session, session.begin():
        stored = await session.get(CashoutRequest, 1)
        assert stored is not None
        stored.notes = "Waiting for player"
    gateway = FakeBotGateway()

    async with TestSessionFactory() as session:
        status = await CashoutTelegramService(session, gateway=gateway).sync_persisted_task(
            await session.get(CashoutRequest, 1)
        )

    assert status == "edited_active"
    text = gateway.edits[-1]["text"]
    assert "Optional Notes:\nWaiting for player" in text
    assert "Requested By:\nsarah" in text
    assert gateway.edits[-1]["buttons"] == build_active_task_markup(1)


@pytest.mark.asyncio
async def test_done_edit_preserves_note_and_creator() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()
    await callback(CashoutCallbackAction.FULL, gateway)

    text = gateway.edits[-1]["text"]
    assert "Optional Notes:\nVIP Player" in text
    assert "Requested By: sarah" in text
    assert "Completed By: @operator" in text


@pytest.mark.asyncio
async def test_multiple_staff_actors_do_not_erase_creator_identity() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()
    await callback(CashoutCallbackAction.PARTIAL, gateway)
    await submit_partial_amount(gateway)

    async with TestSessionFactory() as session:
        refreshed = await session.get(CashoutRequest, 1)
        assert refreshed is not None
        status = await CashoutTelegramService(session, gateway=gateway).sync_persisted_task(
            refreshed
        )

    stored = await cashout()
    assert stored.created_by_staff_id == 42
    assert status == "edited_terminal"
    text = gateway.edits[-1]["text"]
    assert "Requested By: sarah" in text
    assert "Completed By: @operator" in text
    assert "Optional Notes:\nVIP Player" in text
    assert text.count("Optional Notes:") == 1


@pytest.mark.asyncio
async def test_telegram_edit_retry_reconstructs_note_from_db() -> None:
    await seed_completed_cashout(telegram_last_error="terminal_sync_failed: edit failed")
    gateway = FakeBotGateway()

    async with TestSessionFactory() as session:
        stored = await session.get(CashoutRequest, 1)
        status = await CashoutTelegramService(session, gateway=gateway).sync_persisted_task(
            stored
        )

    assert status == "edited_terminal"
    assert "Optional Notes:\nVIP Player" in gateway.edits[-1]["text"]
    assert (await cashout()).telegram_last_error is None


@pytest.mark.asyncio
async def test_missing_display_name_falls_back_to_telegram_user_id() -> None:
    await seed_cashout()
    gateway = FakeBotGateway()

    async with TestSessionFactory() as session:
        result = await CashoutTelegramService(session, gateway=gateway).handle_callback_query(
            query_id="q1",
            callback_data=encode_callback_data(1, CashoutCallbackAction.FULL),
            telegram_chat_id=-1001234567890,
            telegram_user_id=9001,
            telegram_username=None,
            message_id=555,
        )

    assert result.status == "completed_full"
    assert "Completed By: Telegram user 9001" in gateway.edits[-1]["text"]
    assert "Unknown" not in gateway.edits[-1]["text"]
    assert "Optional Notes:\nVIP Player" in gateway.edits[-1]["text"]

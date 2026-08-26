from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.telegram import run_listener
from app.telegram.cashout_bot.api import TelegramBotApiError, TelegramBotFailureClass
from app.telegram.cashout_bot.updates import (
    TelegramBotPollingUnrecoverableError,
    run_cashout_bot_update_loop,
)
from tests.test_config import _set_enabled_telegram_env


class _EmptyWebhookGateway:
    webhook_cleared = False

    async def get_updates(self, *, offset: int | None) -> list[object]:
        del offset
        raise asyncio.CancelledError

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        del drop_pending_updates
        self.webhook_cleared = True


class _RecordingGateway:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb
        self.events.append("gateway_closed")


async def _never_disconnect() -> None:
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_await_critical_tasks_returns_when_session_disconnects() -> None:
    session = asyncio.create_task(asyncio.sleep(0), name="telegram-client-disconnect")
    worker = asyncio.create_task(_never_disconnect(), name="cashout-bot-updates")
    try:
        await run_listener._await_critical_listener_tasks(
            session_task=session,
            critical_tasks=(worker,),
        )
        assert worker.done() is False
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_unexpected_poller_exception_fails_parent_listener() -> None:
    async def boom() -> None:
        raise RuntimeError("poller crashed")

    session = asyncio.create_task(_never_disconnect(), name="telegram-client-disconnect")
    poller = asyncio.create_task(boom(), name="cashout-bot-updates")
    try:
        with pytest.raises(run_listener.TelegramListenerCriticalWorkerError) as error:
            await run_listener._await_critical_listener_tasks(
                session_task=session,
                critical_tasks=(poller,),
            )
        assert error.value.task_name == "cashout-bot-updates"
        assert isinstance(error.value.__cause__, RuntimeError)
    finally:
        session.cancel()
        await asyncio.gather(session, poller, return_exceptions=True)


@pytest.mark.asyncio
async def test_unexpected_worker_return_fails_parent_listener() -> None:
    async def exit_early() -> None:
        return None

    session = asyncio.create_task(_never_disconnect(), name="telegram-client-disconnect")
    worker = asyncio.create_task(exit_early(), name="cashout-delivery")
    try:
        with pytest.raises(run_listener.TelegramListenerCriticalWorkerError) as error:
            await run_listener._await_critical_listener_tasks(
                session_task=session,
                critical_tasks=(worker,),
            )
        assert error.value.task_name == "cashout-delivery"
        assert "exited unexpectedly" in str(error.value)
    finally:
        session.cancel()
        await asyncio.gather(session, worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_per_iteration_send_failure_does_not_fail_parent() -> None:
    async def delivery_worker() -> None:
        while True:
            try:
                raise TelegramBotApiError(
                    "Telegram Bot API transport error",
                    failure_class=TelegramBotFailureClass.RETRYABLE,
                )
            except TelegramBotApiError:
                await asyncio.sleep(0.01)

    session = asyncio.create_task(asyncio.sleep(0.05), name="telegram-client-disconnect")
    worker = asyncio.create_task(delivery_worker(), name="cashout-delivery")
    try:
        await run_listener._await_critical_listener_tasks(
            session_task=session,
            critical_tasks=(worker,),
        )
        assert worker.done() is False
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_worker_propagates_cancellation() -> None:
    session = asyncio.create_task(_never_disconnect(), name="telegram-client-disconnect")
    worker = asyncio.create_task(_never_disconnect(), name="cashout-bot-updates")
    worker.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await run_listener._await_critical_listener_tasks(
                session_task=session,
                critical_tasks=(worker,),
            )
    finally:
        session.cancel()
        await asyncio.gather(session, worker, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_cancels_poller_before_closing_gateway() -> None:
    gateway = _RecordingGateway()
    events = gateway.events
    poller_started = asyncio.Event()
    other_started = asyncio.Event()

    async def poller() -> None:
        events.append("poller_started")
        poller_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append("poller_cancelled")
            raise

    async def other_worker() -> None:
        other_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append("other_cancelled")
            raise

    bot_update_task = asyncio.create_task(poller(), name="cashout-bot-updates")
    other_task = asyncio.create_task(other_worker(), name="cashout-delivery")
    await poller_started.wait()
    await other_started.wait()
    await run_listener._shutdown_listener_session_tasks(
        bot_update_task=bot_update_task,
        other_tasks=(other_task,),
        bot_gateway=gateway,  # type: ignore[arg-type]
    )
    assert events[0] == "poller_started"
    assert events.index("poller_cancelled") < events.index("gateway_closed")
    assert events.index("poller_cancelled") < events.index("other_cancelled")
    assert events.count("poller_started") == 1


@pytest.mark.asyncio
async def test_persistent_409_fails_parent_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.telegram.cashout_bot.updates.CONFLICT_MAX_CONSECUTIVE", 3)
    monkeypatch.setattr("app.telegram.cashout_bot.updates.CONFLICT_BUDGET_SECONDS", 999.0)

    class AlwaysConflictGateway:
        async def get_updates(self, *, offset: int | None) -> list[object]:
            del offset
            raise TelegramBotApiError(
                "Conflict: terminated by other getUpdates request",
                failure_class=TelegramBotFailureClass.CONFLICT,
                status_code=409,
            )

        async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
            del drop_pending_updates

    async def fast_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr("app.telegram.cashout_bot.updates.asyncio.sleep", fast_sleep)
    poller = asyncio.create_task(
        run_cashout_bot_update_loop(
            AlwaysConflictGateway(),  # type: ignore[arg-type]
            report=lambda _: None,
        ),
        name="cashout-bot-updates",
    )
    session = asyncio.create_task(_never_disconnect(), name="telegram-client-disconnect")
    try:
        with pytest.raises(run_listener.TelegramListenerCriticalWorkerError) as error:
            await run_listener._await_critical_listener_tasks(
                session_task=session,
                critical_tasks=(poller,),
            )
        assert error.value.task_name == "cashout-bot-updates"
        assert isinstance(error.value.__cause__, TelegramBotPollingUnrecoverableError)
    finally:
        session.cancel()
        poller.cancel()
        await asyncio.gather(session, poller, return_exceptions=True)


@pytest.mark.asyncio
async def test_run_listener_exits_on_critical_worker_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-1009876543210")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test-token")
    run_listener.get_settings.cache_clear()
    calls = 0

    async def fail_session(*_args: Any, **_kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        raise run_listener.TelegramListenerCriticalWorkerError("cashout-bot-updates")

    monkeypatch.setattr(run_listener, "_run_listener_session", fail_session)
    monkeypatch.setattr(run_listener, "configure_logging", lambda *_args, **_kwargs: None)
    try:
        with pytest.raises(run_listener.TelegramListenerCriticalWorkerError):
            await run_listener.run_listener(report=lambda _: None)
        assert calls == 1
    finally:
        run_listener.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_cancelled_update_loop_does_not_spawn_second_poller() -> None:
    class CountingGateway(_EmptyWebhookGateway):
        def __init__(self) -> None:
            self.calls = 0

        async def get_updates(self, *, offset: int | None) -> list[object]:
            del offset
            self.calls += 1
            raise asyncio.CancelledError

    gateway = CountingGateway()
    with pytest.raises(asyncio.CancelledError):
        await run_cashout_bot_update_loop(
            gateway,  # type: ignore[arg-type]
            report=lambda _: None,
        )
    assert gateway.calls == 1
    assert gateway.webhook_cleared is True

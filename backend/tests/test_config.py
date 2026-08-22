from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.telegram import run_listener


def _set_enabled_telegram_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "test-api-hash")
    monkeypatch.setenv("TELEGRAM_SESSION_NAME", "telegram-ledger")
    monkeypatch.setenv("TELEGRAM_GROUP_ID", "-1001234567890")
    monkeypatch.delenv("TELEGRAM_GROUP_USERNAME", raising=False)


@pytest.mark.asyncio
async def test_enabled_listener_requires_cashout_group_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test-token")
    run_listener.get_settings.cache_clear()

    with pytest.raises(
        RuntimeError,
        match="TELEGRAM_CASHOUT_GROUP_ID is required for cashout Telegram delivery",
    ):
        await run_listener.run_listener(report=lambda _: None)
    run_listener.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_enabled_listener_requires_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-1009876543210")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    run_listener.get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN is required"):
        await run_listener.run_listener(report=lambda _: None)
    run_listener.get_settings.cache_clear()


def test_cashout_group_id_accepts_supergroup_integer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-1009876543210")

    settings = Settings()

    assert settings.telegram_group_id == -1001234567890
    assert settings.telegram_cashout_group_id == -1009876543210
    assert settings.shared_telegram_supergroup_id == -1009876543210
    assert settings.telegram_venmo_group_id is None
    assert settings.resolved_venmo_telegram_group_id == -1009876543210
    assert settings.venmo_group_falls_back_to_cashout is True


def test_venmo_group_id_is_independent_of_cashout_and_payment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-1009876543210")
    monkeypatch.setenv("TELEGRAM_VENMO_GROUP_ID", "-5198735527")

    settings = Settings()

    assert settings.telegram_group_id == -1001234567890
    assert settings.telegram_cashout_group_id == -1009876543210
    assert settings.telegram_venmo_group_id == -5198735527
    assert settings.resolved_venmo_telegram_group_id == -5198735527
    assert settings.venmo_group_falls_back_to_cashout is False
    assert settings.telegram_group_target == -1001234567890


def test_blank_venmo_group_id_falls_back_to_cashout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_enabled_telegram_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "-1009876543210")
    monkeypatch.setenv("TELEGRAM_VENMO_GROUP_ID", "")

    settings = Settings()

    assert settings.telegram_venmo_group_id is None
    assert settings.resolved_venmo_telegram_group_id == -1009876543210
    assert settings.venmo_group_falls_back_to_cashout is True


@pytest.mark.asyncio
async def test_supervised_background_task_restarts_after_failure() -> None:
    attempts = 0

    async def factory() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionRefusedError("Connect call failed ('127.0.0.1', 5432)")
        raise asyncio.CancelledError

    task = asyncio.create_task(
        run_listener._run_supervised_background_task("cashout-delivery", factory)
    )
    try:
        for _ in range(80):
            if attempts >= 2:
                break
            await asyncio.sleep(0.02)
        assert attempts >= 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

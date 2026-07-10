from __future__ import annotations

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
    monkeypatch.delenv("TELEGRAM_CASHOUT_GROUP_ID", raising=False)
    run_listener.get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="TELEGRAM_CASHOUT_GROUP_ID is required"):
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

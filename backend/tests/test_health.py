import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.main import app


@pytest.mark.asyncio
async def test_health_check() -> None:
    """The public health probe returns the documented contract."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_check_does_not_expose_secrets() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    rendered = str(body)
    assert body["status"] in {"ok", "degraded", "not_configured"}
    assert "checks" in body
    assert "database" in body["checks"]
    assert "telegram_workflow_config" in body["checks"]
    assert body["checks"]["telegram_workflow_config"]["status"] in {
        "ok",
        "not_configured",
    }
    assert "test-only-password" not in rendered
    assert "AUTH_SECRET_KEY" not in rendered
    assert "TELEGRAM_BOT_TOKEN" not in rendered


@pytest.mark.asyncio
async def test_readiness_reports_partial_telegram_workflow_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test-token")
    monkeypatch.setenv("TELEGRAM_CASHOUT_GROUP_ID", "")
    get_settings.cache_clear()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/ready")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["telegram_workflow_config"] == {
        "status": "not_configured",
        "detail": "cashout_group_id",
    }
    assert "123:test-token" not in str(body)

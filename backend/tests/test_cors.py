from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import app


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (
            "https://ledger.example.com, https://admin.example.com",
            [
                "https://ledger.example.com",
                "https://admin.example.com",
            ],
        ),
        (
            '["https://ledger.example.com", "https://admin.example.com"]',
            [
                "https://ledger.example.com",
                "https://admin.example.com",
            ],
        ),
    ],
)
def test_cors_origin_formats(
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
    expected: list[str],
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_CORS_ORIGINS", configured)

    settings = Settings()

    assert settings.cors_origin_strings == expected


def test_development_includes_both_loopback_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("APP_CORS_ORIGINS", '["http://localhost:3000"]')

    settings = Settings()

    assert settings.cors_origin_strings == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
)
async def test_auth_login_options_preflight(origin: str) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/api/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Validated application configuration loaded exclusively from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = Field(validation_alias="APP_NAME")
    environment: Literal["development", "test", "staging", "production"] = Field(
        validation_alias="APP_ENV"
    )
    debug: bool = Field(validation_alias="APP_DEBUG")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        validation_alias="APP_LOG_LEVEL"
    )
    api_prefix: str = Field(validation_alias="APP_API_PREFIX")
    cors_origins: Annotated[list[AnyHttpUrl], NoDecode] = Field(
        validation_alias="APP_CORS_ORIGINS"
    )

    database_host: str = Field(validation_alias="DATABASE_HOST")
    database_port: int = Field(validation_alias="DATABASE_PORT")
    database_name: str = Field(validation_alias="DATABASE_NAME")
    database_user: str = Field(validation_alias="DATABASE_USER")
    database_password: SecretStr = Field(validation_alias="DATABASE_PASSWORD")
    database_echo: bool = Field(validation_alias="DATABASE_ECHO")
    database_pool_size: int = Field(gt=0, validation_alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(ge=0, validation_alias="DATABASE_MAX_OVERFLOW")

    auth_secret_key: SecretStr = Field(
        min_length=32,
        validation_alias="AUTH_SECRET_KEY",
    )
    auth_cookie_name: str = Field(
        min_length=1,
        validation_alias="AUTH_COOKIE_NAME",
    )
    auth_cookie_secure: bool = Field(validation_alias="AUTH_COOKIE_SECURE")
    auth_session_minutes: int = Field(
        gt=0,
        le=10_080,
        validation_alias="AUTH_SESSION_MINUTES",
    )

    telegram_api_id: int | None = Field(
        default=None,
        gt=0,
        validation_alias="TELEGRAM_API_ID",
    )
    telegram_api_hash: SecretStr | None = Field(
        default=None,
        validation_alias="TELEGRAM_API_HASH",
    )
    telegram_session_name: str | None = Field(
        default=None,
        min_length=1,
        validation_alias="TELEGRAM_SESSION_NAME",
    )
    telegram_group_id: int | None = Field(
        default=None,
        validation_alias="TELEGRAM_GROUP_ID",
    )
    telegram_cashout_group_id: int | None = Field(
        default=None,
        validation_alias="TELEGRAM_CASHOUT_GROUP_ID",
    )
    telegram_group_username: str | None = Field(
        default=None,
        min_length=2,
        validation_alias="TELEGRAM_GROUP_USERNAME",
    )
    telegram_enabled: bool = Field(
        default=False,
        validation_alias="TELEGRAM_ENABLED",
    )
    telegram_backfill_limit: int = Field(
        default=500,
        gt=0,
        le=10_000,
        validation_alias="TELEGRAM_BACKFILL_LIMIT",
    )
    cashout_completion_reactions: str = Field(
        default="✅,👍",
        validation_alias="CASHOUT_COMPLETION_REACTIONS",
    )
    cashout_reconciliation_interval_seconds: int = Field(
        default=20,
        gt=0,
        le=300,
        validation_alias="CASHOUT_RECONCILIATION_INTERVAL_SECONDS",
    )
    cashout_reconciliation_batch_size: int = Field(
        default=40,
        gt=0,
        le=200,
        validation_alias="CASHOUT_RECONCILIATION_BATCH_SIZE",
    )
    inquiry_media_dir: str = Field(
        default="var/inquiry_media",
        validation_alias="INQUIRY_MEDIA_DIR",
    )
    inquiry_media_max_bytes: int = Field(
        default=10_485_760,
        gt=0,
        le=52_428_800,
        validation_alias="INQUIRY_MEDIA_MAX_BYTES",
    )
    inquiry_page_size_default: int = Field(
        default=40,
        gt=0,
        le=100,
        validation_alias="INQUIRY_PAGE_SIZE_DEFAULT",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        """Accept JSON arrays or comma-separated CORS origin strings."""
        if isinstance(value, list):
            return value
        if not isinstance(value, str):
            raise ValueError("APP_CORS_ORIGINS must be a string or list")

        raw_value = value.strip()
        if not raw_value:
            raise ValueError("APP_CORS_ORIGINS must contain at least one origin")

        if raw_value.startswith("["):
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError as error:
                raise ValueError("APP_CORS_ORIGINS contains invalid JSON") from error
            if not isinstance(parsed, list):
                raise ValueError("APP_CORS_ORIGINS JSON must be an array")
            return parsed

        return [origin.strip() for origin in raw_value.split(",") if origin.strip()]

    @model_validator(mode="after")
    def add_local_development_origins(self) -> Self:
        """Always trust both standard loopback frontend origins in development."""
        if self.environment != "development":
            return self

        configured = {
            str(origin).rstrip("/")
            for origin in self.cors_origins
        }
        for local_origin in (
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ):
            if local_origin not in configured:
                self.cors_origins.append(AnyHttpUrl(local_origin))
        return self

    @model_validator(mode="after")
    def validate_telegram_configuration(self) -> Self:
        """Require complete Telegram credentials only when the listener is enabled."""
        if not self.telegram_enabled:
            return self

        required_values = {
            "TELEGRAM_API_ID": self.telegram_api_id,
            "TELEGRAM_API_HASH": self.telegram_api_hash,
            "TELEGRAM_SESSION_NAME": self.telegram_session_name,
        }
        missing = [name for name, value in required_values.items() if value is None]
        if missing:
            raise ValueError(
                f"Telegram listener configuration is incomplete: {', '.join(missing)}"
            )

        group_selectors = (
            self.telegram_group_id is not None,
            self.telegram_group_username is not None,
        )
        if sum(group_selectors) != 1:
            raise ValueError(
                "Set exactly one of TELEGRAM_GROUP_ID or TELEGRAM_GROUP_USERNAME"
            )
        return self

    @property
    def telegram_group_target(self) -> str | int | None:
        """Return the configured chat ID or username accepted by Telethon."""
        if self.telegram_group_id is not None:
            return self.telegram_group_id
        return self.telegram_group_username

    @property
    def cashout_completion_reaction_allowlist(self) -> frozenset[str] | None:
        """Parsed completion-reaction allowlist (None = any active reaction)."""
        from app.telegram.reaction_matching import parse_completion_reactions

        return parse_completion_reactions(self.cashout_completion_reactions)

    @property
    def cors_origin_strings(self) -> list[str]:
        """Return browser Origin-compatible values without URL trailing slashes."""
        return [str(origin).rstrip("/") for origin in self.cors_origins]

    @property
    def database_url(self) -> str:
        """Build an async PostgreSQL URL while safely escaping credentials."""
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.database_user,
            password=self.database_password.get_secret_value(),
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings instance per process."""
    return Settings()

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_REDACTED_BOT_TOKEN = "<redacted-bot-token>"
_installed_redaction_filters: set[str] = set()


class TelegramBotFailureClass(StrEnum):
    """Operational classification for Telegram Bot API failures."""

    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    CONFIGURATION = "configuration"


@dataclass(frozen=True, slots=True)
class TelegramBotUpdate:
    update_id: int
    payload: dict[str, Any]


class TelegramBotApiError(Exception):
    """Classified, redacted Telegram Bot API error."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: TelegramBotFailureClass,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class TelegramBotLongPollTimeout(Exception):
    """Expected idle getUpdates long-poll timeout."""


class TelegramBotApiGateway:
    """Small Telegram Bot API adapter used by cashout bot runtime."""

    def __init__(
        self,
        *,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        configured = (
            settings.telegram_bot_token.get_secret_value()
            if settings.telegram_bot_token
            else None
        )
        self._token = token or configured
        if not self._token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required for cashout bot runtime")
        self._client = client
        self._owns_client = client is None
        self._timeout_seconds = settings.telegram_bot_api_timeout_seconds
        self._max_retry_after_seconds = settings.telegram_bot_max_retry_after_seconds
        self._long_poll_timeout_seconds = self._default_long_poll_timeout_seconds()
        _install_httpx_token_redaction(self._token)

    async def __aenter__(self) -> TelegramBotApiGateway:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()

    async def send_cashout_task_message(
        self,
        *,
        chat_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]],
    ) -> int | None:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": self._reply_markup(buttons),
        }
        data = await self._post("sendMessage", payload)
        message_id = data.get("message_id")
        return int(message_id) if isinstance(message_id, int) else None

    async def answer_callback_query(
        self,
        *,
        query_id: int | str,
        text: str,
        alert: bool = False,
    ) -> None:
        await self._post(
            "answerCallbackQuery",
            {
                "callback_query_id": str(query_id),
                "text": text,
                "show_alert": alert,
            },
        )

    async def edit_cashout_task_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        buttons: list[list[tuple[str, str]]] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        payload["reply_markup"] = (
            self._reply_markup(buttons) if buttons else self._empty_reply_markup()
        )
        await self._post("editMessageText", payload)

    async def delete_message(self, *, chat_id: int, message_id: int) -> bool:
        try:
            await self._post(
                "deleteMessage",
                {"chat_id": chat_id, "message_id": message_id},
            )
        except TelegramBotApiError as error:
            logger.warning(
                "telegram_bot_delete_failed",
                extra={
                    "telegram_chat_id": chat_id,
                    "telegram_message_id": message_id,
                    "failure_class": error.failure_class.value,
                    "telegram_status_code": error.status_code,
                },
            )
            return False
        return True

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> int | None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        data = await self._post("sendMessage", payload)
        message_id = data.get("message_id")
        return int(message_id) if isinstance(message_id, int) else None

    async def send_photo(
        self,
        *,
        chat_id: int,
        photo_path: Path,
        caption: str,
        buttons: list[list[tuple[str, str]]],
        mime_type: str,
        filename: str | None = None,
    ) -> int | None:
        payload = {
            "chat_id": str(chat_id),
            "caption": caption,
            "reply_markup": json.dumps(self._reply_markup(buttons)),
        }
        file_name = filename or photo_path.name
        data = await self._post_file(
            "sendPhoto",
            payload,
            files={
                "photo": (
                    file_name,
                    await asyncio.to_thread(photo_path.read_bytes),
                    mime_type,
                )
            },
        )
        message_id = data.get("message_id")
        return int(message_id) if isinstance(message_id, int) else None

    async def edit_message_caption(
        self,
        *,
        chat_id: int,
        message_id: int,
        caption: str,
        buttons: list[list[tuple[str, str]]] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": caption,
        }
        payload["reply_markup"] = (
            self._reply_markup(buttons) if buttons else self._empty_reply_markup()
        )
        await self._post("editMessageCaption", payload)

    async def get_updates(
        self,
        *,
        offset: int | None,
        timeout_seconds: int | None = None,
    ) -> list[TelegramBotUpdate]:
        poll_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self._long_poll_timeout_seconds
        )
        payload: dict[str, Any] = {
            "timeout": poll_timeout,
            "allowed_updates": ["callback_query", "message"],
        }
        if offset is not None:
            payload["offset"] = offset
        try:
            raw_updates = await self._post("getUpdates", payload)
        except TelegramBotLongPollTimeout:
            logger.debug(
                "cashout_bot_get_updates_long_poll_timeout",
                extra={"telegram_long_poll_timeout_seconds": poll_timeout},
            )
            return []
        if not isinstance(raw_updates, list):
            return []
        updates: list[TelegramBotUpdate] = []
        for raw in raw_updates:
            if not isinstance(raw, dict):
                continue
            update_id = raw.get("update_id")
            if isinstance(update_id, int):
                updates.append(TelegramBotUpdate(update_id=update_id, payload=raw))
        return updates

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> None:
        """Ensure long polling can receive callback queries."""
        await self._post(
            "deleteWebhook",
            {"drop_pending_updates": drop_pending_updates},
        )

    async def _post(self, method: str, payload: dict[str, Any]) -> Any:
        return await self._request(method, json=payload)

    async def _post_file(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        files: dict[str, tuple[str, bytes, str]],
    ) -> Any:
        return await self._request(method, data=payload, files=files)

    async def _request(
        self,
        method: str,
        *,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
            self._owns_client = True
        try:
            request_kwargs: dict[str, Any] = {}
            if json is not None:
                request_kwargs["json"] = json
            if data is not None:
                request_kwargs["data"] = data
            if files is not None:
                request_kwargs["files"] = files
            response = await self._client.post(
                f"https://api.telegram.org/bot{self._token}/{method}",
                **request_kwargs,
            )
        except httpx.ReadTimeout as error:
            if method == "getUpdates":
                raise TelegramBotLongPollTimeout from error
            raise TelegramBotApiError(
                "Telegram Bot API request timed out",
                failure_class=TelegramBotFailureClass.RETRYABLE,
            ) from error
        except httpx.TimeoutException as error:
            raise TelegramBotApiError(
                "Telegram Bot API request timed out",
                failure_class=TelegramBotFailureClass.RETRYABLE,
            ) from error
        except httpx.TransportError as error:
            raise TelegramBotApiError(
                "Telegram Bot API transport error",
                failure_class=TelegramBotFailureClass.RETRYABLE,
            ) from error

        body = self._response_json(response)
        if response.status_code >= 400:
            raise self._classified_error(response.status_code, body)
        if not body.get("ok"):
            description = body.get("description", "Telegram Bot API request failed")
            raise self._classified_error(response.status_code, body, str(description))
        return body.get("result")

    def _classified_error(
        self,
        status_code: int,
        body: dict[str, Any],
        description: str | None = None,
    ) -> TelegramBotApiError:
        message = description or str(body.get("description") or "Telegram Bot API request failed")
        retry_after = self._retry_after(body)
        if status_code == 429:
            return TelegramBotApiError(
                "Telegram Bot API rate limited",
                failure_class=TelegramBotFailureClass.RETRYABLE,
                status_code=status_code,
                retry_after_seconds=retry_after,
            )
        if status_code in (401, 403, 400, 409):
            failure_class = (
                TelegramBotFailureClass.CONFIGURATION
                if status_code in (401, 403, 409)
                else TelegramBotFailureClass.NON_RETRYABLE
            )
            parameters = body.get("parameters")
            migrate_to = (
                parameters.get("migrate_to_chat_id")
                if isinstance(parameters, dict)
                else None
            )
            if migrate_to is not None:
                message = f"{message} (migrate_to_chat_id={migrate_to})"
            return TelegramBotApiError(
                message,
                failure_class=failure_class,
                status_code=status_code,
            )
        if status_code >= 500:
            return TelegramBotApiError(
                message,
                failure_class=TelegramBotFailureClass.RETRYABLE,
                status_code=status_code,
            )
        return TelegramBotApiError(
            message,
            failure_class=TelegramBotFailureClass.NON_RETRYABLE,
            status_code=status_code,
        )

    def _retry_after(self, body: dict[str, Any]) -> int | None:
        parameters = body.get("parameters")
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if not isinstance(retry_after, int):
            return None
        return min(retry_after, self._max_retry_after_seconds)

    @staticmethod
    def _response_json(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
            raise TelegramBotApiError(
                "Telegram Bot API returned malformed JSON",
                failure_class=(
                    TelegramBotFailureClass.RETRYABLE
                    if response.status_code >= 500
                    else TelegramBotFailureClass.NON_RETRYABLE
                ),
                status_code=response.status_code,
            ) from error
        if not isinstance(body, dict):
            raise TelegramBotApiError(
                "Telegram Bot API returned malformed payload",
                failure_class=TelegramBotFailureClass.RETRYABLE,
                status_code=response.status_code,
            )
        return body

    @staticmethod
    def _reply_markup(buttons: list[list[tuple[str, str]]]) -> dict[str, object]:
        return {
            "inline_keyboard": [
                [
                    {"text": label, "callback_data": callback_data}
                    for label, callback_data in row
                ]
                for row in buttons
            ]
        }

    @staticmethod
    def _empty_reply_markup() -> dict[str, object]:
        """Valid InlineKeyboardMarkup payload that removes existing inline buttons."""
        return {"inline_keyboard": []}

    def _default_long_poll_timeout_seconds(self) -> int:
        """Keep Telegram's poll timeout below the HTTP read timeout."""
        if self._timeout_seconds <= 2:
            return 1
        if self._timeout_seconds <= 6:
            return max(1, int(self._timeout_seconds) - 1)
        return max(1, min(15, int(self._timeout_seconds) - 5))


class _TelegramBotTokenRedactionFilter(logging.Filter):
    def __init__(self, token: str) -> None:
        super().__init__()
        self._needle = f"/bot{token}/"
        self._redacted = f"/bot{_REDACTED_BOT_TOKEN}/"

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = record.msg.replace(self._needle, self._redacted)
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: self._redact(value) for key, value in record.args.items()
            }
        return True

    def _redact(self, value: object) -> object:
        if isinstance(value, str):
            return value.replace(self._needle, self._redacted)
        if isinstance(value, httpx.URL):
            return str(value).replace(self._needle, self._redacted)
        return value


def _install_httpx_token_redaction(token: str) -> None:
    if token in _installed_redaction_filters:
        return
    token_filter = _TelegramBotTokenRedactionFilter(token)
    logging.getLogger("httpx").addFilter(token_filter)
    logging.getLogger("httpcore").addFilter(token_filter)
    _installed_redaction_filters.add(token)

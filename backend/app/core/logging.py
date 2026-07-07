from __future__ import annotations

import json
import logging
import logging.config
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    """Emit machine-readable application logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if environment := getattr(record, "environment", None):
            payload["environment"] = environment
        for field_name in (
            "telegram_message_id",
            "telegram_chat_id",
            "telegram_group",
            "outcome",
            "duration_ms",
            "connection_acquisition_ms",
            "list_query_ms",
            "count_query_ms",
            "has_more_ms",
            "serialization_ms",
            "limit",
            "offset",
            "total",
            "include_total",
            "database_operation",
            "cashout_request_id",
            "cashout_attempt",
            "matched_cashout",
            "previous_status",
            "completed",
            "reason_ignored",
            "raw_update_type",
            "expected_telegram_chat_id",
            "reaction_summary",
            "sse_event",
        ):
            field_value = getattr(record, field_name, None)
            if field_value is not None:
                payload[field_name] = field_value
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    """Configure structured stdout logging for the process."""
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"handlers": ["console"], "level": level},
        }
    )


def get_logger(name: str) -> logging.Logger:
    """Return a named standard-library logger."""
    return logging.getLogger(name)

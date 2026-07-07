"""Telethon connection and event conversion adapters."""

from app.telegram.client import create_telegram_client
from app.telegram.events import create_new_message_handler

__all__ = ["create_new_message_handler", "create_telegram_client"]

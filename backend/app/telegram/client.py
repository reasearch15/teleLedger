from telethon import TelegramClient  # type: ignore[import-untyped]

from app.core.config import Settings


class TelegramConfigurationError(RuntimeError):
    """Raised when the listener is started without complete credentials."""


def create_telegram_client(settings: Settings) -> TelegramClient:
    """Create a local-session Telethon client without connecting it."""
    api_id = settings.telegram_api_id
    api_hash = settings.telegram_api_hash
    session_name = settings.telegram_session_name
    if api_id is None or api_hash is None or session_name is None:
        raise TelegramConfigurationError("Telegram listener credentials are incomplete")

    return TelegramClient(
        session=session_name,
        api_id=api_id,
        api_hash=api_hash.get_secret_value(),
    )

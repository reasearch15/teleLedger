from telethon import utils  # type: ignore[import-untyped]


def telegram_display_name(entity: object | None) -> str:
    """Return a readable Telegram entity name without exposing phone numbers."""
    if entity is None:
        return "<unknown>"
    display_name = str(utils.get_display_name(entity)).strip()
    return display_name or "<unnamed>"


def telegram_entity_id(entity: object | None) -> str:
    """Return Telethon's marked peer ID, including -100 channel prefixes."""
    if entity is None:
        return "<unknown>"
    return str(utils.get_peer_id(entity))


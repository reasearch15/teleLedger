from __future__ import annotations

from telethon import utils  # type: ignore[import-untyped]
from telethon.tl import types  # type: ignore[import-untyped]


def normalize_telegram_chat_id(chat_id: int | None) -> int | None:
    """Normalize Telegram peer IDs to Telethon's marked form (e.g. -100…).

    Accepts marked IDs already returned by ``utils.get_peer_id``, bare positive
    channel/supergroup IDs from configuration, and classic basic-group IDs.
    """
    if chat_id is None:
        return None
    value = int(chat_id)
    if value < 0:
        return value
    # Positive configured channel/supergroup IDs are stored as -100{id}.
    return int(f"-100{value}")


def peer_to_chat_id(peer: object | None) -> int | None:
    """Extract a marked chat ID from a Telethon peer object."""
    if peer is None:
        return None
    try:
        return normalize_telegram_chat_id(int(utils.get_peer_id(peer)))
    except Exception:
        return None


def chat_ids_equivalent(left: int | None, right: int | None) -> bool:
    """Compare two chat IDs after normalization."""
    if left is None or right is None:
        return False
    return normalize_telegram_chat_id(left) == normalize_telegram_chat_id(right)


def marked_channel_id(channel_id: int) -> int:
    """Build a marked channel/supergroup ID from a bare channel ID."""
    if channel_id < 0:
        return channel_id
    return int(utils.get_peer_id(types.PeerChannel(channel_id)))

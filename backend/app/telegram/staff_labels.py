from __future__ import annotations


def format_actor_label(
    *,
    display_name: str | None = None,
    username: str | None = None,
    telegram_username: str | None = None,
    telegram_user_id: int | str | None = None,
) -> str | None:
    """Return a staff/Telegram actor label without inventing "Unknown"."""
    for raw in (display_name, username):
        value = str(raw).strip() if raw is not None else ""
        if value:
            return value
    telegram_name = str(telegram_username).strip() if telegram_username is not None else ""
    if telegram_name:
        return telegram_name if telegram_name.startswith("@") else f"@{telegram_name}"
    if telegram_user_id is None:
        return None
    identifier = str(telegram_user_id).strip()
    if not identifier:
        return None
    return f"Telegram user {identifier}"

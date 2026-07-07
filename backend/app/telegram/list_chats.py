from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Protocol

from telethon.tl import types  # type: ignore[import-untyped]

from app.core.config import Settings, get_settings
from app.telegram.client import create_telegram_client

TerminalReporter = Callable[[str], None]
SEPARATOR = "-" * 60


class DialogLike(Protocol):
    """Telethon dialog fields consumed by this diagnostics command."""

    id: int
    name: str
    entity: object


class DialogClient(Protocol):
    """Minimal async client contract needed to enumerate dialogs."""

    def iter_dialogs(self) -> AsyncIterator[DialogLike]:
        """Iterate every dialog available to the account."""
        ...


@dataclass(frozen=True, slots=True)
class ChatDiagnostic:
    """Printable metadata for one Telegram dialog."""

    title: str
    telegram_id: int
    chat_type: str
    username: str | None
    member_count: int | None


def _chat_type(entity: object) -> str:
    if isinstance(entity, types.User):
        return "Bot" if entity.bot else "Private Chat"
    if isinstance(entity, types.Chat):
        return "Group"
    if isinstance(entity, types.Channel):
        if entity.megagroup or entity.gigagroup:
            return "Supergroup"
        return "Channel"
    return "Private Chat"


def _optional_username(entity: object) -> str | None:
    username = getattr(entity, "username", None)
    return username if isinstance(username, str) and username else None


def _optional_member_count(entity: object) -> int | None:
    member_count = getattr(entity, "participants_count", None)
    return member_count if isinstance(member_count, int) else None


def dialog_to_diagnostic(dialog: DialogLike) -> ChatDiagnostic:
    """Convert one Telethon dialog into stable printable metadata."""
    return ChatDiagnostic(
        title=dialog.name.strip() or "<untitled>",
        telegram_id=dialog.id,
        chat_type=_chat_type(dialog.entity),
        username=_optional_username(dialog.entity),
        member_count=_optional_member_count(dialog.entity),
    )


async def discover_chats(client: DialogClient) -> list[ChatDiagnostic]:
    """Load and alphabetically sort every available Telegram dialog."""
    chats = [dialog_to_diagnostic(dialog) async for dialog in client.iter_dialogs()]
    return sorted(chats, key=lambda chat: chat.title.casefold())


def is_configured_target(chat: ChatDiagnostic, settings: Settings) -> bool:
    """Check whether a dialog matches either configured target selector."""
    if (
        settings.telegram_group_id is not None
        and chat.telegram_id == settings.telegram_group_id
    ):
        return True

    if settings.telegram_group_username is None or chat.username is None:
        return False
    configured_username = settings.telegram_group_username.removeprefix("@").casefold()
    return chat.username.removeprefix("@").casefold() == configured_username


def print_chat(
    chat: ChatDiagnostic,
    *,
    target: bool,
    report: TerminalReporter = print,
) -> None:
    """Print one clean dialog metadata block."""
    report(SEPARATOR)
    if target:
        report(">>> TARGET GROUP")
        report("")
    report(f"Title: {chat.title}")
    report(f"ID: {chat.telegram_id}")
    report(f"Type: {chat.chat_type}")
    report(f"Username: {chat.username or 'None'}")
    report(
        f"Members: {chat.member_count if chat.member_count is not None else 'Unavailable'}"
    )
    report(SEPARATOR)


async def run_list_chats(report: TerminalReporter = print) -> None:
    """Authenticate with the existing session and print all Telegram dialogs."""
    settings = get_settings()
    client = create_telegram_client(settings)
    report(f"Using Telethon session: {settings.telegram_session_name}")

    try:
        await client.start()
        chats = await discover_chats(client)
        if not chats:
            report("No Telegram dialogs were found for this account.")
            return
        for chat in chats:
            print_chat(
                chat,
                target=is_configured_target(chat, settings),
                report=report,
            )
    finally:
        await client.disconnect()


def main() -> None:
    """CLI entry point."""
    try:
        asyncio.run(run_list_chats())
    except KeyboardInterrupt:
        print("Chat diagnostics stopped.")


if __name__ == "__main__":
    main()


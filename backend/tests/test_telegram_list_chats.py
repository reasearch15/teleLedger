from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from telethon.tl import types  # type: ignore[import-untyped]

from app.core.config import Settings
from app.telegram.list_chats import (
    ChatDiagnostic,
    dialog_to_diagnostic,
    discover_chats,
    is_configured_target,
    print_chat,
)


@dataclass
class MockDialog:
    id: int
    name: str
    entity: object


class MockDialogClient:
    def __init__(self, dialogs: list[MockDialog]) -> None:
        self._dialogs = dialogs

    async def iter_dialogs(self):  # type: ignore[no-untyped-def]
        for dialog in self._dialogs:
            yield dialog


def make_channel(
    *,
    title: str,
    username: str | None,
    member_count: int,
    megagroup: bool,
) -> types.Channel:
    return types.Channel(
        id=1234567890,
        title=title,
        photo=types.ChatPhotoEmpty(),
        date=datetime(2026, 1, 1, tzinfo=UTC),
        broadcast=not megagroup,
        megagroup=megagroup,
        username=username,
        participants_count=member_count,
    )


def test_dialog_conversion_classifies_supergroup() -> None:
    diagnostic = dialog_to_diagnostic(
        MockDialog(
            id=-1001234567890,
            name="Payment confirmation!",
            entity=make_channel(
                title="Payment confirmation!",
                username=None,
                member_count=4,
                megagroup=True,
            ),
        )
    )

    assert diagnostic == ChatDiagnostic(
        title="Payment confirmation!",
        telegram_id=-1001234567890,
        chat_type="Supergroup",
        username=None,
        member_count=4,
    )


def test_dialog_conversion_classifies_private_chat_and_bot() -> None:
    private_chat = dialog_to_diagnostic(
        MockDialog(
            id=10,
            name="Alice",
            entity=types.User(id=10, first_name="Alice"),
        )
    )
    bot = dialog_to_diagnostic(
        MockDialog(
            id=11,
            name="Payments Bot",
            entity=types.User(id=11, first_name="Payments", bot=True),
        )
    )

    assert private_chat.chat_type == "Private Chat"
    assert bot.chat_type == "Bot"


@pytest.mark.asyncio
async def test_discover_chats_sorts_alphabetically() -> None:
    client = MockDialogClient(
        [
            MockDialog(2, "Zulu", types.User(id=2, first_name="Zulu")),
            MockDialog(1, "alpha", types.User(id=1, first_name="Alpha")),
        ]
    )

    chats = await discover_chats(client)

    assert [chat.title for chat in chats] == ["alpha", "Zulu"]


def test_target_matches_group_id_and_username() -> None:
    settings = Settings().model_copy(
        update={
            "telegram_group_id": -1001234567890,
            "telegram_group_username": "@payment_confirmations",
        }
    )
    by_id = ChatDiagnostic(
        "Private payments",
        -1001234567890,
        "Supergroup",
        None,
        4,
    )
    by_username = ChatDiagnostic(
        "Public payments",
        -1009999999999,
        "Supergroup",
        "Payment_Confirmations",
        10,
    )

    assert is_configured_target(by_id, settings)
    assert is_configured_target(by_username, settings)


def test_target_output_is_highlighted() -> None:
    output: list[str] = []
    chat = ChatDiagnostic(
        "Payment confirmation!",
        -1001234567890,
        "Supergroup",
        None,
        4,
    )

    print_chat(chat, target=True, report=output.append)

    rendered = "\n".join(output)
    assert ">>> TARGET GROUP" in rendered
    assert "Title: Payment confirmation!" in rendered
    assert "ID: -1001234567890" in rendered
    assert "Type: Supergroup" in rendered
    assert "Username: None" in rendered
    assert "Members: 4" in rendered

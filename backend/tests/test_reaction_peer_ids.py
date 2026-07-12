from __future__ import annotations

import pytest
from telethon.tl import types  # type: ignore[import-untyped]

from app.telegram.events import create_reaction_handler
from app.telegram.peer_ids import marked_channel_id, peer_to_chat_id


def test_peer_channel_normalizes_to_marked_id() -> None:
    assert peer_to_chat_id(types.PeerChannel(1234567890)) == -1001234567890
    assert marked_channel_id(1234567890) == -1001234567890


@pytest.mark.asyncio
async def test_handler_ignores_reaction_from_other_group() -> None:
    calls: list[tuple[int, int]] = []

    async def complete(
        message_id: int,
        chat_id: int,
        expected_chat_id: int,
        **_: object,
    ):
        calls.append((message_id, chat_id))
        from app.telegram.cashout_reactions import CashoutReactionCompletionResult

        return CashoutReactionCompletionResult(
            completed=True,
            cashout_id=1,
            reason="completed",
        )

    handler = create_reaction_handler(
        expected_chat_id=-1001234567890,
        allowed_reactions=frozenset({"✅"}),
        complete_from_reaction=complete,
        report=lambda _: None,
    )
    await handler(
        types.UpdateMessageReactions(
            peer=types.PeerChannel(999999999),
            msg_id=555,
            reactions=types.MessageReactions(
                results=[
                    types.ReactionCount(
                        reaction=types.ReactionEmoji("✅"),
                        count=1,
                    )
                ],
                recent_reactions=[],
                min=False,
                can_see_list=True,
            ),
        )
    )

    assert calls == []

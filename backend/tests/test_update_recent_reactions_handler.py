from __future__ import annotations

import logging

import pytest
from telethon.tl import types  # type: ignore[import-untyped]

from app.telegram import cashout_reactions
from app.telegram.events import create_reaction_handler


@pytest.mark.asyncio
async def test_update_recent_reactions_invokes_recent_reaction_scan(
    caplog: pytest.LogCaptureFixture,
) -> None:
    direct_message_ids: list[int] = []
    recent_scan_calls = 0

    async def complete_from_reaction(
        message_id: int,
    ) -> cashout_reactions.CashoutReactionCompletionResult:
        direct_message_ids.append(message_id)
        return cashout_reactions.CashoutReactionCompletionResult(
            completed=True,
            cashout_id=1,
            reason="completed",
        )

    async def complete_recent_reactions() -> list[
        cashout_reactions.CashoutReactionCompletionResult
    ]:
        nonlocal recent_scan_calls
        recent_scan_calls += 1
        return [
            cashout_reactions.CashoutReactionCompletionResult(
                completed=True,
                cashout_id=1,
                reason="completed",
                matched_cashout=True,
                previous_status="sent",
            )
        ]

    caplog.set_level(logging.INFO, logger="app.telegram.events")
    handler = create_reaction_handler(
        expected_chat_id=-1001234567890,
        complete_from_reaction=complete_from_reaction,
        complete_recent_reactions=complete_recent_reactions,
        report=lambda _: None,
    )

    await handler(types.UpdateRecentReactions())

    assert direct_message_ids == []
    assert recent_scan_calls == 1
    assert "telegram_recent_reactions_update_received" in caplog.messages
    assert "cashout_recent_reaction_scan_processed" in caplog.messages

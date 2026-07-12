from __future__ import annotations

from collections.abc import Iterable


def extract_reaction_emoticons(source: object | None) -> set[str]:
    """Collect emoji strings from Telethon reaction payloads."""
    found: set[str] = set()
    if source is None:
        return found
    _collect_emoticons(source, found)
    return found


def _collect_emoticons(value: object, found: set[str]) -> None:
    if value is None:
        return
    emoticon = getattr(value, "emoticon", None)
    if isinstance(emoticon, str) and emoticon:
        found.add(emoticon)

    reaction = getattr(value, "reaction", None)
    if reaction is not None and reaction is not value:
        _collect_emoticons(reaction, found)

    for attribute in ("results", "recent_reactions", "new_reactions", "reactions"):
        nested = getattr(value, attribute, None)
        if nested is None or nested is value:
            continue
        if isinstance(nested, Iterable) and not isinstance(nested, (str, bytes)):
            for item in nested:
                _collect_emoticons(item, found)
        else:
            _collect_emoticons(nested, found)

    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        for item in value:
            _collect_emoticons(item, found)


def reaction_matches_completion(
    emoticons: set[str],
    allowed: frozenset[str] | None,
) -> bool:
    """Return True when the update contains an allowed completion reaction.

    ``allowed is None`` means any active reaction completes (legacy ``*`` / ``any``).
    A non-empty frozenset requires at least one matching emoticon.
    """
    if allowed is None:
        return True
    if not emoticons:
        return False
    return bool(emoticons & allowed)


def parse_completion_reactions(raw: str | None) -> frozenset[str] | None:
    """Parse ``CASHOUT_COMPLETION_REACTIONS`` env value.

    Empty / whitespace / ``*`` / ``any`` → None (any active reaction).
    Comma-separated emojis → frozenset allowlist.
    """
    if raw is None:
        return frozenset({"✅", "👍"})
    cleaned = raw.strip()
    if not cleaned or cleaned in {"*", "any", "ANY"}:
        return None
    parts = {part.strip() for part in cleaned.split(",") if part.strip()}
    return frozenset(parts) if parts else frozenset({"✅", "👍"})

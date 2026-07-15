"""Add stable inquiry sender aliases.

Revision ID: 20260715_22
Revises: 20260714_21
Create Date: 2026-07-15 00:00:00
"""

from collections.abc import Sequence
import secrets

import sqlalchemy as sa

from alembic import op

revision: str = "20260715_22"
down_revision: str | None = "20260714_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALIAS_ADJECTIVES = (
    "Amber",
    "Blue",
    "Bright",
    "Calm",
    "Copper",
    "Crimson",
    "Emerald",
    "Golden",
    "Hidden",
    "Ivory",
    "Misty",
    "Quiet",
    "Ruby",
    "Silver",
    "Soft",
    "Sunny",
    "Velvet",
    "Warm",
)
_ALIAS_NOUNS = (
    "Cedar",
    "Falcon",
    "Fox",
    "Harbor",
    "Lantern",
    "Maple",
    "Meadow",
    "Moon",
    "Owl",
    "Panda",
    "Pine",
    "River",
    "Sparrow",
    "Stone",
    "Summit",
    "Willow",
)


def upgrade() -> None:
    """Create and backfill public aliases for external inquiry senders."""
    op.create_table(
        "inquiry_sender_aliases",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("telegram_sender_id", sa.BigInteger(), nullable=False),
        sa.Column("alias", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "telegram_sender_id",
            name="uq_inquiry_sender_aliases_telegram_sender_id",
        ),
        sa.UniqueConstraint("alias", name="uq_inquiry_sender_aliases_alias"),
    )

    bind = op.get_bind()
    sender_ids = [
        int(row[0])
        for row in bind.execute(
            sa.text(
                """
                SELECT DISTINCT telegram_sender_id
                FROM inquiry_messages
                WHERE telegram_sender_id IS NOT NULL
                  AND message_source = 'telegram_external'
                ORDER BY telegram_sender_id
                """
            )
        )
    ]
    used_aliases: set[str] = set()
    for sender_id in sender_ids:
        alias = _next_alias(used_aliases)
        used_aliases.add(alias)
        bind.execute(
            sa.text(
                """
                INSERT INTO inquiry_sender_aliases (telegram_sender_id, alias)
                VALUES (:telegram_sender_id, :alias)
                """
            ),
            {"telegram_sender_id": sender_id, "alias": alias},
        )


def downgrade() -> None:
    """Remove inquiry sender aliases."""
    op.drop_table("inquiry_sender_aliases")


def _next_alias(used_aliases: set[str]) -> str:
    for _attempt in range(500):
        alias = f"{secrets.choice(_ALIAS_ADJECTIVES)} {secrets.choice(_ALIAS_NOUNS)}"
        if len(used_aliases) >= len(_ALIAS_ADJECTIVES) * len(_ALIAS_NOUNS):
            alias = f"{alias} {secrets.randbelow(90) + 10}"
        if alias not in used_aliases:
            return alias
    raise RuntimeError("Unable to allocate a unique inquiry sender alias")

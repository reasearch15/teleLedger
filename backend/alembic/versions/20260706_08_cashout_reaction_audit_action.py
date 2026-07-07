"""Add cashout reaction completion audit action.

Revision ID: 20260706_08
Revises: 20260706_07
Create Date: 2026-07-06 00:00:07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260706_08"
down_revision: str | None = "20260706_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow audit rows for reaction-completed cashouts."""
    op.execute(
        """
        ALTER TYPE cashout_audit_action
        ADD VALUE IF NOT EXISTS 'telegram_reaction_completed'
        """
    )


def downgrade() -> None:
    """Keep enum value in place; PostgreSQL cannot cheaply remove enum values."""

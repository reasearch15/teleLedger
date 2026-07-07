"""Ensure staff-delete columns are nullable with SET NULL foreign keys.

Revision ID: 20260707_12
Revises: 20260707_11
Create Date: 2026-07-07 00:00:12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260707_12"
down_revision: str | None = "20260707_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_not_null(table: str, column: str) -> None:
    """Drop NOT NULL using dialect-appropriate SQL."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL"
            )
        )
        return
    op.alter_column(
        table,
        column,
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def _ensure_user_fk_set_null(
    table: str,
    column: str,
    constraint_name: str,
) -> None:
    """Recreate a users FK with ON DELETE SET NULL."""
    op.drop_constraint(constraint_name, table, type_="foreignkey")
    op.create_foreign_key(
        constraint_name,
        table,
        "users",
        [column],
        ["id"],
        ondelete="SET NULL",
    )


def upgrade() -> None:
    """Correct nullable staff references missed by the prior migration."""
    _drop_not_null("cashout_requests", "created_by_staff_id")
    _ensure_user_fk_set_null(
        "cashout_requests",
        "created_by_staff_id",
        "fk_cashout_requests_created_by_staff_id_users",
    )
    _ensure_user_fk_set_null(
        "cashout_requests",
        "completed_by_staff_id",
        "fk_cashout_requests_completed_by_staff_id_users",
    )

    _drop_not_null("staff_settlements", "staff_id")
    _ensure_user_fk_set_null(
        "staff_settlements",
        "staff_id",
        "fk_staff_settlements_staff_id_users",
    )

    _drop_not_null("staff_settlement_audit_logs", "actor_user_id")
    _ensure_user_fk_set_null(
        "staff_settlement_audit_logs",
        "actor_user_id",
        "fk_staff_settlement_audit_logs_actor_user_id_users",
    )


def downgrade() -> None:
    """No-op: prior revision downgrade restores restrictive constraints."""

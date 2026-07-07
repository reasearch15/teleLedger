"""Create local users.

Revision ID: 20260706_02
Revises: 20260706_01
Create Date: 2026-07-06 00:00:01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260706_02"
down_revision: str | None = "20260706_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

user_role = postgresql.ENUM(
    "admin",
    "staff",
    name="user_role",
    create_type=False,
)


def upgrade() -> None:
    """Create the local authentication users table."""
    user_role.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )


def downgrade() -> None:
    """Drop local users and their role type."""
    op.drop_table("users")
    user_role.drop(op.get_bind(), checkfirst=True)


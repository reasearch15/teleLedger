"""Shared coadmin backfill helpers used by Alembic revisions."""

from __future__ import annotations

from collections.abc import Mapping

import sqlalchemy as sa
from alembic import op

DEFAULT_COADMIN_USERNAME = "default_coadmin"
# Placeholder password for the bootstrap coadmin; reset via admin UI before use.
DEFAULT_COADMIN_PASSWORD = "Unset-Default-Coadmin1"
STAFF_REQUIRES_COADMIN_CONSTRAINT = "ck_users_staff_requires_coadmin_id"


def _fetch_scalar(
    connection: sa.Connection,
    statement: sa.TextClause,
    parameters: Mapping[str, object] | None = None,
) -> object | None:
    return connection.execute(statement, parameters or {}).scalar()


def _as_int(value: object | None, *, error: str) -> int:
    if value is None:
        raise RuntimeError(error)
    if isinstance(value, int):
        return value
    return int(str(value))


def _coadmin_role_predicate(connection: sa.Connection) -> str:
    if connection.dialect.name == "postgresql":
        return "role = 'coadmin'::user_role"
    return "role = 'coadmin'"


def _existing_coadmin_id(
    connection: sa.Connection,
    *,
    username: str | None = None,
) -> int | None:
    role_predicate = _coadmin_role_predicate(connection)
    if username is not None:
        row_id = _fetch_scalar(
            connection,
            sa.text(
                f"""
                SELECT id
                FROM users
                WHERE {role_predicate} AND username = :username
                LIMIT 1
                """
            ),
            {"username": username},
        )
        if row_id is not None:
            return _as_int(row_id, error="Invalid coadmin id")

    row_id = _fetch_scalar(
        connection,
        sa.text(
            f"""
            SELECT id
            FROM users
            WHERE {role_predicate}
            ORDER BY id
            LIMIT 1
            """
        ),
    )
    return _as_int(row_id, error="Invalid coadmin id") if row_id is not None else None


def _insert_default_coadmin(connection: sa.Connection) -> int:
    from app.auth.security import hash_password, staff_color_for_username

    username = DEFAULT_COADMIN_USERNAME
    password_hash = hash_password(DEFAULT_COADMIN_PASSWORD)
    staff_color = staff_color_for_username(username)
    if connection.dialect.name == "postgresql":
        row_id = _fetch_scalar(
            connection,
            sa.text(
                """
                INSERT INTO users (
                    username,
                    password_hash,
                    role,
                    is_active,
                    staff_color,
                    created_at,
                    updated_at
                )
                VALUES (
                    :username,
                    :password_hash,
                    'coadmin'::user_role,
                    true,
                    :staff_color,
                    now(),
                    now()
                )
                RETURNING id
                """
            ),
            {
                "username": username,
                "password_hash": password_hash,
                "staff_color": staff_color,
            },
        )
        if row_id is None:
            raise RuntimeError("Failed to create default coadmin account")
        return _as_int(row_id, error="Failed to create default coadmin account")

    connection.execute(
        sa.text(
            """
            INSERT INTO users (
                username,
                password_hash,
                role,
                is_active,
                staff_color,
                created_at,
                updated_at
            )
            VALUES (
                :username,
                :password_hash,
                'coadmin',
                1,
                :staff_color,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            )
            """
        ),
        {
            "username": username,
            "password_hash": password_hash,
            "staff_color": staff_color,
        },
    )
    row_id = _fetch_scalar(
        connection,
        sa.text("SELECT id FROM users WHERE username = :username"),
        {"username": username},
    )
    if row_id is None:
        raise RuntimeError("Failed to create default coadmin account")
    return _as_int(row_id, error="Failed to create default coadmin account")


def ensure_coadmin_target(connection: sa.Connection) -> int:
    """Return the coadmin account used to backfill orphan staff rows."""
    existing_default = _existing_coadmin_id(connection, username=DEFAULT_COADMIN_USERNAME)
    if existing_default is not None:
        return existing_default

    existing_any = _existing_coadmin_id(connection)
    if existing_any is not None:
        return existing_any

    return _insert_default_coadmin(connection)


def assign_orphan_staff(connection: sa.Connection, coadmin_id: int) -> None:
    """Attach legacy staff accounts with no coadmin owner."""
    staff_predicate = (
        "role = 'staff'::user_role"
        if connection.dialect.name == "postgresql"
        else "role = 'staff'"
    )
    connection.execute(
        sa.text(
            f"""
            UPDATE users
            SET coadmin_id = :coadmin_id
            WHERE {staff_predicate} AND coadmin_id IS NULL
            """
        ),
        {"coadmin_id": coadmin_id},
    )


def add_staff_coadmin_required_constraint() -> None:
    """Require coadmin ownership for staff rows while leaving other roles unchanged."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        inspector = sa.inspect(bind)
        existing = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("users")
        }
        if STAFF_REQUIRES_COADMIN_CONSTRAINT in existing:
            return

    op.create_check_constraint(
        STAFF_REQUIRES_COADMIN_CONSTRAINT,
        "users",
        "role != 'staff' OR coadmin_id IS NOT NULL",
    )


def drop_staff_coadmin_required_constraint() -> None:
    """Drop the staff ownership check when present."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        inspector = sa.inspect(bind)
        existing = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("users")
        }
        if STAFF_REQUIRES_COADMIN_CONSTRAINT not in existing:
            return

    op.drop_constraint(STAFF_REQUIRES_COADMIN_CONSTRAINT, "users", type_="check")


def run_coadmin_backfill() -> None:
    """Create default coadmin when needed and attach orphan staff rows."""
    connection = op.get_bind()
    target_coadmin_id = ensure_coadmin_target(connection)
    assign_orphan_staff(connection, target_coadmin_id)
    add_staff_coadmin_required_constraint()

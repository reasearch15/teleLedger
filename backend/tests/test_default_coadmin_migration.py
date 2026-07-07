from __future__ import annotations

import importlib.util
import types
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from app.auth.security import hash_password, verify_password
from app.db.base import Base
from app.models.user import UserRole

BACKEND_ROOT = Path(__file__).resolve().parents[1]
STAFF_PASSWORD = "A-secure-staff-password"
STAFF_PASSWORD_HASH = hash_password(STAFF_PASSWORD)
MIGRATION_14_PATH = (
    BACKEND_ROOT / "alembic" / "versions" / "20260707_14_coadmin_payment_dismissals.py"
)
MIGRATION_PATH = (
    BACKEND_ROOT / "alembic" / "versions" / "20260707_15_default_coadmin_staff_backfill.py"
)
COADMIN_BACKFILL_PATH = BACKEND_ROOT / "app" / "db" / "coadmin_backfill.py"
ALEMBIC_ENV_PATH = BACKEND_ROOT / "alembic" / "env.py"


def _load_migration() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "migration_20260707_15",
        MIGRATION_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load default coadmin migration module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()


def _sync_engine() -> Engine:
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_legacy_staff(connection: sa.Connection) -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    connection.execute(
        sa.text(
            """
            INSERT INTO users (
                id, username, password_hash, role, is_active, staff_color,
                created_at, updated_at
            )
            VALUES (
                1, 'admin_user', :admin_hash, 'admin', 1, '#111111',
                :timestamp, :timestamp
            )
            """
        ),
        {
            "admin_hash": hash_password("A-secure-admin-password"),
            "timestamp": timestamp,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO users (
                id, username, password_hash, role, is_active, staff_color,
                created_at, updated_at
            )
            VALUES (
                2, 'legacy_staff', :staff_hash, 'staff', 1, '#2563EB',
                :timestamp, :timestamp
            )
            """
        ),
        {
            "staff_hash": STAFF_PASSWORD_HASH,
            "timestamp": timestamp,
        },
    )


def test_migration_module_defines_default_coadmin_backfill() -> None:
    migration_source = MIGRATION_PATH.read_text(encoding="utf-8")
    backfill_source = COADMIN_BACKFILL_PATH.read_text(encoding="utf-8")
    assert 'DEFAULT_COADMIN_USERNAME = "default_coadmin"' in backfill_source
    assert "ck_users_staff_requires_coadmin_id" in backfill_source
    assert "role != 'staff' OR coadmin_id IS NOT NULL" in backfill_source
    assert "assign_orphan_staff" in backfill_source
    assert "run_coadmin_backfill" in migration_source


def test_coadmin_enum_migration_commits_before_use() -> None:
    migration_14 = MIGRATION_14_PATH.read_text(encoding="utf-8")
    alembic_env = ALEMBIC_ENV_PATH.read_text(encoding="utf-8")

    assert "autocommit_block" in migration_14
    assert "ADD VALUE IF NOT EXISTS 'coadmin'" in migration_14
    assert "run_coadmin_backfill" in migration_14
    assert "transaction_per_migration=True" in alembic_env


def test_migration_creates_default_coadmin_and_assigns_orphan_staff() -> None:
    engine = _sync_engine()
    with engine.begin() as connection:
        Base.metadata.create_all(bind=connection)
        _seed_legacy_staff(connection)

        target_id = migration.ensure_coadmin_target(connection)
        migration.assign_orphan_staff(connection, target_id)

        coadmin = connection.execute(
            sa.text("SELECT username, role, is_active FROM users WHERE id = :user_id"),
            {"user_id": target_id},
        ).one()
        staff = connection.execute(
            sa.text(
                "SELECT coadmin_id, password_hash, role FROM users "
                "WHERE username = 'legacy_staff'"
            )
        ).one()

    assert coadmin.username == migration.DEFAULT_COADMIN_USERNAME
    assert coadmin.role == "coadmin"
    assert bool(coadmin.is_active) is True
    assert staff.coadmin_id == target_id
    assert staff.role == UserRole.STAFF.value
    valid, _ = verify_password(STAFF_PASSWORD, staff.password_hash)
    assert valid is True


def test_migration_adds_staff_coadmin_required_constraint() -> None:
    backfill_source = COADMIN_BACKFILL_PATH.read_text(encoding="utf-8")
    assert "add_staff_coadmin_required_constraint" in backfill_source
    assert migration.STAFF_REQUIRES_COADMIN_CONSTRAINT == "ck_users_staff_requires_coadmin_id"
    assert "role != 'staff' OR coadmin_id IS NOT NULL" in backfill_source


def test_migration_reuses_existing_coadmin_without_creating_default() -> None:
    engine = _sync_engine()
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    with engine.begin() as connection:
        Base.metadata.create_all(bind=connection)
        connection.execute(
            sa.text(
                """
                INSERT INTO users (
                    id, username, password_hash, role, is_active, staff_color,
                    created_at, updated_at
                )
                VALUES (
                    10, 'ops_coadmin', :password_hash, 'coadmin', 1, '#7C3AED',
                    :timestamp, :timestamp
                )
                """
            ),
            {
                "password_hash": hash_password("Another-secure-password"),
                "timestamp": timestamp,
            },
        )
        _seed_legacy_staff(connection)

        target_id = migration.ensure_coadmin_target(connection)
        migration.assign_orphan_staff(connection, target_id)

        coadmin_count = connection.execute(
            sa.text("SELECT COUNT(*) FROM users WHERE role = 'coadmin'")
        ).scalar_one()
        staff = connection.execute(
            sa.text("SELECT coadmin_id FROM users WHERE username = 'legacy_staff'")
        ).one()

    assert coadmin_count == 1
    assert target_id == 10
    assert staff.coadmin_id == 10

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_corrective_migration_emits_postgresql_drop_not_null() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "20260707_12", "--sql"],
        cwd=BACKEND_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    sql = result.stdout.upper()
    assert "ALTER TABLE CASHOUT_REQUESTS ALTER COLUMN CREATED_BY_STAFF_ID DROP NOT NULL" in sql
    assert "ON DELETE SET NULL" in sql


def test_corrective_migration_module_targets_cashout_created_by_staff() -> None:
    migration_path = (
        BACKEND_ROOT
        / "alembic"
        / "versions"
        / "20260707_12_fix_staff_delete_nullable.py"
    )
    source = migration_path.read_text(encoding="utf-8")
    assert '_drop_not_null("cashout_requests", "created_by_staff_id")' in source
    assert "completed_by_staff_id" in source

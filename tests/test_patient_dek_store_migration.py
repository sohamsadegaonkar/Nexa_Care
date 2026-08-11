from __future__ import annotations

import importlib.util
from pathlib import Path


def _migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20260812_dek_store_runtime.py"
    )
    spec = importlib.util.spec_from_file_location("dek_store_runtime_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_dek_store_runtime_revision_and_parent_are_forward_only() -> None:
    migration = _migration()
    assert migration.revision == "20260812_dek_store_runtime"
    assert migration.down_revision == "20260810_identity_review"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


def test_dek_store_upgrade_contract_is_non_destructive_and_versioned() -> None:
    source = Path(migration_path := _migration().__file__).read_text(encoding="utf-8")
    assert "is_active" in source
    assert "destroyed_at IS NULL" in source
    assert "UPDATE public.patient_dek_store" in source
    assert 'op.alter_column("patient_dek_store", "is_active", nullable=False)' in source
    assert 'op.drop_constraint(_OLD_CONSTRAINT, "patient_dek_store", type_="unique")' in source
    assert 'op.create_unique_constraint(\n        _NEW_CONSTRAINT' in source
    assert 'op.create_index(_NEW_INDEX, "patient_dek_store", ["patient_id"])' in source
    assert "DEK_STORE_DOWNGRADE_BLOCKED" in source
    assert "GROUP BY patient_id" in source
    assert "never stamp past a failed migration" in source
    assert migration_path.endswith("20260812_dek_store_runtime.py")


def test_dek_store_downgrade_restores_patient_only_uniqueness_without_deletion() -> None:
    source = Path(_migration().__file__).read_text(encoding="utf-8")
    assert 'op.create_unique_constraint(\n        _OLD_CONSTRAINT' in source
    assert 'unique=True' in source
    assert 'op.drop_column("patient_dek_store", "is_active")' in source
    assert "DELETE FROM" not in source
    assert "renumber" in source

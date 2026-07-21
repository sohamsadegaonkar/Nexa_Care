from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260717_provider_password_canonical.py"


def load_migration():
    spec = importlib.util.spec_from_file_location("provider_password_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_ordered_after_current_audit_head():
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    revision = script.get_revision("20260717_provider_pwd_canonical")
    assert revision.down_revision == "20260716_audit_ledger_chain"
    assert script.get_current_head() == "20260719_security_runtime"
    assert len(revision.revision) <= 32


@pytest.mark.parametrize(
    ("canonical", "legacy", "expected"),
    [
        ("canonical", None, "canonical"),
        (None, "legacy", "legacy"),
        ("same", "same", "same"),
        (" canonical ", None, "canonical"),
    ],
)
def test_hash_resolution_covers_canonical_legacy_and_equal_rows(canonical, legacy, expected):
    assert load_migration().resolve_canonical_hash(canonical, legacy) == expected


def test_conflicting_hashes_are_not_silently_overwritten():
    with pytest.raises(RuntimeError, match="Conflicting"):
        load_migration().resolve_canonical_hash("canonical", "different")


def test_missing_hashes_fail_migration():
    with pytest.raises(RuntimeError, match="no usable"):
        load_migration().resolve_canonical_hash(None, None)


def test_migration_removes_legacy_column_and_adds_normalized_uniqueness():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'op.drop_column("provider_credential", "hashed_password")' in source
    assert "uq_provider_credential_login_identifier_normalized" in source
    assert "lower(btrim(login_identifier))" in source
    assert "password_hash" in source


def test_downgrade_restores_legacy_column_with_canonical_value():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'sa.Column("hashed_password", sa.Text(), nullable=True)' in source
    assert "SET hashed_password = password_hash" in source

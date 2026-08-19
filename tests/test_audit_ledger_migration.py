from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260716_canonical_audit_ledger_chain.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "canonical_audit_ledger_migration", MIGRATION
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EmptyRows:
    def mappings(self):
        return []


def test_audit_migration_precedes_current_single_head():
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["20260819_widen_vault_pii_columns"]
    revision = script.get_revision("20260716_audit_ledger_chain")
    assert revision.down_revision == "20260715_patient_auth_identity"


def test_audit_migration_upgrade_evolves_existing_table():
    module = _module()
    bind = MagicMock()
    bind.execute.return_value = EmptyRows()
    with (
        patch.object(module.op, "get_bind", return_value=bind),
        patch.object(module.op, "add_column") as add_column,
        patch.object(module.op, "alter_column") as alter_column,
        patch.object(module.op, "create_unique_constraint") as create_unique,
        patch.object(module.op, "create_check_constraint"),
        patch.object(module.op, "create_index"),
    ):
        module.upgrade()

    assert all(item.args[0] == "audit_ledger" for item in add_column.call_args_list)
    assert {item.args[1].name for item in add_column.call_args_list} == {
        "trace_id",
        "status",
        "previous_hash",
        "record_hash",
        "created_at",
    }
    altered_not_null = {
        item.args[1]
        for item in alter_column.call_args_list
        if item.kwargs.get("nullable") is False
    }
    assert {"previous_hash", "record_hash"}.issubset(altered_not_null)
    constraint_names = {item.args[0] for item in create_unique.call_args_list}
    assert constraint_names == {
        "uq_audit_ledger_previous_hash",
        "uq_audit_ledger_record_hash",
    }


def test_audit_migration_downgrade_operations_are_valid_and_ordered():
    module = _module()
    with (
        patch.object(module.op, "drop_index") as drop_index,
        patch.object(module.op, "drop_constraint") as drop_constraint,
        patch.object(module.op, "drop_column") as drop_column,
    ):
        module.downgrade()

    assert drop_index.call_count == 4
    assert drop_constraint.call_count == 3
    assert [item.args[1] for item in drop_column.call_args_list] == [
        "created_at",
        "record_hash",
        "previous_hash",
        "status",
        "trace_id",
    ]

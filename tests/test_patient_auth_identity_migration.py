from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "20260715_add_patient_auth_identities.py"


def _module():
    spec = importlib.util.spec_from_file_location("patient_auth_identity_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_current_single_head() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["20260718_security_governance"]
    assert script.get_revision("20260715_patient_auth_identity").down_revision == "20260714_provider_schema"


def test_upgrade_defines_constraints_and_index() -> None:
    module = _module()
    with patch.object(module.op, "create_table") as create_table, patch.object(
        module.op, "create_index"
    ) as create_index:
        module.upgrade()
    assert create_table.call_args.args[0] == "patient_auth_identities"
    rendered = " ".join(str(argument) for argument in create_table.call_args.args[1:])
    assert "patient_id" in rendered
    assert "provider_subject" in rendered
    patient_column = next(
        argument
        for argument in create_table.call_args.args[1:]
        if getattr(argument, "name", None) == "patient_id"
    )
    assert next(iter(patient_column.foreign_keys)).ondelete == "RESTRICT"
    constraints = create_table.call_args.args[1:]
    assert any(
        getattr(item, "name", None) == "uq_patient_auth_identity_provider_subject"
        for item in constraints
    )
    create_index.assert_called_once_with(
        "ix_patient_auth_identities_patient_id",
        "patient_auth_identities",
        ["patient_id"],
    )


def test_downgrade_removes_index_then_table() -> None:
    module = _module()
    with patch.object(module.op, "drop_index") as drop_index, patch.object(
        module.op, "drop_table"
    ) as drop_table:
        module.downgrade()
    drop_index.assert_called_once_with(
        "ix_patient_auth_identities_patient_id",
        table_name="patient_auth_identities",
    )
    drop_table.assert_called_once_with("patient_auth_identities")

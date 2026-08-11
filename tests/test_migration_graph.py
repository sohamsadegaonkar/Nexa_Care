from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
CLEANUP_REVISION = "20260704_drop_raw_pii_from_vault"
CORE_REVISION = "20260705_nexa_v1"
EXPECTED_HEAD = "20260812_dek_store_runtime"


def _scripts() -> ScriptDirectory:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_cleanup_depends_on_core_schema() -> None:
    cleanup = _scripts().get_revision(CLEANUP_REVISION)
    assert cleanup is not None
    assert cleanup.dependencies == CORE_REVISION


def test_migration_chain_has_expected_single_head() -> None:
    assert _scripts().get_heads() == [EXPECTED_HEAD]


def test_eligibility_reason_head_descends_from_candidate_eligibility() -> None:
    revision = _scripts().get_revision("20260806_eligibility_reason")
    assert revision is not None
    assert revision.down_revision == "20260806_candidate_eligibility"


def test_dek_store_runtime_head_descends_from_identity_review() -> None:
    revision = _scripts().get_revision(EXPECTED_HEAD)
    assert revision is not None
    assert revision.down_revision == "20260810_identity_review"


def test_migration_revision_ids_fit_alembic_version_column() -> None:
    too_long = [
        revision.revision
        for revision in _scripts().walk_revisions(base="base", head="heads")
        if len(revision.revision) > 32
    ]
    assert not too_long


def test_final_runtime_fix_repairs_uuid_head_and_adds_recovery_schema() -> None:
    source = (
        ROOT / "alembic" / "versions" / "20260720_final_runtime_fix.py"
    ).read_text()
    assert 'revision: str = "20260720_final_runtime_fix"' in source
    assert 'down_revision: Union[str, None] = "20260719_security_runtime"' in source
    assert "type_=postgresql.UUID(as_uuid=True)" in source
    assert "fk_audit_chain_heads_head_event" in source
    assert "lease_expires_at" in source
    assert "mutation_idempotency" in source


def test_policy_audit_type_hardening_descends_from_runtime_fix() -> None:
    source = (
        ROOT / "alembic" / "versions" / "20260721_policy_audit_types.py"
    ).read_text()
    assert 'revision: str = "20260721_policy_audit_types"' in source
    assert 'down_revision: Union[str, None] = "20260720_final_runtime_fix"' in source
    assert "type_=sa.DateTime(timezone=True)" in source
    assert "type_=sa.String(192)" in source


def test_cleanup_sql_guards_absent_tables() -> None:
    source = (
        ROOT / "alembic" / "versions" / "20260704_drop_raw_pii_from_vault.py"
    ).read_text()
    assert "to_regclass('public.nexa_vault') IS NOT NULL" in source
    assert "to_regclass('public.nexa_clinical') IS NOT NULL" in source
    assert "information_schema.columns" in source
    assert "ADD COLUMN raw_pii JSONB" in source


def test_no_root_revision_references_cross_root_tables() -> None:
    scripts = _scripts()
    for revision in scripts.walk_revisions(base="base", head="heads"):
        if revision.down_revision is None and revision.revision == CLEANUP_REVISION:
            assert revision.dependencies == CORE_REVISION


def test_device_timestamp_correction_descends_from_previous_head() -> None:
    correction = _scripts().get_revision("20260713_device_key_timestamps")
    assert correction is not None
    assert correction.down_revision == "20260712_tombstone_integrity"


def test_device_timestamp_correction_is_guarded_and_non_destructive() -> None:
    source = (
        ROOT / "alembic" / "versions" / "20260713_add_patient_device_key_timestamps.py"
    ).read_text()
    assert 'get_columns("patient_device_keys")' in source
    assert 'if "created_at" not in columns' in source
    assert 'if "updated_at" not in columns' in source
    assert "sa.DateTime(timezone=True)" in source
    assert "server_default=sa.func.now()" in source
    assert "op.drop_column" not in source

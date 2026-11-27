from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from scripts import run_pilot_migrations
from scripts.ci import prepare_ci_shared_db
from scripts import validate_pilot_runtime_evidence


ROOT = Path(__file__).resolve().parents[1]
REVISION = "20260919_medication_catalog"
PARENT = "20260919_prescriber_eligibility"
MIGRATION = ROOT / "alembic" / "versions" / f"{REVISION}.py"


def _scripts() -> ScriptDirectory:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_document_access_extends_medication_catalog() -> None:
    assert _scripts().get_heads() == ["20261120_document_access"]
    assert _scripts().get_revision("20261120_document_access").down_revision == REVISION
    revision = _scripts().get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PARENT


def test_medication_catalog_migration_contains_physical_security_guards() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for required in (
        "MEDICATION_CATALOG_RELEASE_REVIEW",
        "medication_catalog_release",
        "medication_catalog_entry",
        "medication_catalog_evidence",
        "medication_catalog_emergency_deny",
        "uq_medication_catalog_release_single_active",
        "status = 'ACTIVE'",
        "BEFORE UPDATE OR DELETE",
        "MEDICATION_CATALOG_RELEASE_IMMUTABLE",
        "MEDICATION_CATALOG_PUBLISHED_CONTENT_IMMUTABLE",
        "MEDICATION_CATALOG_EMERGENCY_HISTORY_IMMUTABLE",
        "ERRCODE = '55000'",
        "ECDSA_SHA_256",
    ):
        assert required in source
    assert 'op.create_table(\n        "prescription"' not in source.lower()
    assert 'op.create_table(\n        "prescription_item"' not in source.lower()


def test_current_migration_authorities_all_equal_repository_head() -> None:
    repository_heads = tuple(_scripts().get_heads())
    assert repository_heads == ("20261120_document_access",)
    assert run_pilot_migrations.EXPECTED_HEAD == repository_heads[0]
    assert prepare_ci_shared_db.HEAD == repository_heads[0]
    assert validate_pilot_runtime_evidence.CURRENT_MIGRATION_HEAD == repository_heads[0]

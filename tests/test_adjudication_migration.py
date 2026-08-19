from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
BASE_MIGRATION = ROOT / "alembic/versions/20260730_source_adjudicate.py"
MIGRATION = ROOT / "alembic/versions/20260731_adjudication_harden.py"


def test_adjudication_remains_on_the_single_head_chain_with_expected_parent():
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["20260819_widen_vault_pii_columns"]
    assert (
        script.get_revision("20260801_textract_candidates").down_revision
        == "20260731_adjudication_harden"
    )
    code = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "20260731_adjudication_harden"' in code
    assert 'down_revision = "20260730_source_adjudicate"' in code


def test_migration_has_immutable_submission_and_workflow_constraints():
    code = BASE_MIGRATION.read_text(encoding="utf-8")
    assert '"adjudication_cases"' in code
    assert '"adjudication_submissions"' in code
    assert "uq_adjudication_submissions_idempotency" in code
    assert "uq_adjudication_submissions_case_attempt" in code
    assert "ck_adjudication_cases_source_binding" in code
    assert "ck_adjudication_submissions_outcome" in code
    assert "clinical_payload" in code
    assert 'drop_table("adjudication_submissions")' in code


def test_hardening_migration_enforces_cross_case_and_hash_integrity():
    code = MIGRATION.read_text(encoding="utf-8")
    assert "ck_adjudication_cases_version_positive" in code
    assert "ck_adjudication_cases_operation_hash_length" in code
    assert "ck_adjudication_submissions_attempt_positive" in code
    assert "ck_adjudication_submissions_content_hash_length" in code
    assert "ck_adjudication_submissions_source_binding" in code
    assert "fk_adjudication_cases_accepted_submission_same_case" in code
    assert "uq_adjudication_submissions_case_id_id" in code
    assert "fk_adjudication_submissions_document" in code
    assert "fk_adjudication_submissions_job" in code
    assert "fk_adjudication_submissions_routing" in code
    assert "fk_adjudication_submissions_decision" in code
    assert "def downgrade()" in code


def test_clinical_commit_guard_binds_decision_organization_to_tenant():
    code = (
        ROOT / "alembic" / "versions" / "20260815_clinical_commit_guard.py"
    ).read_text(encoding="utf-8")
    assert "ck_extraction_decisions_organization_tenant" in code
    assert "organization_id IS DISTINCT FROM tenant_id" in code
    assert "C1_DECISION_ORGANIZATION_TENANT_MISMATCH" in code
    assert "ERRCODE = '23514'" in code

from pathlib import Path

from alembic.script import ScriptDirectory
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]
REVISION = "20260919_prescriber_eligibility"
PARENT = "20260918_treatment_vitals_encounter"
MIGRATION = (
    ROOT / "alembic" / "versions" / "20260919_prescriber_eligibility.py"
)


def _scripts() -> ScriptDirectory:
    config = Config(str(ROOT / "alembic.ini"))
    return ScriptDirectory.from_config(config)


def test_prescriber_eligibility_is_the_single_linear_head() -> None:
    assert _scripts().get_heads() == [REVISION]
    revision = _scripts().get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PARENT


def test_migration_creates_append_only_authority_and_review_permission() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for required in (
        "prescribing_eligibility_decision",
        "PRESCRIBING_ELIGIBILITY_REVIEW",
        "FULL_RMP_MODERN_MEDICINE",
        "SOURCE_UNAVAILABLE",
        "uq_prescribing_eligibility_provider_version",
        "uq_prescribing_eligibility_previous_decision",
        "ck_prescribing_eligibility_no_self_review",
        "ck_prescribing_eligibility_positive_shape",
        "evidence_sha256",
        "professional_verification_version",
        "PRESCRIBING_ELIGIBILITY_DECISION_IMMUTABLE",
        "BEFORE UPDATE OR DELETE",
        "ERRCODE = '55000'",
    ):
        assert required in source


def test_migration_starts_empty_and_does_not_touch_prescription_storage() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "INSERT INTO prescribing_eligibility_decision" not in source
    assert "patient_medications" not in source
    assert "prescription_item" not in source.lower()
    assert "CREATE TABLE prescription" not in source.upper()

def test_model_and_migration_share_strict_evidence_digest_constraint() -> None:
    model_source = (
        ROOT / "app" / "models" / "provider.py"
    ).read_text(encoding="utf-8")
    migration_source = MIGRATION.read_text(encoding="utf-8")
    strict_digest = "evidence_sha256 ~ '^[0-9a-f]{64}$'"

    assert strict_digest in model_source
    assert strict_digest in migration_source


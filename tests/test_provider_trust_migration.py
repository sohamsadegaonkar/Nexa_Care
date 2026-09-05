from pathlib import Path
import ast

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
REVISION = "20260830_delegated_assurance"
TRUST_REVISION = "20260830_provider_trust"
CONTACT_ASSURANCE_REVISION = "20260902_contact_assurance"
LIFECYCLE_REVISION = "20260903_trust_lifecycle"
AUTHORIZATION_REVISION = "20260903_trust_authorization"
EVIDENCE_REVISION = "20260904_verification_evidence"
APPLICATION_REVISION = "20260905_verification_application"
SCHEDULER_REVISION = "20260906_verification_scheduler"


def _source() -> str:
    return (ROOT / "alembic" / "versions" / f"{REVISION}.py").read_text(
        encoding="utf-8"
    )


def _trust_source() -> str:
    return (ROOT / "alembic" / "versions" / f"{TRUST_REVISION}.py").read_text(
        encoding="utf-8"
    )


def _lifecycle_source() -> str:
    return (ROOT / "alembic" / "versions" / f"{LIFECYCLE_REVISION}.py").read_text(
        encoding="utf-8"
    )


def test_provider_trust_migration_is_current_single_head() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [SCHEDULER_REVISION]
    assert scripts.get_revision(REVISION).down_revision == "20260830_provider_trust"
    assert (
        scripts.get_revision(LIFECYCLE_REVISION).down_revision
        == CONTACT_ASSURANCE_REVISION
    )
    assert (
        scripts.get_revision(AUTHORIZATION_REVISION).down_revision == LIFECYCLE_REVISION
    )
    assert (
        scripts.get_revision(EVIDENCE_REVISION).down_revision == AUTHORIZATION_REVISION
    )
    assert scripts.get_revision(APPLICATION_REVISION).down_revision == EVIDENCE_REVISION
    assert (
        scripts.get_revision(SCHEDULER_REVISION).down_revision == APPLICATION_REVISION
    )


def test_lifecycle_version_migration_is_documented_backfilled_and_forward_only() -> (
    None
):
    source = _lifecycle_source()
    for required in (
        "professional_verification",
        "facility_verification",
        "provider_hospital_affiliation",
        "SET version = 1",
        "nullable=False",
        "version > 0",
        "Purpose:",
        "Preconditions:",
        "Existing-data behavior:",
        "Locking risk:",
        "Rollback position:",
        "Validation query:",
        "Forward-fix strategy:",
        "raise RuntimeError",
    ):
        assert required in source


def test_migration_backfills_trust_state_fail_closed_and_is_forward_only() -> None:
    source = _trust_source()
    assert "'NOT_SUBMITTED'" in source
    assert "'DRAFT'" in source
    assert "'PENDING_ACTIVATION'" in source
    assert "medical_registration_number" not in source
    assert "raise RuntimeError" in source
    assert "op.drop_table" not in source


def test_migration_defines_modelled_tables_constraints_and_documentation() -> None:
    source = _trust_source()
    for required in (
        "professional_verification",
        "facility_verification",
        "email_verified_at",
        "phone_verified_at",
        "facility_type",
        "trust_status",
        "uq_professional_verification_authority_registration",
        "ck_professional_verification_status",
        "ck_facility_verification_status",
        "ck_provider_hospital_affiliation_trust_status",
        "Purpose:",
        "Preconditions:",
        "Existing-data behavior:",
        "Locking risk:",
        "Rollback position:",
        "Validation query:",
        "Forward-fix strategy:",
    ):
        assert required in source


def test_delegated_assurance_migration_requires_complete_non_secret_provenance() -> (
    None
):
    source = _source()
    for required in (
        "authorization_initiated_at",
        "authorization_authentication_method",
        "authorization_mfa_verified_at",
        "authorization_assurance_policy_version",
        "ck_extraction_jobs_delegated_assurance_complete",
        "forward-only",
        "raise RuntimeError",
    ):
        assert required in source


def test_every_production_extraction_job_creation_sets_complete_assurance() -> None:
    required = {
        "authorization_initiated_at",
        "authorization_authentication_method",
        "authorization_mfa_verified_at",
        "authorization_assurance_policy_version",
    }
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Name) and node.func.id == "ExtractionJob"
            ):
                continue
            assert required.issubset({item.arg for item in node.keywords})

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
REVISION = "20260903_trust_authorization"
APPLICATION_REVISION = "20260905_verification_application"
SCHEDULER_REVISION = "20260906_verification_scheduler"
DEVICE_TRUST_REVISION = "20260909_device_trust_lifecycle"
REGISTRATION_RECOVERY_REVISION = "20260910_registration_recovery_review"
PATIENT_SEARCH_REVISION = "20260914_patient_search_identifiers"
CLINICAL_ACCESS_REVISION = "20260916_clinical_access_sessions"
HEAD_REVISION = "20260917_treatment_session_operations"


def test_trust_authorization_migration_is_single_head_and_forward_only() -> None:
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == [HEAD_REVISION]
    revision = scripts.get_revision(REVISION)
    assert revision is not None and revision.down_revision == "20260903_trust_lifecycle"
    evidence_revision = scripts.get_revision("20260904_verification_evidence")
    assert evidence_revision is not None and evidence_revision.down_revision == REVISION
    app_revision = scripts.get_revision(APPLICATION_REVISION)
    assert (
        app_revision is not None
        and app_revision.down_revision == "20260904_verification_evidence"
    )
    scheduler_revision = scripts.get_revision(SCHEDULER_REVISION)
    assert (
        scheduler_revision is not None
        and scheduler_revision.down_revision == APPLICATION_REVISION
    )
    device_revision = scripts.get_revision(DEVICE_TRUST_REVISION)
    assert device_revision is not None and device_revision.down_revision == SCHEDULER_REVISION
    recovery_revision = scripts.get_revision(REGISTRATION_RECOVERY_REVISION)
    assert (
        recovery_revision is not None
        and recovery_revision.down_revision == DEVICE_TRUST_REVISION
    )
    patient_search_revision = scripts.get_revision(PATIENT_SEARCH_REVISION)
    assert (
        patient_search_revision is not None
        and patient_search_revision.down_revision == REGISTRATION_RECOVERY_REVISION
    )
    clinical_access_revision = scripts.get_revision(CLINICAL_ACCESS_REVISION)
    assert (
        clinical_access_revision is not None
        and clinical_access_revision.down_revision == PATIENT_SEARCH_REVISION
    )
    head_revision = scripts.get_revision(HEAD_REVISION)
    assert (
        head_revision is not None
        and head_revision.down_revision == CLINICAL_ACCESS_REVISION
    )
    source = (ROOT / "alembic" / "versions" / f"{REVISION}.py").read_text()
    for required in (
        "provider_trust_permission_grant",
        "PROFESSIONAL_REVIEW",
        "FACILITY_REVIEW",
        "AFFILIATION_MANAGE",
        "TRUST_PERMISSION_MANAGE",
        "revoked_at IS NULL",
        "Purpose:",
        "Existing-data behavior:",
        "inserts no grants",
        "raise RuntimeError",
    ):
        assert required in source

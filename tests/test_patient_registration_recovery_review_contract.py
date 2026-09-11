"""Static non-regression guards for the registration-account recovery boundary."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_recovery_mobile_transport_is_explicitly_unauthenticated() -> None:
    source = _read("nexa-client/packages/app/services/patientRegistrationRecovery.ts")
    assert source.count("{ noAuth: true }") >= 3


def test_repairable_identity_proof_uses_atomic_authority_exchange() -> None:
    route = _read("app/api/v2/registration_recovery_routes.py")
    assert "exchange_registration_recovery_attempt_for_capability" in route
    assert "issue_registration_recovery_capability(" not in route


def test_manual_review_reference_is_a_durable_opaque_case_handle() -> None:
    route = _read("app/api/v2/registration_recovery_routes.py")
    review_service = _read("app/services/patient_registration_recovery_review_service.py")
    review_model = _read("app/models/patient_registration_recovery_review.py")
    assert '"case_reference"' in route
    assert '"recovery_reference"' not in route
    assert "open_registration_recovery_review_case" in route
    assert "case_reference" in review_service
    assert "PatientRegistrationRecoveryReviewCase" in review_model


def test_review_status_surface_is_patient_safe_and_reviewer_authority_is_separate() -> None:
    review_routes = _read("app/api/v2/registration_recovery_review_routes.py")
    gate = _read("app/core/registration_recovery_review_gate.py")
    patient_model = review_routes.split("class ReviewerCaseResponse", 1)[0]
    assert "graph_fingerprint" not in patient_model
    assert "provider_subject" not in patient_model
    assert "assigned_reviewer_id" not in patient_model
    assert "get_registration_recovery_reviewer" in review_routes
    assert "registration_recovery_reviewer" in gate
    assert "REGISTRATION_RECOVERY_REVIEW_MFA_MAX_AGE_SECONDS" in gate


def test_manual_review_resolution_reuses_locked_automatic_repair_domain() -> None:
    review_service = _read("app/services/patient_registration_recovery_review_service.py")
    assert "pg_advisory_xact_lock" in review_service
    assert "registration_recovery_lock_key" in review_service
    assert "inspect_patient_registration_recovery" in review_service
    assert "REPAIR_RESTORE_RECORD" in review_service
    assert "REPAIR_REBIND_MERGED_IDENTITY" in review_service
    assert "issue_patient_access_session" not in review_service
    assert "issue_device_enrollment_token" not in review_service


def test_erasure_and_revocation_are_explicit_nonrepairs() -> None:
    service = _read("app/services/patient_registration_recovery_service.py")
    governance = _read("docs/governance/PATIENT_REGISTRATION_ACCOUNT_RECOVERY.md").lower()
    assert "ERASURE_STATE_PRESENT" in service
    assert "IDENTITY_REVOKED" in service
    assert "does not automatically clear or override" in governance
    assert "identity revocation" in governance
    assert "erasure-registry state" in governance

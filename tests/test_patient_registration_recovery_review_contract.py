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


def test_manual_review_reference_is_not_misrepresented_as_durable_case() -> None:
    route = _read("app/api/v2/registration_recovery_routes.py")
    governance = _read("docs/governance/PATIENT_REGISTRATION_ACCOUNT_RECOVERY.md")
    assert '"recovery_reference"' in route
    assert '"case_reference"' not in route
    assert "not a durable case identifier" in governance


def test_erasure_and_revocation_are_explicit_nonrepairs() -> None:
    service = _read("app/services/patient_registration_recovery_service.py")
    governance = _read("docs/governance/PATIENT_REGISTRATION_ACCOUNT_RECOVERY.md")
    assert "ERASURE_STATE_PRESENT" in service
    assert "IDENTITY_REVOKED" in service
    assert "never clears an identity revocation" in governance

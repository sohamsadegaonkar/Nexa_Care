"""Route-level contract checks for Slice 9A registration-recovery review."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_slice9_review_router_uses_stable_v2_prefix_and_is_registered() -> None:
    routes = _read("app/api/v2/registration_recovery_review_routes.py")
    main = _read("app/main.py")

    prefix = "/api/v2/auth/registration-recovery/review"
    assert f'prefix="{prefix}"' in routes
    assert "registration_recovery_review_v2_router" in main
    assert "app.include_router(registration_recovery_review_v2_router)" in main


def test_patient_status_response_does_not_expose_internal_authority_fields() -> None:
    routes = _read("app/api/v2/registration_recovery_review_routes.py")
    patient_model = routes.split("class ReviewerCaseResponse", 1)[0]

    for forbidden in (
        "provider_subject",
        "graph_fingerprint",
        "identity_id",
        "assigned_reviewer_id",
        "review_session_binding",
        "reviewer_authority_version",
        "access_token",
        "device_enrollment_token",
        "consent_token",
    ):
        assert forbidden not in patient_model


def test_reviewer_routes_use_independent_reviewer_dependency() -> None:
    routes = _read("app/api/v2/registration_recovery_review_routes.py")

    assert routes.count("get_registration_recovery_reviewer") >= 6
    assert "/reviewer/cases" in routes
    assert "/claim" in routes
    assert "/recover-session" in routes
    assert "/resolve" in routes

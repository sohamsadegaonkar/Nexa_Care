from __future__ import annotations

from app.api.v2.provider_trust_routes import PrescribingEligibilityReviewRequest


def test_prescribing_review_payload_is_closed_and_cannot_select_authority_fields():
    fields = set(PrescribingEligibilityReviewRequest.model_fields)
    assert fields == {
        "expected_professional_verification_version",
        "expected_previous_decision_version",
        "status",
        "practitioner_class",
        "source_type",
        "source_reference",
        "evidence_sha256",
        "decision_reason_code",
        "restriction_code",
    }
    for forbidden in {
        "provider_id",
        "reviewer_provider_id",
        "registration_authority_code",
        "registration_number_normalized",
        "valid_until",
        "capability",
        "role",
        "hospital_id",
    }:
        assert forbidden not in fields


def test_prescribing_review_route_is_command_specific():
    from app.api.v2.provider_trust_routes import router

    matches = [
        route
        for route in router.routes
        if route.path
        == "/api/v2/provider-trust/professional/{provider_id}/prescribing-eligibility"
    ]
    assert len(matches) == 1
    assert matches[0].methods == {"POST"}

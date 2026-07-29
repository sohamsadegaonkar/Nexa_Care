from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v2.pipeline_routes import create_adjudication_submission
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.services.adjudication import (
    AdjudicationError,
    _assert_authoritative_session,
    _reviewer_role,
    _validate_idempotency_key,
    _validate_session,
)


def _provider(*roles: str) -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid4(),
            display_name="Clinical reviewer",
            contact_email="reviewer@example.test",
        ),
        hospital=HospitalContext(
            hospital_id=uuid4(),
            facility_code="TEST",
            display_name="Test facility",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            roles=list(roles),
            is_primary=True,
        ),
    )


def test_generic_admin_is_not_clinically_qualified():
    with pytest.raises(AdjudicationError) as exc:
        _reviewer_role(_provider("admin"))
    assert exc.value.code == "ADJUDICATION_ROLE_REQUIRED"
    assert _reviewer_role(_provider("clinician")) == "clinician"
    assert _reviewer_role(_provider("clinical_reviewer")) == "clinical_reviewer"


def test_case_review_session_is_authoritative():
    case = SimpleNamespace(review_session_id="review-session-01")
    assert (
        _assert_authoritative_session(case, "review-session-01") == "review-session-01"
    )
    with pytest.raises(AdjudicationError) as exc:
        _assert_authoritative_session(case, "review-session-02")
    assert exc.value.code == "ADJUDICATION_SESSION_MISMATCH"


@pytest.mark.parametrize(
    ("validator", "value", "code"),
    [
        (_validate_session, "short", "ADJUDICATION_SESSION_INVALID"),
        (_validate_session, "x" * 97, "ADJUDICATION_SESSION_INVALID"),
        (
            _validate_idempotency_key,
            "short",
            "ADJUDICATION_IDEMPOTENCY_KEY_INVALID",
        ),
        (
            _validate_idempotency_key,
            "x" * 193,
            "ADJUDICATION_IDEMPOTENCY_KEY_INVALID",
        ),
        (
            _validate_idempotency_key,
            "invalid key with spaces",
            "ADJUDICATION_IDEMPOTENCY_KEY_INVALID",
        ),
    ],
)
def test_session_and_idempotency_identifiers_fail_with_stable_codes(
    validator, value, code
):
    with pytest.raises(AdjudicationError) as exc:
        validator(value)
    assert exc.value.code == code
    assert value not in str(exc.value)


@pytest.mark.asyncio
async def test_unknown_reason_code_returns_safe_api_error_without_echo():
    clinical_text = "patient potassium was 9.9"
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await create_adjudication_submission(
            case_id=str(uuid4()),
            raw_payload={
                "review_session_id": "review-session-01",
                "idempotency_key": "submission-key-01",
                "outcome": "REJECTED",
                "fields": [],
                "reason_codes": [clinical_text],
            },
            provider=_provider("clinician"),
            db=db,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == {"error_code": "ADJUDICATION_PAYLOAD_INVALID"}
    assert clinical_text not in str(exc.value.detail)
    db.rollback.assert_awaited_once()

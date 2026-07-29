from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
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
    _live_access,
    _revalidate_case_graph,
    _validate_idempotency_key,
    _validate_session,
    rotate_review_session,
)
from app.security.document_processing_policy import DocumentProcessingOperation
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
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
    assert _reviewer_role(_provider("admin", "clinician")) == "clinician"


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


@pytest.mark.asyncio
async def test_pending_case_session_rotation_increments_version_and_invalidates_old_session():
    provider = _provider("admin", "clinician")
    case = SimpleNamespace(
        id=uuid4(),
        patient_id=uuid4(),
        tenant_id=provider.hospital.hospital_id,
        organization_id=provider.hospital.hospital_id,
        source_document_id=uuid4(),
        job_id=uuid4(),
        routing_id=None,
        decision_id=None,
        reviewer_id=provider.actor_uid,
        reviewer_organization_id=provider.hospital.hospital_id,
        reviewer_role="clinician",
        review_session_id="review-session-old",
        status="PENDING",
        version=2,
        accepted_submission_id=None,
        clinical_committed_at=None,
        resolved_at=None,
        contract_version="1.0",
        policy_version="source-adjudication/1.0",
    )
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: case)
    job = SimpleNamespace(
        id=case.job_id, tenant_id=case.tenant_id, patient_id=case.patient_id
    )
    with (
        patch(
            "app.services.adjudication._revalidate_case_graph",
            AsyncMock(return_value=(job, SimpleNamespace())),
        ),
        patch("app.services.adjudication._live_access", AsyncMock()),
        patch("app.services.adjudication.enqueue_audit_event", AsyncMock()) as audit,
    ):
        rotated = await rotate_review_session(
            db,
            case_id=case.id,
            provider=provider,
            new_review_session_id="review-session-new",
        )
    assert rotated.review_session_id == "review-session-new"
    assert rotated.version == 3
    with pytest.raises(AdjudicationError) as exc:
        _assert_authoritative_session(rotated, "review-session-old")
    assert exc.value.code == "ADJUDICATION_SESSION_MISMATCH"
    audit.assert_awaited_once()
    assert "review-session" not in str(audit.await_args.kwargs["metadata"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_status", "committed"),
    [
        ("ACCEPTED", False),
        ("REJECTED", False),
        ("NEEDS_SPECIALIST_REVIEW", False),
        ("PENDING", True),
    ],
)
async def test_resolved_or_committed_case_cannot_rotate_session(case_status, committed):
    provider = _provider("clinician")
    case = SimpleNamespace(
        status=case_status,
        resolved_at=None if case_status == "PENDING" else object(),
        accepted_submission_id=None,
        clinical_committed_at=object() if committed else None,
    )
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: case)
    with pytest.raises(AdjudicationError) as exc:
        await rotate_review_session(
            db,
            case_id=uuid4(),
            provider=provider,
            new_review_session_id="review-session-new",
        )
    assert exc.value.code == "ADJUDICATION_RECOVERY_NOT_ALLOWED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("registry_error", "expected_code", "reason"),
    [
        (
            _PatientErasedSignal("patient-reference"),
            "ADJUDICATION_ERASURE_ACCESS_BLOCKED",
            "ERASURE_ACCESS_BLOCKED",
        ),
        (
            ErasureRegistryUnavailable("registry detail must not escape"),
            "ADJUDICATION_ERASURE_REGISTRY_UNAVAILABLE",
            "ERASURE_REGISTRY_UNAVAILABLE",
        ),
    ],
)
async def test_erasure_failures_are_stable_value_free_and_audited(
    registry_error, expected_code, reason
):
    provider = _provider("clinician")
    job = SimpleNamespace(
        id=uuid4(),
        patient_id=uuid4(),
        tenant_id=provider.hospital.hospital_id,
        authorization_provider_id=provider.actor_uid,
        consent_request_id=uuid4(),
    )
    capability = SimpleNamespace(
        allowed_operations=[DocumentProcessingOperation.READ_DOCUMENT_SOURCE.value]
    )
    with (
        patch(
            "app.services.adjudication.validate_live_document_processing_request",
            AsyncMock(return_value=capability),
        ),
        patch(
            "app.services.adjudication.check_erasure_registry",
            AsyncMock(side_effect=registry_error),
        ),
        patch("app.services.adjudication.enqueue_audit_event", AsyncMock()) as audit,
    ):
        with pytest.raises(AdjudicationError) as exc:
            await _live_access(
                AsyncMock(),
                job=job,
                provider=provider,
                operation=DocumentProcessingOperation.READ_DOCUMENT_SOURCE,
            )
    assert exc.value.code == expected_code
    assert "registry detail" not in str(exc.value)
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["metadata"] == {
        "operation": DocumentProcessingOperation.READ_DOCUMENT_SOURCE.value,
        "reason": reason,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["reviewer", "tenant"])
async def test_case_revalidation_rejects_cross_reviewer_and_cross_tenant(mismatch):
    owner = _provider("clinician")
    caller = _provider("clinician")
    case = SimpleNamespace(
        contract_version="1.0",
        policy_version="source-adjudication/1.0",
        reviewer_id=owner.actor_uid,
        reviewer_organization_id=owner.hospital.hospital_id,
        reviewer_role="clinician",
        tenant_id=owner.hospital.hospital_id,
        organization_id=owner.hospital.hospital_id,
    )
    if mismatch == "reviewer":
        caller = ProviderContext(
            provider=caller.provider,
            hospital=owner.hospital,
            affiliation=caller.affiliation,
        )
    else:
        case.reviewer_id = caller.actor_uid
    with pytest.raises(AdjudicationError) as exc:
        await _revalidate_case_graph(AsyncMock(), case=case, provider=caller)
    assert exc.value.code == "ADJUDICATION_ACCESS_DENIED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "access_code",
    [
        "ADJUDICATION_CONSENT_INACTIVE",
        "ADJUDICATION_ERASURE_ACCESS_BLOCKED",
        "ADJUDICATION_ERASURE_REGISTRY_UNAVAILABLE",
    ],
)
async def test_recovery_does_not_rotate_when_live_access_fails(access_code):
    provider = _provider("clinician")
    case = SimpleNamespace(
        id=uuid4(),
        patient_id=uuid4(),
        tenant_id=provider.hospital.hospital_id,
        organization_id=provider.hospital.hospital_id,
        source_document_id=uuid4(),
        job_id=uuid4(),
        routing_id=None,
        decision_id=None,
        reviewer_id=provider.actor_uid,
        reviewer_organization_id=provider.hospital.hospital_id,
        reviewer_role="clinician",
        review_session_id="review-session-old",
        status="PENDING",
        version=1,
        accepted_submission_id=None,
        clinical_committed_at=None,
        resolved_at=None,
        contract_version="1.0",
        policy_version="source-adjudication/1.0",
    )
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: case)
    with (
        patch(
            "app.services.adjudication._revalidate_case_graph",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.adjudication._live_access",
            AsyncMock(side_effect=AdjudicationError(access_code)),
        ),
    ):
        with pytest.raises(AdjudicationError) as exc:
            await rotate_review_session(
                db,
                case_id=case.id,
                provider=provider,
                new_review_session_id="review-session-new",
            )
    assert exc.value.code == access_code
    assert case.review_session_id == "review-session-old"
    assert case.version == 1

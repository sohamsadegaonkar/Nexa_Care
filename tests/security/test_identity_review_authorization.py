from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.core.identity_review_gate import (
    IdentityReviewGateError,
    assert_identity_review_separation,
    authorize_identity_review,
)
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
)
from app.security.identity_review_policy import IdentityReviewOperation
from app.services.approved_access_capability import ApprovedAccessCapability


def _provider(*, roles=("identity_reviewer",), hospital_id=None, actor_id=None):
    hospital_id = hospital_id or uuid.uuid4()
    actor_id = actor_id or uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=actor_id,
            display_name="Independent Reviewer",
            contact_email="reviewer@example.test",
        ),
        hospital=HospitalContext(
            hospital_id=hospital_id,
            facility_code="TEST",
            display_name="Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            roles=list(roles),
            is_primary=True,
            valid_from=datetime.now(timezone.utc) - timedelta(days=1),
            valid_until=datetime.now(timezone.utc) + timedelta(days=1),
        ),
        session_binding="a" * 64,
    )


def _capability(provider, patient_id):
    return ApprovedAccessCapability(
        patient_id=str(patient_id),
        clinician_id=provider.actor_uid,
        hospital_id=str(provider.hospital_id),
        request_id="reviewer-request",
        purpose="document_processing",
        scope=["documents"],
        is_break_glass=False,
        reason_code=None,
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        grant_type="document_processing",
        allowed_operations=("read_job_status",),
    )


@pytest.mark.asyncio
async def test_identity_reviewer_uses_own_live_read_job_capability():
    patient_id = uuid.uuid4()
    provider = _provider()
    capability = _capability(provider, patient_id)
    with (
        patch(
            "app.core.identity_review_gate.validate_document_processing_access",
            new=AsyncMock(return_value=capability),
        ) as validate,
        patch(
            "app.core.identity_review_gate.check_erasure_registry",
            new=AsyncMock(return_value=None),
        ) as erasure,
    ):
        result = await authorize_identity_review(
            MagicMock(),
            token="reviewer-own-token",
            patient_id=str(patient_id),
            tenant_id=str(provider.hospital_id),
            provider=provider,
            operation=IdentityReviewOperation.CLAIM_CASE,
        )
    assert result is capability
    assert validate.await_args.kwargs["provider_id"] == provider.actor_uid
    assert validate.await_args.kwargs["patient_id"] == str(patient_id)
    assert validate.await_args.kwargs["required_operation"].value == "read_job_status"
    erasure.assert_awaited_once_with(str(patient_id), ANY)


@pytest.mark.parametrize(
    "roles",
    [
        ("clinician",),
        ("clinical_reviewer",),
        ("admin",),
        ("auditor",),
        ("privacy_officer",),
        ("receptionist",),
        (),
    ],
)
@pytest.mark.asyncio
async def test_all_existing_roles_are_denied_without_literal_identity_role(roles):
    provider = _provider(roles=roles)
    with pytest.raises(IdentityReviewGateError) as caught:
        await authorize_identity_review(
            MagicMock(),
            token="token",
            patient_id=str(uuid.uuid4()),
            tenant_id=str(provider.hospital_id),
            provider=provider,
            operation=IdentityReviewOperation.READ_CASE,
        )
    assert caught.value.code == "IDENTITY_REVIEW_ROLE_REQUIRED"


@pytest.mark.asyncio
async def test_cross_tenant_and_inactive_affiliation_fail_closed():
    provider = _provider()
    with pytest.raises(IdentityReviewGateError) as caught:
        await authorize_identity_review(
            MagicMock(),
            token="token",
            patient_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            provider=provider,
            operation=IdentityReviewOperation.READ_CASE,
        )
    assert caught.value.code == "IDENTITY_REVIEW_ACCESS_DENIED"

    inactive = provider.model_copy(
        update={
            "affiliation": provider.affiliation.model_copy(
                update={
                    "valid_until": datetime.now(timezone.utc) - timedelta(seconds=1)
                }
            )
        }
    )
    with pytest.raises(IdentityReviewGateError) as caught:
        await authorize_identity_review(
            MagicMock(),
            token="token",
            patient_id=str(uuid.uuid4()),
            tenant_id=str(inactive.hospital_id),
            provider=inactive,
            operation=IdentityReviewOperation.READ_CASE,
        )
    assert caught.value.code == "IDENTITY_REVIEW_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_revoked_or_wrong_binding_capability_is_denied():
    provider = _provider()
    with patch(
        "app.core.identity_review_gate.validate_document_processing_access",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(IdentityReviewGateError) as caught:
            await authorize_identity_review(
                MagicMock(),
                token="revoked-token",
                patient_id=str(uuid.uuid4()),
                tenant_id=str(provider.hospital_id),
                provider=provider,
                operation=IdentityReviewOperation.SUBMIT_DISPOSITION,
            )
    assert caught.value.code == "IDENTITY_REVIEW_CONSENT_INACTIVE"


@pytest.mark.parametrize(
    ("erasure_error", "expected"),
    [
        (_PatientErasedSignal("patient"), "IDENTITY_REVIEW_ERASURE_ACCESS_BLOCKED"),
        (
            ErasureRegistryUnavailable("unavailable"),
            "IDENTITY_REVIEW_ERASURE_REGISTRY_UNAVAILABLE",
        ),
    ],
)
@pytest.mark.asyncio
async def test_erasure_and_registry_failure_are_distinct_fail_closed_denials(
    erasure_error, expected
):
    patient_id = uuid.uuid4()
    provider = _provider()
    with (
        patch(
            "app.core.identity_review_gate.validate_document_processing_access",
            new=AsyncMock(return_value=_capability(provider, patient_id)),
        ),
        patch(
            "app.core.identity_review_gate.check_erasure_registry",
            new=AsyncMock(side_effect=erasure_error),
        ),
    ):
        with pytest.raises(IdentityReviewGateError) as caught:
            await authorize_identity_review(
                MagicMock(),
                token="token",
                patient_id=str(patient_id),
                tenant_id=str(provider.hospital_id),
                provider=provider,
                operation=IdentityReviewOperation.SUBMIT_DISPOSITION,
            )
    assert caught.value.code == expected


def test_uploader_and_original_authorization_provider_cannot_self_review():
    provider = _provider()
    for uploader_id, original_provider_id in (
        (provider.actor_uid, str(uuid.uuid4())),
        (str(uuid.uuid4()), provider.actor_uid),
    ):
        with pytest.raises(IdentityReviewGateError) as caught:
            assert_identity_review_separation(
                provider=provider,
                original_uploader_id=uploader_id,
                original_authorization_provider_id=original_provider_id,
            )
        assert caught.value.code == "IDENTITY_REVIEW_SELF_REVIEW_FORBIDDEN"

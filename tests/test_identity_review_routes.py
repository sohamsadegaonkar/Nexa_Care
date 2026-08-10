from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider
from app.main import app
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from tests.conftest import DualModeTestClient


def _provider():
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Identity Reviewer",
            contact_email="reviewer@example.test",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="TEST",
            display_name="Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            roles=["identity_reviewer"],
            is_primary=True,
        ),
        session_binding="a" * 64,
    )


def _safe_case(case_id, provider):
    now = datetime.now(timezone.utc)
    return {
        "case_id": str(case_id),
        "job_id": str(uuid.uuid4()),
        "patient_id": str(uuid.uuid4()),
        "tenant_id": str(provider.hospital_id),
        "document_id": str(uuid.uuid4()),
        "status": "IN_REVIEW",
        "identity_reason_codes": ["DOCUMENT_IDENTITY_MISMATCH"],
        "is_assigned": True,
        "assigned_to_current_reviewer": True,
        "version": 2,
        "created_at": now,
        "claimed_at": now,
        "resolved_at": None,
        "route_count": 1,
        "contract_version": "identity-review/1.0",
        "policy_version": "identity-review/1.0",
    }


@pytest.fixture
def route_context():
    provider = _provider()
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    async def provider_override():
        return provider

    async def db_override():
        return db

    app.dependency_overrides[get_current_provider] = provider_override
    app.dependency_overrides[get_db_session] = db_override
    try:
        yield DualModeTestClient(app), provider, db
    finally:
        app.dependency_overrides.pop(get_current_provider, None)
        app.dependency_overrides.pop(get_db_session, None)


def test_hidden_ownership_identity_clinical_and_free_text_payloads_fail_closed(
    route_context,
):
    client, _, db = route_context
    job_id = uuid.uuid4()
    with patch(
        "app.api.v2.identity_review_routes.create_case", new=AsyncMock()
    ) as create:
        for forbidden in (
            "patient_id",
            "target_patient_id",
            "ocr_name",
            "aadhaar_abha_id",
            "diagnoses",
            "source_text",
            "notes",
        ):
            response = client.post(
                f"/api/v2/pipeline/jobs/{job_id}/identity-review-cases",
                headers={"X-Consent-Token": "token"},
                json={"idempotency_key": "create-route-0001", forbidden: "blocked"},
            )
            assert response.status_code == 422
            assert response.json() == {
                "detail": {"error_code": "IDENTITY_REVIEW_PAYLOAD_INVALID"}
            }
    create.assert_not_awaited()
    assert db.rollback.await_count == 7


def test_invalid_idempotency_key_uses_stable_value_free_error(route_context):
    client, _, _ = route_context
    response = client.post(
        f"/api/v2/pipeline/jobs/{uuid.uuid4()}/identity-review-cases",
        headers={"X-Consent-Token": "token"},
        json={"idempotency_key": "bad"},
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": {"error_code": "IDENTITY_REVIEW_IDEMPOTENCY_KEY_INVALID"}
    }

    response = client.post(
        f"/api/v2/pipeline/jobs/{uuid.uuid4()}/identity-review-cases",
        headers={"X-Consent-Token": "token"},
        json=["not", "an", "object"],
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": {"error_code": "IDENTITY_REVIEW_PAYLOAD_INVALID"}
    }


def test_all_endpoints_are_metadata_only_and_never_call_kms_or_storage(route_context):
    client, provider, db = route_context
    case_id = uuid.uuid4()
    job_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    row = SimpleNamespace(id=case_id)
    disposition = SimpleNamespace(
        id=uuid.uuid4(),
        case_id=case_id,
        outcome="INSUFFICIENT_IDENTITY_EVIDENCE",
        reason_codes=["IDENTITY_REVIEW_INCONCLUSIVE"],
        submitted_at=datetime.now(timezone.utc),
        contract_version="identity-review/1.0",
        policy_version="identity-review/1.0",
    )
    safe = _safe_case(case_id, provider)
    with (
        patch(
            "app.api.v2.identity_review_routes.create_case",
            new=AsyncMock(return_value=row),
        ),
        patch(
            "app.api.v2.identity_review_routes.list_cases",
            new=AsyncMock(return_value=[row]),
        ),
        patch(
            "app.api.v2.identity_review_routes.read_case",
            new=AsyncMock(return_value=row),
        ),
        patch(
            "app.api.v2.identity_review_routes.claim_case",
            new=AsyncMock(return_value=row),
        ),
        patch(
            "app.api.v2.identity_review_routes.recover_session",
            new=AsyncMock(return_value=row),
        ),
        patch(
            "app.api.v2.identity_review_routes.submit_disposition",
            new=AsyncMock(return_value=disposition),
        ),
        patch(
            "app.api.v2.identity_review_routes.case_metadata",
            new=AsyncMock(return_value=safe),
        ),
        patch("app.services.crypto_kms.get_encryption_provider") as kms,
        patch("app.services.document_storage.get_document_storage") as storage,
    ):
        responses = [
            client.post(
                f"/api/v2/pipeline/jobs/{job_id}/identity-review-cases",
                headers={"X-Consent-Token": "token"},
                json={"idempotency_key": "create-route-0002"},
            ),
            client.get(
                f"/api/v2/pipeline/patients/{patient_id}/identity-review-cases",
                headers={"X-Consent-Token": "token"},
            ),
            client.get(
                f"/api/v2/pipeline/identity-review-cases/{case_id}",
                headers={"X-Consent-Token": "token"},
            ),
            client.post(
                f"/api/v2/pipeline/identity-review-cases/{case_id}/claim",
                headers={"X-Consent-Token": "token"},
                json={"expected_version": 1, "idempotency_key": "claim-route-0001"},
            ),
            client.post(
                f"/api/v2/pipeline/identity-review-cases/{case_id}/recover-session",
                headers={"X-Consent-Token": "token"},
                json={
                    "expected_version": 2,
                    "idempotency_key": "recover-route-0001",
                },
            ),
            client.post(
                f"/api/v2/pipeline/identity-review-cases/{case_id}/dispositions",
                headers={"X-Consent-Token": "token"},
                json={
                    "expected_version": 3,
                    "idempotency_key": "disposition-route-0001",
                    "outcome": "INSUFFICIENT_IDENTITY_EVIDENCE",
                    "reason_codes": ["IDENTITY_REVIEW_INCONCLUSIVE"],
                },
            ),
        ]
    assert [response.status_code for response in responses] == [
        201,
        200,
        200,
        200,
        200,
        201,
    ]
    serialized = " ".join(response.text.lower() for response in responses)
    for forbidden in (
        "encrypted_raw_value",
        "encrypted_source_text",
        "source_text",
        "patient_name",
        "ocr_name",
        "aadhaar",
        "abha",
        "clinical_summary",
        "original_filename",
    ):
        assert forbidden not in serialized
    assert kms.call_count == 0
    assert storage.call_count == 0
    assert db.commit.await_count == 6


def test_audit_or_service_failure_rolls_back_and_returns_no_success(route_context):
    client, _, db = route_context
    with patch(
        "app.api.v2.identity_review_routes.create_case",
        new=AsyncMock(side_effect=RuntimeError("audit unavailable")),
    ):
        response = client.post(
            f"/api/v2/pipeline/jobs/{uuid.uuid4()}/identity-review-cases",
            headers={"X-Consent-Token": "token"},
            json={"idempotency_key": "audit-failure-0001"},
        )
    assert response.status_code == 500
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


def test_no_source_release_reassignment_or_delete_endpoints_exist(route_context):
    client, _, _ = route_context
    case_id = uuid.uuid4()
    for suffix in ("source", "release", "reassign"):
        response = client.post(
            f"/api/v2/pipeline/identity-review-cases/{case_id}/{suffix}",
            headers={"X-Consent-Token": "token"},
            json={},
        )
        assert response.status_code in {404, 405}
    response = client.delete(
        f"/api/v2/pipeline/identity-review-cases/{case_id}",
        headers={"X-Consent-Token": "token"},
    )
    assert response.status_code in {404, 405}

"""Security tests — T-05: Tampered API Payloads.

Verifies that the pipeline commit endpoint rejects:
- Fields with tampered status (needs_review claimed as approved)
- Fields missing required confidence metadata
- Fields with invalid risk_level values
- Fields with out-of-range confidence scores
- Field review with invalid action string

Also verifies that a patient signing a modified decision (tampered
payload) produces a signature that fails verification.

Threat model reference: docs/threat-model.md T-05
"""

from __future__ import annotations

import base64
import json
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.dependencies import get_current_provider, get_scoped_session
from app.main import app
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.models.provider import AffiliationType
from app.services.consent_engine import ConsentCapability
from tests.conftest import DualModeTestClient, FakeRedis, FakeSyncRedis


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Dr. Tamper",
            contact_email="tamper@hospital.example",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="TAM",
            display_name="Tamper Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician"],
        ),
    )


def _make_capability(patient_id: str) -> ConsentCapability:
    return ConsentCapability(
        patient_id=patient_id,
        clinician_id=str(uuid.uuid4()),
        purpose="TREATMENT",
        scope=["clinical.*", "pii.*"],
        is_break_glass=False,
        reason_code=None,
        issued_at=datetime.now(timezone.utc).isoformat(),
    )


def _mock_job(job_id, patient_id, status="scored"):
    m = MagicMock()
    m.id = uuid.UUID(job_id)
    m.patient_id = uuid.UUID(patient_id)
    m.status = status
    m.document_type = "LAB_REPORT"
    m.created_at = datetime.now(timezone.utc)
    return m


def _db_result(*, scalar_one_or_none=None, scalars_all=None, scalar=None):
    if scalars_all is not None:
        return MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=scalars_all))),
        )
    if scalar is not None:
        return MagicMock(scalar=MagicMock(return_value=scalar))
    return MagicMock(scalar_one_or_none=MagicMock(return_value=scalar_one_or_none))


def _side_effect_with_fallback(results):
    default = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        scalar_one_or_none=MagicMock(return_value=None),
        scalar=MagicMock(return_value=0),
    )
    results_iter = iter(results)

    def _next(*args, **kwargs):
        try:
            return next(results_iter)
        except StopIteration:
            return default

    return _next


def _reset_mock_db(mock_db):
    mock_db.execute.side_effect = None
    mock_db.execute.reset_mock()
    mock_db.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        scalar_one_or_none=MagicMock(return_value=None),
        scalar=MagicMock(return_value=0),
    )


def _consent_and_audit_patches(capability):
    stack = ExitStack()
    stack.enter_context(
        patch("app.core.consent_gate.validate_consent_capability",
              new_callable=AsyncMock, return_value=capability)
    )
    for mod in (
        "app.core.consent_gate",
        "app.api.v2.pipeline_routes",
        "app.observability.audit_ledger",
        "app.services.record_ingestion",
        "app.services.pipeline_orchestrator",
    ):
        stack.enter_context(patch(f"{mod}.append_audit_log_or_503", return_value=None))
    stack.enter_context(patch("app.observability.audit_ledger.append_audit_log", return_value=None))
    stack.enter_context(patch("app.api.v2.pipeline_routes.process_extraction_job", return_value=None))
    stack.enter_context(patch("app.services.pipeline_orchestrator.process_extraction_job", return_value=None))
    stack.enter_context(
        patch("app.api.v2.pipeline_routes.ingest_extracted_fields",
              new_callable=AsyncMock, return_value=None)
    )
    return stack


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    return DualModeTestClient(app)


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def fake_sync_redis(fake_redis):
    return FakeSyncRedis(fake_redis)


@pytest.fixture
def provider():
    return _make_provider_context()


@pytest.fixture
def patient_id():
    return str(uuid.uuid4())


@pytest.fixture
def overrides():
    saved = {}
    yield saved
    for dep in saved:
        app.dependency_overrides.pop(dep, None)


def _apply_auth_overrides(overrides, provider):
    async def _provider_dep():
        return provider

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep


# ── Pipeline commit tamper tests ─────────────────────────────────────────────


def test_commit_with_tampered_field_status(
    client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id,
):
    """T-05a: Commit rejects needs_review field even if payload claims approved.

    The DB still has the field as needs_review, so the unresolved-fields
    query catches it and returns 409.
    """
    job_id = str(uuid.uuid4())
    capability = _make_capability(patient_id)
    _apply_auth_overrides(overrides, provider)
    job = _mock_job(job_id, patient_id, status="review_required")

    needs_review_field = MagicMock()
    needs_review_field.id = uuid.uuid4()
    needs_review_field.job_id = uuid.UUID(job_id)
    needs_review_field.status = "needs_review"

    with _consent_and_audit_patches(capability):
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=job),
            _db_result(scalars_all=[needs_review_field]),  # DB says needs_review
        ])
        resp = client.post(
            f"/api/v2/pipeline/jobs/{job_id}/commit",
            json={
                "patient_id": patient_id,
                "fields": [{
                    "field_id": str(needs_review_field.id),
                    "field_name": "test",
                    "raw_value": "v", "normalized_value": "v",
                    "confidence": 0.9, "risk_level": "LOW_RISK",
                    "status": "approved",  # TAMPERED from needs_review
                }],
            },
            headers={"X-Consent-Token": "t"},
        )
        assert resp.status_code == 409, (
            f"Commit with tampered field status should return 409, got {resp.status_code}"
        )


def test_commit_with_missing_confidence(
    client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id,
):
    """T-05b: Commit rejects a field missing confidence metadata → 400."""
    job_id = str(uuid.uuid4())
    capability = _make_capability(patient_id)
    _apply_auth_overrides(overrides, provider)
    job = _mock_job(job_id, patient_id)

    with _consent_and_audit_patches(capability):
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=job),
            _db_result(scalars_all=[]),
        ])
        resp = client.post(
            f"/api/v2/pipeline/jobs/{job_id}/commit",
            json={
                "patient_id": patient_id,
                "fields": [{
                    "field_id": "f1", "field_name": "test",
                    "raw_value": "v", "normalized_value": "v",
                    "confidence": None, "risk_level": "LOW_RISK",
                    "status": "approved",
                }],
            },
            headers={"X-Consent-Token": "t"},
        )
        assert resp.status_code == 400, (
            f"Missing confidence should return 400, got {resp.status_code}"
        )


def test_commit_with_invalid_risk_level(
    client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id,
):
    """T-05c: Commit rejects a field with invalid risk_level → 400."""
    job_id = str(uuid.uuid4())
    capability = _make_capability(patient_id)
    _apply_auth_overrides(overrides, provider)
    job = _mock_job(job_id, patient_id)

    with _consent_and_audit_patches(capability):
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=job),
            _db_result(scalars_all=[]),
        ])
        resp = client.post(
            f"/api/v2/pipeline/jobs/{job_id}/commit",
            json={
                "patient_id": patient_id,
                "fields": [{
                    "field_id": "f1", "field_name": "test",
                    "raw_value": "v", "normalized_value": "v",
                    "confidence": 0.9, "risk_level": "NO_RISK",  # invalid
                    "status": "approved",
                }],
            },
            headers={"X-Consent-Token": "t"},
        )
        assert resp.status_code == 400, (
            f"Invalid risk_level should return 400, got {resp.status_code}"
        )


def test_commit_with_out_of_range_confidence(
    client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id,
):
    """T-05d: Commit rejects confidence > 1.0 → 400."""
    job_id = str(uuid.uuid4())
    capability = _make_capability(patient_id)
    _apply_auth_overrides(overrides, provider)
    job = _mock_job(job_id, patient_id)

    with _consent_and_audit_patches(capability):
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=job),
            _db_result(scalars_all=[]),
        ])
        resp = client.post(
            f"/api/v2/pipeline/jobs/{job_id}/commit",
            json={
                "patient_id": patient_id,
                "fields": [{
                    "field_id": "f1", "field_name": "test",
                    "raw_value": "v", "normalized_value": "v",
                    "confidence": 1.5, "risk_level": "LOW_RISK",  # out of range
                    "status": "approved",
                }],
            },
            headers={"X-Consent-Token": "t"},
        )
        assert resp.status_code == 400, (
            f"Out-of-range confidence should return 400, got {resp.status_code}"
        )


def test_field_review_invalid_action(
    client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id,
):
    """T-05e: Field review rejects invalid action string → 400."""
    field_id = str(uuid.uuid4())
    capability = _make_capability(patient_id)
    _apply_auth_overrides(overrides, provider)

    with _consent_and_audit_patches(capability):
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=MagicMock()),  # field found
            _db_result(scalar_one_or_none=MagicMock()),  # job found
        ])
        resp = client.post(
            f"/api/v2/pipeline/fields/{field_id}/review",
            json={"action": "delete"},  # invalid action
            headers={"X-Consent-Token": "t"},
        )
        assert resp.status_code == 400, (
            f"Invalid review action should return 400, got {resp.status_code}"
        )


# ── Tampered decision signature test ─────────────────────────────────────────


def test_tampered_decision_signature_rejected(
    client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id,
):
    """T-05f: Patient signs "approved" but attacker changes decision to "denied" → 401.

    The signing input includes the decision field. If an attacker modifies
    the decision after signing, the signature no longer matches the
    reconstructed signing input, so verification fails.
    """
    private_key, der_bytes, der_b64 = _generate_keypair()
    device_id = str(uuid.uuid4())
    provider_id = str(provider.provider.provider_id)

    async def _provider_dep():
        return provider

    async def _session_dep():
        return patient_id

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep
    overrides[get_scoped_session] = _session_dep
    app.dependency_overrides[get_scoped_session] = _session_dep

    with _patch_stack(fake_redis, fake_sync_redis):
        device_row = _mock_device_row(device_id, patient_id, der_bytes)

        # Request consent
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=device_row),
        ])
        req_resp = client.post(
            "/api/v2/consent/request",
            json={"patient_id": patient_id, "purpose": "checkup", "scope": "clinical", "access_duration_seconds": 900},
        )
        assert req_resp.status_code == 201
        request_id = req_resp.json()["request_id"]
        challenge_nonce = req_resp.json()["challenge_nonce"]

        # Patient signs "approved"
        challenge_raw = fake_sync_redis.get(f"consent_request:{request_id}")
        challenge_data = json.loads(challenge_raw)
        signing_input = _build_signing_input(
            request_id=request_id, patient_id=patient_id, provider_id=provider_id,
            challenge_nonce=challenge_nonce, decision="approved", scope="clinical",
            purpose="checkup", access_duration=challenge_data["access_duration"],
            expires_at=challenge_data["expires_at"],
        )
        real_sig = _sign(private_key, signing_input)

        # Attacker submits with decision="denied" but same signature
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=device_row),
            _db_result(scalars_all=[device_row]),
        ])
        resp = client.post(
            "/api/v2/consent/approve-signed",
            json={"request_id": request_id, "patient_id": patient_id,
                  "decision": "denied",  # TAMPERED from "approved"
                  "challenge_nonce": challenge_nonce,
                  "signature": real_sig, "device_id": device_id},
        )
        assert resp.status_code == 401, (
            f"Tampered decision signature should be rejected (401), got {resp.status_code}"
        )


# ── Inline crypto + patch helpers ────────────────────────────────────────────


def _generate_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    der_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    der_b64 = base64.b64encode(der_bytes).decode("ascii")
    return private_key, der_bytes, der_b64


def _sign(private_key, message: str) -> str:
    raw_sig = private_key.sign(message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(raw_sig).decode("ascii")


def _build_signing_input(**kw) -> str:
    return (
        f"{kw['request_id']}|{kw['patient_id']}|{kw['provider_id']}|"
        f"{kw['challenge_nonce']}|{kw['decision']}|{kw['scope']}|"
        f"{kw['purpose']}|{kw['access_duration']}|{kw['expires_at']}"
    )


def _mock_device_row(device_id, patient_id, der_bytes, status="active", revoked_at=None):
    row = MagicMock()
    row.id = uuid.UUID(device_id)
    row.patient_id = uuid.UUID(patient_id)
    row.device_public_key = der_bytes
    row.device_label = "Security Test Device"
    row.platform = "ios"
    row.status = status
    row.key_algorithm = "ECDSA-P256"
    row.enrolled_at = datetime.now(timezone.utc)
    row.revoked_at = revoked_at
    return row


def _patch_stack(fake_redis, fake_sync_redis):
    stack = ExitStack()
    stack.enter_context(patch("app.core.redis.get_redis_client", return_value=fake_sync_redis))
    stack.enter_context(patch("app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis))
    stack.enter_context(patch("app.services.consent_engine.get_consent_redis_client", return_value=fake_redis))
    stack.enter_context(patch("app.services.provider_auth_service.get_redis_client", return_value=fake_sync_redis))
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data={})
    stack.enter_context(patch("app.core.supabase.get_supabase_client", return_value=mock_supabase))
    stack.enter_context(patch("app.observability.audit_ledger.get_supabase_client", return_value=mock_supabase))
    stack.enter_context(patch("app.services.biometric_signature_verifier.get_supabase_client", return_value=mock_supabase))
    for mod in (
        "app.observability.audit_ledger",
        "app.core.consent_gate",
        "app.api.v2.consent_routes",
        "app.api.v2.device_routes",
        "app.services.consent_engine",
        "app.services.signed_approval_verifier",
    ):
        stack.enter_context(patch(f"{mod}.append_audit_log_or_503", return_value=None))
    stack.enter_context(patch("app.observability.audit_ledger.append_audit_log", return_value=None))
    stack.enter_context(patch("app.services.consent_engine.append_audit_log", return_value=None))
    stack.enter_context(patch("app.api.v2.consent_routes._break_glass_limiter", return_value=None))
    stack.enter_context(patch("app.api.v2.assurance_routes.push_service.send_approval_request", return_value=None))
    return stack

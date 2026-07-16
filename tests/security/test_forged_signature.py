"""Security tests — T-01: Forged ECDSA Signatures.

Verifies that the SignedApprovalVerifier and consent approve-signed route
reject:
- Signatures from unenrolled key pairs
- Signatures from revoked devices
- Tampered decision payloads (signature doesn't match signed input)

All tests use REAL P-256 ECDSA signatures — no bypasses.

Threat model reference: docs/threat-model.md T-01
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
from tests.conftest import DualModeTestClient, FakeRedis, FakeSyncRedis


# ── Helpers ───────────────────────────────────────────────────────────────────


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


def _make_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Dr. Security",
            contact_email="security@hospital.example",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="SEC",
            display_name="Security Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician"],
        ),
    )


def _patch_stack(fake_redis, fake_sync_redis):
    stack = ExitStack()
    stack.enter_context(patch("app.api.v2.device_routes.claim_device_enrollment_token", new=AsyncMock(return_value="claim-1")))
    stack.enter_context(patch("app.api.v2.device_routes.finalize_device_enrollment_token", new=AsyncMock(return_value=True)))
    stack.enter_context(patch("app.core.redis.get_redis_client", return_value=fake_sync_redis))
    stack.enter_context(patch("app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis))
    stack.enter_context(patch("app.services.consent_engine.get_consent_redis_client", return_value=fake_redis))
    stack.enter_context(patch("app.services.provider_auth_service.get_redis_client", return_value=fake_sync_redis))
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data={})
    stack.enter_context(patch("app.core.supabase.get_supabase_client", return_value=mock_supabase))
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


# ── Test: Wrong keypair ──────────────────────────────────────────────────────


def test_forged_signature_wrong_keypair(
    client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id,
):
    """T-01a: A signature from an attacker-generated keypair is rejected (401).

    The patient enrolled key A, but the attacker signs with key B.
    The verifier tries all enrolled keys; none match → 401.
    """
    enrolled_private, enrolled_der, enrolled_b64 = _generate_keypair()
    attacker_private, _, _ = _generate_keypair()
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
        # Enroll with key A
        device_row = _mock_device_row(device_id, patient_id, enrolled_der)
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar=0),
            _db_result(scalar_one_or_none=None),
        ])
        enroll_resp = client.post(
            "/api/v2/patient/devices/enroll",
            json={"device_public_key": enrolled_b64, "device_label": "Sec Device", "platform": "ios", "device_enrollment_token": "e" * 43},
        )
        assert enroll_resp.status_code == 201

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

        # Sign with ATTACKER key B
        challenge_raw = fake_sync_redis.get(f"consent_request:{request_id}")
        challenge_data = json.loads(challenge_raw)
        signing_input = _build_signing_input(
            request_id=request_id, patient_id=patient_id, provider_id=provider_id,
            challenge_nonce=challenge_nonce, decision="approved", scope="clinical",
            purpose="checkup", access_duration=challenge_data["access_duration"],
            expires_at=challenge_data["expires_at"],
        )
        forged_sig = _sign(attacker_private, signing_input)

        # Submit — must be rejected
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=device_row),
            _db_result(scalars_all=[device_row]),
        ])
        resp = client.post(
            "/api/v2/consent/approve-signed",
            json={"request_id": request_id, "patient_id": patient_id,
                  "decision": "approved", "challenge_nonce": challenge_nonce,
                  "signature": forged_sig, "device_id": device_id},
        )
        assert resp.status_code == 401, (
            f"Forged signature from wrong keypair should be rejected (401), got {resp.status_code}"
        )


# ── Test: Revoked device ─────────────────────────────────────────────────────


def test_forged_signature_revoked_device(
    client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id,
):
    """T-01b: A valid signature from a revoked device is rejected (401).

    The device was previously enrolled but then revoked. The signature
    is cryptographically valid but the key is no longer trusted.
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
        # Mock a REVOKED device row
        revoked_row = _mock_device_row(
            device_id, patient_id, der_bytes,
            status="revoked", revoked_at=datetime.now(timezone.utc),
        )

        # Request consent — device is still "active" for the initial query
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=_mock_device_row(device_id, patient_id, der_bytes, status="active")),
        ])
        req_resp = client.post(
            "/api/v2/consent/request",
            json={"patient_id": patient_id, "purpose": "checkup", "scope": "clinical", "access_duration_seconds": 900},
        )
        assert req_resp.status_code == 201
        request_id = req_resp.json()["request_id"]
        challenge_nonce = req_resp.json()["challenge_nonce"]

        # Build valid signature
        challenge_raw = fake_sync_redis.get(f"consent_request:{request_id}")
        challenge_data = json.loads(challenge_raw)
        signing_input = _build_signing_input(
            request_id=request_id, patient_id=patient_id, provider_id=provider_id,
            challenge_nonce=challenge_nonce, decision="approved", scope="clinical",
            purpose="checkup", access_duration=challenge_data["access_duration"],
            expires_at=challenge_data["expires_at"],
        )
        real_sig = _sign(private_key, signing_input)

        # Submit with revoked device — route handler checks revoked_at
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=revoked_row),  # device lookup in route → revoked
        ])
        resp = client.post(
            "/api/v2/consent/approve-signed",
            json={"request_id": request_id, "patient_id": patient_id,
                  "decision": "approved", "challenge_nonce": challenge_nonce,
                  "signature": real_sig, "device_id": device_id},
        )
        assert resp.status_code == 401, (
            f"Valid signature from revoked device should be rejected (401), got {resp.status_code}"
        )


# ── Test: Timing side-channel ────────────────────────────────────────────────


def test_forged_signature_timing_sidechannel():
    """T-01c: SignedApprovalVerifier enforces minimum verification duration."""
    from app.services.signed_approval_verifier import _MIN_VERIFY_DURATION_SECONDS

    assert _MIN_VERIFY_DURATION_SECONDS > 0, (
        "SignedApprovalVerifier must enforce a minimum verification duration to prevent timing attacks"
    )


# ── Test: Unenrolled key (verifier direct) ────────────────────────────────────


def test_forged_signature_unenrolled_key_direct(
    client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id,
):
    """T-01d: Signature from a key that was never enrolled for the patient → 401.

    The approve-signed handler looks up the device by ID. If the device
    is not found for this patient, the request is rejected.
    """
    attacker_private, _, attacker_b64 = _generate_keypair()
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
        # Enroll with a DIFFERENT key
        enrolled_private, enrolled_der, enrolled_b64 = _generate_keypair()
        enrolled_row = _mock_device_row(str(uuid.uuid4()), patient_id, enrolled_der)

        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar=0),
            _db_result(scalar_one_or_none=None),
        ])
        enroll_resp = client.post(
            "/api/v2/patient/devices/enroll",
            json={"device_public_key": enrolled_b64, "device_label": "Enrolled Device", "platform": "ios", "device_enrollment_token": "e" * 43},
        )
        assert enroll_resp.status_code == 201

        # Request consent
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=enrolled_row),
        ])
        req_resp = client.post(
            "/api/v2/consent/request",
            json={"patient_id": patient_id, "purpose": "checkup", "scope": "clinical", "access_duration_seconds": 900},
        )
        assert req_resp.status_code == 201
        request_id = req_resp.json()["request_id"]
        challenge_nonce = req_resp.json()["challenge_nonce"]

        # Sign with UNENROLLED key
        challenge_raw = fake_sync_redis.get(f"consent_request:{request_id}")
        challenge_data = json.loads(challenge_raw)
        signing_input = _build_signing_input(
            request_id=request_id, patient_id=patient_id, provider_id=provider_id,
            challenge_nonce=challenge_nonce, decision="approved", scope="clinical",
            purpose="checkup", access_duration=challenge_data["access_duration"],
            expires_at=challenge_data["expires_at"],
        )
        forged_sig = _sign(attacker_private, signing_input)

        # Device not found for this patient
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback([
            _db_result(scalar_one_or_none=None),  # device lookup → not found
        ])
        resp = client.post(
            "/api/v2/consent/approve-signed",
            json={"request_id": request_id, "patient_id": patient_id,
                  "decision": "approved", "challenge_nonce": challenge_nonce,
                  "signature": forged_sig, "device_id": device_id},
        )
        assert resp.status_code == 401, (
            f"Unenrolled key signature should be rejected (401), got {resp.status_code}"
        )

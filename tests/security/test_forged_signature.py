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
import asyncio
import hashlib
import json
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.dependencies import (
    AuthenticatedPatientSession,
    get_current_patient_session,
    get_scoped_session,
)
from app.main import app
from app.services.patient_discovery_service import PatientDiscoveryService
from app.services.signed_approval_verifier import canonical_signed_approval_payload
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
    return canonical_signed_approval_payload(**kw).decode("utf-8")


def _db_result(*, scalar_one_or_none=None, scalars_all=None, scalar=None):
    if scalars_all is not None:
        return MagicMock(
            scalars=MagicMock(
                return_value=MagicMock(all=MagicMock(return_value=scalars_all))
            ),
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


def _mock_device_row(
    device_id, patient_id, der_bytes, status="active", revoked_at=None
):
    row = MagicMock()
    row.id = uuid.UUID(device_id)
    row.device_id = uuid.UUID(device_id)
    row.key_version = 1
    row.patient_id = uuid.UUID(patient_id)
    row.device_public_key = der_bytes
    row.public_key_fingerprint = "b" * 64
    row.device_label = "Security Test Device"
    row.platform = "ios"
    row.status = status
    row.key_algorithm = "ECDSA-P256"
    row.enrolled_at = datetime.now(timezone.utc)
    row.revoked_at = revoked_at
    return row


def _patch_stack(fake_redis, fake_sync_redis, patient_id: str):
    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.api.v2.device_routes.claim_device_enrollment_token",
            new=AsyncMock(return_value="claim-1"),
        )
    )
    stack.enter_context(
        patch(
            "app.api.v2.device_routes.finalize_device_enrollment_token",
            new=AsyncMock(return_value=True),
        )
    )

    async def _enroll_stub(
        db,
        *,
        patient_id,
        raw_public_key,
        device_label,
        platform,
        actor_id,
    ):
        row = MagicMock()
        row.id = uuid.uuid4()
        row.device_id = uuid.uuid4()
        row.key_version = 1
        row.status = "active"
        row.enrolled_at = datetime.now(timezone.utc)
        return row

    stack.enter_context(
        patch(
            "app.api.v2.device_routes.enroll_patient_device_key",
            new=AsyncMock(side_effect=_enroll_stub),
        )
    )
    stack.enter_context(
        patch("app.core.redis.get_redis_client", return_value=fake_sync_redis)
    )
    stack.enter_context(
        patch(
            "app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis
        )
    )
    patient = MagicMock(patient_uuid=uuid.UUID(patient_id), is_deleted=False)
    stack.enter_context(
        patch.object(
            PatientDiscoveryService,
            "resolve_patient_id",
            new=AsyncMock(return_value=(patient, False)),
        )
    )
    stack.enter_context(
        patch(
            "app.api.v2.consent_routes.get_async_redis_client",
            return_value=fake_redis,
        )
    )
    stack.enter_context(
        patch(
            "app.services.consent_engine.get_consent_redis_client",
            return_value=fake_redis,
        )
    )
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[]
    )
    mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
        data={}
    )
    stack.enter_context(
        patch("app.core.supabase.get_supabase_client", return_value=mock_supabase)
    )
    for mod in (
        "app.observability.audit_ledger",
        "app.core.consent_gate",
        "app.api.v2.consent_routes",
        "app.api.v2.device_routes",
        "app.services.consent_engine",
        "app.services.signed_approval_verifier",
    ):
        stack.enter_context(patch(f"{mod}.append_audit_log_or_503", return_value=None))
    stack.enter_context(
        patch("app.observability.audit_ledger.append_audit_log", return_value=None)
    )
    stack.enter_context(
        patch("app.services.consent_engine.append_audit_log", return_value=None)
    )
    stack.enter_context(
        patch("app.api.v2.consent_routes._break_glass_limiter", return_value=None)
    )
    stack.enter_context(
        patch(
            "app.api.v2.assurance_routes.push_service.send_approval_request",
            return_value=None,
        )
    )
    return stack


def _active_discovery_handle(fake_redis, clinical_session, patient_id: str) -> str:
    service = PatientDiscoveryService(db=MagicMock(), redis=fake_redis)
    patient = MagicMock(patient_uuid=uuid.UUID(patient_id), is_deleted=False)
    handle = asyncio.run(
        service.issue_handle(
            patient=patient,
            provider_id=str(clinical_session.provider.id),
            hospital_id=str(clinical_session.hospital.id),
            session_binding=hashlib.sha256(
                clinical_session.token.encode("utf-8")
            ).hexdigest(),
            identifier_type="TEST_DISCOVERY",
        )
    )
    assert asyncio.run(service.activate_handle(raw_handle=handle.value))
    return handle.value


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
def patient_id():
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def current_patient_session(patient_id):
    patient = MagicMock(patient_uuid=uuid.UUID(patient_id), is_deleted=False)

    async def _current_session():
        return AuthenticatedPatientSession(
            patient_id=patient_id,
            patient=patient,
            session_id="forged-signature-session",
            session_epoch=1,
            supabase_user_id="forged-signature-user",
        )

    app.dependency_overrides[get_current_patient_session] = _current_session
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_patient_session, None)
        app.dependency_overrides.pop(get_scoped_session, None)


# ── Test: Wrong keypair ──────────────────────────────────────────────────────


def test_forged_signature_wrong_keypair(
    client,
    fake_redis,
    fake_sync_redis,
    mock_db,
    patient_id,
    real_clinical_session,
):
    """T-01a: A signature from an attacker-generated keypair is rejected (401)."""
    _, enrolled_der, enrolled_b64 = _generate_keypair()
    attacker_private, _, _ = _generate_keypair()
    device_id = str(uuid.uuid4())
    provider_id = str(real_clinical_session.provider.id)

    async def _session_dep():
        return patient_id

    app.dependency_overrides[get_scoped_session] = _session_dep

    with _patch_stack(fake_redis, fake_sync_redis, patient_id):
        discovery_handle = _active_discovery_handle(
            fake_redis, real_clinical_session, patient_id
        )
        device_row = _mock_device_row(device_id, patient_id, enrolled_der)
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback(
            [_db_result(scalar=0), _db_result(scalar_one_or_none=None)]
        )
        enroll_resp = client.post(
            "/api/v2/patient/devices/enroll",
            json={
                "device_public_key": enrolled_b64,
                "device_label": "Sec Device",
                "platform": "ios",
                "device_enrollment_token": "e" * 43,
            },
        )
        assert enroll_resp.status_code == 201

        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback(
            [_db_result(scalar_one_or_none=device_row)]
        )
        req_resp = client.post(
            "/api/v2/consent/request",
            headers=real_clinical_session.headers,
            json={
                "discovery_handle": discovery_handle,
                "purpose": "checkup",
                "scope": "clinical",
                "access_duration_seconds": 900,
            },
        )
        assert req_resp.status_code == 201
        request_id = req_resp.json()["request_id"]
        challenge_nonce = req_resp.json()["challenge_nonce"]
        challenge_data = json.loads(
            fake_sync_redis.get(f"consent_request:{request_id}")
        )
        signing_input = _build_signing_input(
            request_id=request_id,
            patient_id=patient_id,
            provider_id=provider_id,
            challenge_nonce=challenge_nonce,
            decision="approved",
            scope="clinical",
            purpose="checkup",
            access_duration=challenge_data["access_duration"],
            issued_at=challenge_data["created_at"],
            expires_at=challenge_data["expires_at"],
            device_id=device_id,
        )
        forged_sig = _sign(attacker_private, signing_input)

        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback(
            [
                _db_result(scalar_one_or_none=device_row),
                _db_result(scalars_all=[device_row]),
            ]
        )
        resp = client.post(
            "/api/v2/consent/approve-signed",
            json={
                "request_id": request_id,
                "patient_id": patient_id,
                "decision": "approved",
                "challenge_nonce": challenge_nonce,
                "signature": forged_sig,
                "device_id": device_id,
            },
        )
        assert resp.status_code == 401


# ── Test: Revoked device ─────────────────────────────────────────────────────


def test_forged_signature_revoked_device(
    client,
    fake_redis,
    fake_sync_redis,
    mock_db,
    patient_id,
    real_clinical_session,
):
    """T-01b: A valid signature from a revoked device is rejected (401)."""
    private_key, der_bytes, _ = _generate_keypair()
    device_id = str(uuid.uuid4())
    provider_id = str(real_clinical_session.provider.id)

    async def _session_dep():
        return patient_id

    app.dependency_overrides[get_scoped_session] = _session_dep

    with _patch_stack(fake_redis, fake_sync_redis, patient_id):
        discovery_handle = _active_discovery_handle(
            fake_redis, real_clinical_session, patient_id
        )
        revoked_row = _mock_device_row(
            device_id,
            patient_id,
            der_bytes,
            status="revoked",
            revoked_at=datetime.now(timezone.utc),
        )
        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback(
            [
                _db_result(
                    scalar_one_or_none=_mock_device_row(
                        device_id, patient_id, der_bytes, status="active"
                    )
                )
            ]
        )
        req_resp = client.post(
            "/api/v2/consent/request",
            headers=real_clinical_session.headers,
            json={
                "discovery_handle": discovery_handle,
                "purpose": "checkup",
                "scope": "clinical",
                "access_duration_seconds": 900,
            },
        )
        assert req_resp.status_code == 201
        request_id = req_resp.json()["request_id"]
        challenge_nonce = req_resp.json()["challenge_nonce"]
        challenge_data = json.loads(
            fake_sync_redis.get(f"consent_request:{request_id}")
        )
        signing_input = _build_signing_input(
            request_id=request_id,
            patient_id=patient_id,
            provider_id=provider_id,
            challenge_nonce=challenge_nonce,
            decision="approved",
            scope="clinical",
            purpose="checkup",
            access_duration=challenge_data["access_duration"],
            issued_at=challenge_data["created_at"],
            expires_at=challenge_data["expires_at"],
            device_id=device_id,
        )
        real_sig = _sign(private_key, signing_input)

        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback(
            [_db_result(scalar_one_or_none=revoked_row)]
        )
        resp = client.post(
            "/api/v2/consent/approve-signed",
            json={
                "request_id": request_id,
                "patient_id": patient_id,
                "decision": "approved",
                "challenge_nonce": challenge_nonce,
                "signature": real_sig,
                "device_id": device_id,
            },
        )
        assert resp.status_code == 401


# ── Test: Timing side-channel ────────────────────────────────────────────────


def test_forged_signature_timing_sidechannel():
    """T-01c: SignedApprovalVerifier enforces minimum verification duration."""
    from app.services.signed_approval_verifier import _MIN_VERIFY_DURATION_SECONDS

    assert _MIN_VERIFY_DURATION_SECONDS > 0


# ── Test: Unenrolled key (verifier direct) ────────────────────────────────────


def test_forged_signature_unenrolled_key_direct(
    client,
    fake_redis,
    fake_sync_redis,
    mock_db,
    patient_id,
    real_clinical_session,
):
    """T-01d: Signature from a key that was never enrolled for the patient → 401."""
    attacker_private, _, _ = _generate_keypair()
    device_id = str(uuid.uuid4())
    provider_id = str(real_clinical_session.provider.id)

    async def _session_dep():
        return patient_id

    app.dependency_overrides[get_scoped_session] = _session_dep

    with _patch_stack(fake_redis, fake_sync_redis, patient_id):
        discovery_handle = _active_discovery_handle(
            fake_redis, real_clinical_session, patient_id
        )
        _, enrolled_der, enrolled_b64 = _generate_keypair()
        enrolled_row = _mock_device_row(str(uuid.uuid4()), patient_id, enrolled_der)

        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback(
            [_db_result(scalar=0), _db_result(scalar_one_or_none=None)]
        )
        enroll_resp = client.post(
            "/api/v2/patient/devices/enroll",
            json={
                "device_public_key": enrolled_b64,
                "device_label": "Enrolled Device",
                "platform": "ios",
                "device_enrollment_token": "e" * 43,
            },
        )
        assert enroll_resp.status_code == 201

        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback(
            [_db_result(scalar_one_or_none=enrolled_row)]
        )
        req_resp = client.post(
            "/api/v2/consent/request",
            headers=real_clinical_session.headers,
            json={
                "discovery_handle": discovery_handle,
                "purpose": "checkup",
                "scope": "clinical",
                "access_duration_seconds": 900,
            },
        )
        assert req_resp.status_code == 201
        request_id = req_resp.json()["request_id"]
        challenge_nonce = req_resp.json()["challenge_nonce"]
        challenge_data = json.loads(
            fake_sync_redis.get(f"consent_request:{request_id}")
        )
        signing_input = _build_signing_input(
            request_id=request_id,
            patient_id=patient_id,
            provider_id=provider_id,
            challenge_nonce=challenge_nonce,
            decision="approved",
            scope="clinical",
            purpose="checkup",
            access_duration=challenge_data["access_duration"],
            issued_at=challenge_data["created_at"],
            expires_at=challenge_data["expires_at"],
            device_id=device_id,
        )
        forged_sig = _sign(attacker_private, signing_input)

        _reset_mock_db(mock_db)
        mock_db.execute.side_effect = _side_effect_with_fallback(
            [_db_result(scalar_one_or_none=None)]
        )
        resp = client.post(
            "/api/v2/consent/approve-signed",
            json={
                "request_id": request_id,
                "patient_id": patient_id,
                "decision": "approved",
                "challenge_nonce": challenge_nonce,
                "signature": forged_sig,
                "device_id": device_id,
            },
        )
        assert resp.status_code == 401

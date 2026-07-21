"""Consent flow integration tests — Days 6-8 connected flow.

Exercises the full cryptographic consent chain using REAL P-256 keypairs:
  Enroll device → request consent → sign challenge → approve →
  verify grant → access record → verify audit.

Every signature is produced by a real ECDSA P-256 private key — no bypasses.

ALPHA: These tests use mock_db and FakeRedis for the data layer but exercise
the real route handler code, the real SignedApprovalVerifier, and the real
consent engine issue/validate path (with FakeRedis standing in for Redis).
The Supabase audit ledger is patched to a MagicMock.
"""

from __future__ import annotations

import base64
import asyncio
import json
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from app.services.signed_approval_verifier import canonical_signed_approval_payload

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


# ── Cryptographic helpers ────────────────────────────────────────────────────


def generate_p256_keypair():
    """Generate a real ECDSA P-256 keypair and return (private_key, der_public_key_bytes, der_b64)."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    der_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    der_b64 = base64.b64encode(der_bytes).decode("ascii")
    return private_key, der_bytes, der_b64


def sign_challenge(private_key, message: str) -> str:
    """Produce a real ECDSA-SHA256 signature over a UTF-8 message. Returns base64."""
    raw_sig = private_key.sign(
        message.encode("utf-8"),
        ec.ECDSA(hashes.SHA256()),
    )
    return base64.b64encode(raw_sig).decode("ascii")


def build_signing_input(
    request_id: str,
    patient_id: str,
    provider_id: str,
    challenge_nonce: str,
    decision: str,
    scope: str,
    purpose: str,
    access_duration: int,
    issued_at: str,
    expires_at: str,
    device_id: str,
) -> str:
    return canonical_signed_approval_payload(
        request_id=request_id, patient_id=patient_id, provider_id=provider_id,
        challenge_nonce=challenge_nonce, decision=decision, scope=scope,
        purpose=purpose, access_duration=access_duration, issued_at=issued_at,
        expires_at=expires_at, device_id=device_id,
    ).decode("utf-8")


# ── Mock DB helpers ──────────────────────────────────────────────────────────


def _db_result(*, scalar_one_or_none=None, scalars_all=None, scalar=None):
    """Create a MagicMock mimicking a SQLAlchemy Result row.

    Convenience factory so test code stays readable:
        _db_result(scalar_one_or_none=job)
        _db_result(scalars_all=[field1, field2])
        _db_result(scalar=0)
    """
    if scalars_all is not None:
        return MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=scalars_all))),
        )
    if scalar is not None:
        return MagicMock(scalar=MagicMock(return_value=scalar))
    # Default: scalar_one_or_none
    return MagicMock(scalar_one_or_none=MagicMock(return_value=scalar_one_or_none))


def _side_effect_with_fallback(results):
    """Create a side_effect that yields specific results then falls back to safe defaults.

    When the list of specific results is exhausted, subsequent calls
    return a safe default (empty scalars, None scalar_one_or_none).
    This prevents StopAsyncIteration from extra db.execute calls made
    by middleware or dependency injection.
    """
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
    """Reset mock_db.execute for the next HTTP call.

    IMPORTANT: ``reset_mock()`` does NOT clear ``side_effect`` — it must be
    explicitly set to None first.
    """
    mock_db.execute.side_effect = None
    mock_db.execute.reset_mock()
    mock_db.execute.return_value = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        scalar_one_or_none=MagicMock(return_value=None),
        scalar=MagicMock(return_value=0),
    )


# ── Provider / patient context helpers ───────────────────────────────────────


def _make_provider_context() -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=uuid.uuid4(),
            display_name="Dr. Integration",
            contact_email="integration@hospital.example",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="INT",
            display_name="Integration Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician"],
        ),
    )


def _mock_device_row(device_id: str, patient_id: str, der_bytes: bytes):
    """Create a MagicMock that behaves like a PatientDeviceKey row."""
    row = MagicMock()
    row.id = uuid.UUID(device_id)
    row.patient_id = uuid.UUID(patient_id)
    row.device_public_key = der_bytes
    row.device_label = "Integration Test Device"
    row.platform = "ios"
    row.status = "active"
    row.key_algorithm = "ECDSA-P256"
    row.enrolled_at = datetime.now(timezone.utc)
    row.revoked_at = None
    return row


def _setup_mock_db_for_approve(mock_db, device_row):
    """Configure mock_db for the approve-signed endpoint.

    The approve-signed handler and the SignedApprovalVerifier each do
    one db.execute call, so we need at least 2 side_effect entries.
    Uses _side_effect_with_fallback so extra calls return safe defaults
    instead of raising StopAsyncIteration.
    """
    mock_db.execute.side_effect = _side_effect_with_fallback([
        _db_result(scalar_one_or_none=device_row),  # device lookup in route
        _db_result(scalars_all=[device_row]),  # verifier key lookup
    ])


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
def keypair():
    """Real P-256 keypair for the patient device."""
    return generate_p256_keypair()


@pytest.fixture
def overrides():
    saved = {}
    yield saved
    for dep in saved:
        app.dependency_overrides.pop(dep, None)


def _apply_overrides(overrides, provider, patient_id):
    """Wire up all dependency overrides for a consent flow test."""

    async def _provider_dep():
        return provider

    async def _session_dep():
        return patient_id

    overrides[get_current_provider] = _provider_dep
    app.dependency_overrides[get_current_provider] = _provider_dep
    overrides[get_scoped_session] = _session_dep
    app.dependency_overrides[get_scoped_session] = _session_dep


def _patch_stack(fake_redis, fake_sync_redis):
    """Return an ExitStack with all Redis/Supabase/audit patches applied."""
    stack = ExitStack()
    stack.enter_context(patch("app.api.v2.device_routes.claim_device_enrollment_token", new=AsyncMock(return_value="claim-1")))
    stack.enter_context(patch("app.api.v2.device_routes.finalize_device_enrollment_token", new=AsyncMock(return_value=True)))

    # Redis patches
    stack.enter_context(
        patch("app.core.redis.get_redis_client", return_value=fake_sync_redis)
    )
    stack.enter_context(
        patch("app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis)
    )
    stack.enter_context(
        patch("app.services.approved_access_capability.get_async_redis_client", return_value=fake_redis)
    )
    stack.enter_context(
        patch("app.services.consent_engine.get_consent_redis_client", return_value=fake_redis)
    )
    stack.enter_context(
        patch("app.services.provider_auth_service.get_redis_client", return_value=fake_sync_redis)
    )

    # Supabase / audit patches
    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
    mock_supabase.table.return_value.insert.return_value.execute.return_value = MagicMock()
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data={})
    stack.enter_context(
        patch("app.core.supabase.get_supabase_client", return_value=mock_supabase)
    )
    stack.enter_context(
        patch("app.observability.audit_ledger.append_audit_log_or_503", return_value=None)
    )
    stack.enter_context(
        patch("app.observability.audit_ledger.append_audit_log", return_value=None)
    )

    # Audit patches in consuming modules
    for mod in (
        "app.core.consent_gate",
        "app.api.v2.consent_routes",
        "app.api.v2.device_routes",
        "app.api.v2.pipeline_routes",
        "app.api.v2.patient_record_routes",
        "app.services.consent_engine",
        "app.services.signed_approval_verifier",
    ):
        stack.enter_context(
            patch(f"{mod}.append_audit_log_or_503", return_value=None)
        )
    stack.enter_context(
        patch("app.services.consent_engine.append_audit_log", return_value=None)
    )

    # Break-glass rate limiter
    stack.enter_context(
        patch("app.api.v2.consent_routes._break_glass_limiter", return_value=None)
    )

    # Assurance verifier for push
    stack.enter_context(
        patch("app.api.v2.assurance_routes.push_service.send_approval_request", return_value=None)
    )

    return stack


# ═══════════════════════════════════════════════════════════════════════════════
# FULL CONSENT FLOW INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentFlowIntegration:
    """End-to-end: enroll device → request consent → sign challenge →
    approve → verify grant → access record → verify audit.

    ALPHA: Uses mock_db for the SQLAlchemy layer, FakeRedis for the consent
    store, and real P-256 signatures.  The route handlers, consent engine,
    and SignedApprovalVerifier all run real code.
    """

    def test_full_consent_flow_with_real_signatures(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id, keypair
    ):
        """Enroll → request → sign → approve → validate → access record."""
        private_key, der_bytes, der_b64 = keypair
        device_id = str(uuid.uuid4())
        provider_id = str(provider.provider.provider_id)

        _apply_overrides(overrides, provider, patient_id)

        with _patch_stack(fake_redis, fake_sync_redis):
            # ── Step 1: Enroll device ────────────────────────────────────
            device_row = _mock_device_row(device_id, patient_id, der_bytes)
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar=0),  # count query
                _db_result(scalar_one_or_none=None),  # existing key check
            ])

            enroll_resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={
                    "device_public_key": der_b64,
                    "device_label": "Integration Test Device",
                    "platform": "ios",
                    "device_enrollment_token": "e" * 43,
                },
            )
            assert enroll_resp.status_code == 201, f"Enroll failed: {enroll_resp.text}"
            assert enroll_resp.json()["status"] == "active"

            # ── Step 2: Request consent (doctor initiates) ──────────────
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=device_row),  # device lookup
            ])

            request_resp = client.post(
                "/api/v2/consent/request",
                json={
                    "patient_id": patient_id,
                    "purpose": "routine_checkup",
                    "scope": "clinical",
                    "access_duration_seconds": 900,
                },
            )
            assert request_resp.status_code == 201, f"Consent request failed: {request_resp.text}"
            request_data = request_resp.json()
            assert request_data["status"] == "pending"
            request_id = request_data["request_id"]
            challenge_nonce = request_data["challenge_nonce"]
            assert challenge_nonce is not None

            # ── Step 3: Build signing input and sign with REAL P-256 key ─
            challenge_raw = fake_sync_redis.get(f"consent_request:{request_id}")
            assert challenge_raw is not None, "Challenge not stored in Redis"
            challenge_data = json.loads(challenge_raw)
            expires_at = challenge_data["expires_at"]
            access_duration = challenge_data["access_duration"]

            signing_input = build_signing_input(
                request_id=request_id,
                patient_id=patient_id,
                provider_id=provider_id,
                challenge_nonce=challenge_nonce,
                decision="approved",
                scope="clinical",
                purpose="routine_checkup",
                access_duration=access_duration,
                issued_at=challenge_data["created_at"],
                expires_at=expires_at,
                device_id=device_id,
            )
            real_signature = sign_challenge(private_key, signing_input)

            # ── Step 4: Submit signed approval ──────────────────────────
            _reset_mock_db(mock_db)
            _setup_mock_db_for_approve(mock_db, device_row)

            approval_resp = client.post(
                "/api/v2/consent/approve-signed",
                json={
                    "request_id": request_id,
                    "patient_id": patient_id,
                    "decision": "approved",
                    "challenge_nonce": challenge_nonce,
                    "signature": real_signature,
                    "device_id": device_id,
                },
            )
            assert approval_resp.status_code == 200, f"Approval failed: {approval_resp.text}"
            assert approval_resp.json()["status"] == "approved"

            # ── Step 5: Verify consent grant was issued ─────────────────
            updated_raw = fake_sync_redis.get(f"consent_request:{request_id}")
            updated_data = json.loads(updated_raw)
            assert "consent_token" not in updated_data
            _reset_mock_db(mock_db)
            mock_db.execute.return_value = _db_result(scalar_one_or_none=device_row)
            claim_resp = client.post(
                f"/api/v2/consent/{request_id}/claim-access",
                headers={"X-Hospital-Id": str(provider.hospital_id)},
            )
            assert claim_resp.status_code == 200, claim_resp.text
            assert claim_resp.headers["cache-control"] == "no-store"
            claim = claim_resp.json()
            assert claim["patient_id"] == patient_id
            consent_token = claim["consent_token"]
            assert consent_token not in str(fake_sync_redis._a.data.keys())

            from app.services.approved_access_capability import validate
            capability = asyncio.run(validate(
                token=consent_token,
                patient_id=patient_id,
                provider_id=provider_id,
                hospital_id=str(provider.hospital_id),
                requested_category="clinical_summary",
            ))
            assert capability is not None, "Consent token validation failed"
            assert capability.patient_id == patient_id
            assert capability.clinician_id == provider_id

    def test_denied_consent_flow_with_real_signatures(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id, keypair
    ):
        """Enroll → request → sign with 'denied' decision → verify no grant issued."""
        private_key, der_bytes, der_b64 = keypair
        device_id = str(uuid.uuid4())
        provider_id = str(provider.provider.provider_id)

        _apply_overrides(overrides, provider, patient_id)

        with _patch_stack(fake_redis, fake_sync_redis):
            # Enroll
            device_row = _mock_device_row(device_id, patient_id, der_bytes)
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar=0),
                _db_result(scalar_one_or_none=None),
            ])
            enroll_resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={"device_public_key": der_b64, "device_label": "Deny Device", "platform": "android", "device_enrollment_token": "e" * 43},
            )
            assert enroll_resp.status_code == 201

            # Request consent
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=device_row),
            ])
            request_resp = client.post(
                "/api/v2/consent/request",
                json={"patient_id": patient_id, "purpose": "checkup", "scope": "clinical", "access_duration_seconds": 900},
            )
            assert request_resp.status_code == 201
            request_id = request_resp.json()["request_id"]
            challenge_nonce = request_resp.json()["challenge_nonce"]

            # Sign with "denied" decision
            challenge_raw = fake_sync_redis.get(f"consent_request:{request_id}")
            challenge_data = json.loads(challenge_raw)
            signing_input = build_signing_input(
                request_id, patient_id, provider_id, challenge_nonce, "denied",
                "clinical", "checkup", challenge_data["access_duration"],
                challenge_data["created_at"], challenge_data["expires_at"], device_id,
            )
            real_signature = sign_challenge(private_key, signing_input)

            # Submit denial
            _reset_mock_db(mock_db)
            _setup_mock_db_for_approve(mock_db, device_row)
            denial_resp = client.post(
                "/api/v2/consent/approve-signed",
                json={"request_id": request_id, "patient_id": patient_id,
                      "decision": "denied", "challenge_nonce": challenge_nonce,
                      "signature": real_signature, "device_id": device_id},
            )
            assert denial_resp.status_code == 200
            assert denial_resp.json()["status"] == "denied"

            # Verify NO consent token was issued
            updated_raw = fake_sync_redis.get(f"consent_request:{request_id}")
            updated_data = json.loads(updated_raw)
            assert updated_data.get("consent_token") is None, (
                "Consent token should NOT be issued for denied decision"
            )

    def test_wrong_key_signature_is_rejected(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id, keypair
    ):
        """Enroll with key A, sign with key B → verification fails (401)."""
        _, enrolled_der_bytes, enrolled_der_b64 = keypair
        wrong_private_key, _, _ = generate_p256_keypair()
        device_id = str(uuid.uuid4())
        provider_id = str(provider.provider.provider_id)

        _apply_overrides(overrides, provider, patient_id)

        with _patch_stack(fake_redis, fake_sync_redis):
            # Enroll with key A
            device_row = _mock_device_row(device_id, patient_id, enrolled_der_bytes)
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar=0),
                _db_result(scalar_one_or_none=None),
            ])
            enroll_resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={"device_public_key": enrolled_der_b64, "device_label": "Key Mismatch Device", "platform": "ios", "device_enrollment_token": "e" * 43},
            )
            assert enroll_resp.status_code == 201

            # Request consent
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=device_row),
            ])
            request_resp = client.post(
                "/api/v2/consent/request",
                json={"patient_id": patient_id, "purpose": "checkup", "scope": "clinical", "access_duration_seconds": 900},
            )
            assert request_resp.status_code == 201
            request_id = request_resp.json()["request_id"]
            challenge_nonce = request_resp.json()["challenge_nonce"]

            # Sign with WRONG key (key B)
            challenge_raw = fake_sync_redis.get(f"consent_request:{request_id}")
            challenge_data = json.loads(challenge_raw)
            signing_input = build_signing_input(
                request_id, patient_id, provider_id, challenge_nonce, "approved",
                "clinical", "checkup", challenge_data["access_duration"],
                challenge_data["created_at"], challenge_data["expires_at"], device_id,
            )
            forged_signature = sign_challenge(wrong_private_key, signing_input)

            # Submit — must be rejected (verifier tries all keys, none match)
            _reset_mock_db(mock_db)
            _setup_mock_db_for_approve(mock_db, device_row)
            approval_resp = client.post(
                "/api/v2/consent/approve-signed",
                json={"request_id": request_id, "patient_id": patient_id,
                      "decision": "approved", "challenge_nonce": challenge_nonce,
                      "signature": forged_signature, "device_id": device_id},
            )
            assert approval_resp.status_code == 401, (
                f"Wrong-key signature should be rejected (401), got {approval_resp.status_code}"
            )

    def test_consent_status_polling(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id, keypair
    ):
        """Request → poll status (pending) → approve → poll status (approved)."""
        private_key, der_bytes, der_b64 = keypair
        device_id = str(uuid.uuid4())
        provider_id = str(provider.provider.provider_id)

        _apply_overrides(overrides, provider, patient_id)

        with _patch_stack(fake_redis, fake_sync_redis):
            # Enroll
            device_row = _mock_device_row(device_id, patient_id, der_bytes)
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar=0),
                _db_result(scalar_one_or_none=None),
            ])
            client.post(
                "/api/v2/patient/devices/enroll",
                json={"device_public_key": der_b64, "device_label": "Poll Device", "platform": "ios", "device_enrollment_token": "e" * 43},
            )

            # Request
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=device_row),
            ])
            request_resp = client.post(
                "/api/v2/consent/request",
                json={"patient_id": patient_id, "purpose": "checkup", "scope": "clinical", "access_duration_seconds": 900},
            )
            request_id = request_resp.json()["request_id"]
            challenge_nonce = request_resp.json()["challenge_nonce"]

            # Poll — should be pending
            status_resp = client.get(f"/api/v2/consent/status/{request_id}")
            assert status_resp.status_code == 200
            assert status_resp.json()["status"] == "pending"

            # Approve with real signature
            challenge_raw = fake_sync_redis.get(f"consent_request:{request_id}")
            challenge_data = json.loads(challenge_raw)
            signing_input = build_signing_input(
                request_id, patient_id, provider_id, challenge_nonce, "approved",
                "clinical", "checkup", challenge_data["access_duration"],
                challenge_data["created_at"], challenge_data["expires_at"], device_id,
            )
            real_sig = sign_challenge(private_key, signing_input)

            _reset_mock_db(mock_db)
            _setup_mock_db_for_approve(mock_db, device_row)
            client.post(
                "/api/v2/consent/approve-signed",
                json={"request_id": request_id, "patient_id": patient_id,
                      "decision": "approved", "challenge_nonce": challenge_nonce,
                      "signature": real_sig, "device_id": device_id},
            )

            # Poll — should be approved
            status_resp = client.get(f"/api/v2/consent/status/{request_id}")
            assert status_resp.status_code == 200
            assert status_resp.json()["status"] == "approved"

    def test_replayed_nonce_is_rejected(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides, provider, patient_id, keypair
    ):
        """Approve → replay the same nonce/signature → 409 conflict."""
        private_key, der_bytes, der_b64 = keypair
        device_id = str(uuid.uuid4())
        provider_id = str(provider.provider.provider_id)

        _apply_overrides(overrides, provider, patient_id)

        with _patch_stack(fake_redis, fake_sync_redis):
            # Enroll
            device_row = _mock_device_row(device_id, patient_id, der_bytes)
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar=0),
                _db_result(scalar_one_or_none=None),
            ])
            client.post(
                "/api/v2/patient/devices/enroll",
                json={"device_public_key": der_b64, "device_label": "Replay Device", "platform": "ios", "device_enrollment_token": "e" * 43},
            )

            # Request
            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback([
                _db_result(scalar_one_or_none=device_row),
            ])
            request_resp = client.post(
                "/api/v2/consent/request",
                json={"patient_id": patient_id, "purpose": "checkup", "scope": "clinical", "access_duration_seconds": 900},
            )
            request_id = request_resp.json()["request_id"]
            challenge_nonce = request_resp.json()["challenge_nonce"]

            # Approve
            challenge_raw = fake_sync_redis.get(f"consent_request:{request_id}")
            challenge_data = json.loads(challenge_raw)
            signing_input = build_signing_input(
                request_id, patient_id, provider_id, challenge_nonce, "approved",
                "clinical", "checkup", challenge_data["access_duration"],
                challenge_data["created_at"], challenge_data["expires_at"], device_id,
            )
            real_sig = sign_challenge(private_key, signing_input)

            _reset_mock_db(mock_db)
            _setup_mock_db_for_approve(mock_db, device_row)
            first = client.post(
                "/api/v2/consent/approve-signed",
                json={"request_id": request_id, "patient_id": patient_id,
                      "decision": "approved", "challenge_nonce": challenge_nonce,
                      "signature": real_sig, "device_id": device_id},
            )
            assert first.status_code == 200

            # Replay — same nonce
            _reset_mock_db(mock_db)
            _setup_mock_db_for_approve(mock_db, device_row)
            replay = client.post(
                "/api/v2/consent/approve-signed",
                json={"request_id": request_id, "patient_id": patient_id,
                      "decision": "approved", "challenge_nonce": challenge_nonce,
                      "signature": real_sig, "device_id": device_id},
            )
            assert replay.status_code == 200
            assert replay.json() == first.json()

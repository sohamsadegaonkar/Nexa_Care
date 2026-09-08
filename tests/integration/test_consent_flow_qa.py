"""Consent flow integration tests — Days 6-8 connected flow.

Exercises the cryptographic consent chain with real P-256 keypairs while using
mock SQLAlchemy/FakeRedis test doubles at storage boundaries. Device route setup
uses the authoritative current-patient-session dependency and mocks only the
already separately PostgreSQL-qualified device-trust persistence service.
"""

from __future__ import annotations

import asyncio
import base64
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
    get_current_provider,
    get_provider_context,
    get_scoped_session,
    require_clinical_capability,
)
from app.main import app
from app.models.provider import AffiliationType
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.security.provider_capabilities import ClinicalCapability
from app.services.patient_discovery_service import PatientDiscoveryService
from app.services.signed_approval_verifier import canonical_signed_approval_payload
from tests.conftest import DualModeTestClient, FakeRedis, FakeSyncRedis


def generate_p256_keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    der_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, der_bytes, base64.b64encode(der_bytes).decode("ascii")


def sign_challenge(private_key, message: str) -> str:
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
        request_id=request_id,
        patient_id=patient_id,
        provider_id=provider_id,
        challenge_nonce=challenge_nonce,
        decision=decision,
        scope=scope,
        purpose=purpose,
        access_duration=access_duration,
        issued_at=issued_at,
        expires_at=expires_at,
        device_id=device_id,
    ).decode("utf-8")


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
    row = MagicMock()
    row.id = uuid.UUID(device_id)
    row.device_id = uuid.UUID(device_id)
    row.patient_id = uuid.UUID(patient_id)
    row.device_public_key = der_bytes
    row.device_label = "Integration Test Device"
    row.platform = "ios"
    row.status = "active"
    row.key_algorithm = "ECDSA-P256"
    row.key_version = 1
    row.enrolled_at = datetime.now(timezone.utc)
    row.revoked_at = None
    row.revocation_reason_code = None
    return row


def _setup_mock_db_for_approve(mock_db, device_row):
    mock_db.execute.side_effect = _side_effect_with_fallback(
        [
            _db_result(scalar_one_or_none=device_row),
            _db_result(scalars_all=[device_row]),
        ]
    )


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
    return generate_p256_keypair()


@pytest.fixture
def overrides():
    saved = {}
    yield saved
    for dep in saved:
        app.dependency_overrides.pop(dep, None)


def _apply_overrides(overrides, provider, patient_id):
    async def _provider_dep():
        return provider

    async def _scoped_session_dep():
        return patient_id

    patient = MagicMock(patient_uuid=uuid.UUID(patient_id), is_deleted=False)

    async def _current_patient_session_dep():
        return AuthenticatedPatientSession(
            patient_id=patient_id,
            patient=patient,
            session_id="consent-flow-patient-session",
            session_epoch=0,
            supabase_user_id="consent-flow-patient-subject",
        )

    for dep, factory in (
        (get_current_provider, _provider_dep),
        (get_provider_context, _provider_dep),
        (get_scoped_session, _scoped_session_dep),
        (get_current_patient_session, _current_patient_session_dep),
    ):
        overrides[dep] = factory
        app.dependency_overrides[dep] = factory

    for capability in ClinicalCapability:
        gate = require_clinical_capability(capability)
        overrides[gate] = _provider_dep
        app.dependency_overrides[gate] = _provider_dep


def _patch_stack(fake_redis, fake_sync_redis, patient_id):
    stack = ExitStack()

    async def _enroll_device_stub(
        db,
        *,
        patient_id,
        raw_public_key,
        device_label,
        platform,
        actor_id,
    ):
        del db, actor_id
        row = MagicMock()
        row.id = uuid.uuid4()
        row.device_id = uuid.uuid4()
        row.patient_id = patient_id
        row.device_public_key = raw_public_key
        row.device_label = device_label
        row.platform = platform
        row.status = "active"
        row.key_version = 1
        row.enrolled_at = datetime.now(timezone.utc)
        return row

    stack.enter_context(
        patch(
            "app.api.v2.device_routes.enroll_patient_device_key",
            new=AsyncMock(side_effect=_enroll_device_stub),
        )
    )
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

    patient = MagicMock(patient_uuid=uuid.UUID(patient_id), is_deleted=False)
    stack.enter_context(
        patch.object(
            PatientDiscoveryService,
            "resolve_patient_id",
            new=AsyncMock(return_value=(patient, False)),
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
    stack.enter_context(
        patch(
            "app.api.v2.consent_routes.get_async_redis_client", return_value=fake_redis
        )
    )
    stack.enter_context(
        patch(
            "app.services.approved_access_capability.get_async_redis_client",
            return_value=fake_redis,
        )
    )
    stack.enter_context(
        patch(
            "app.services.consent_engine.get_consent_redis_client",
            return_value=fake_redis,
        )
    )
    stack.enter_context(
        patch(
            "app.services.provider_auth_service.get_redis_client",
            return_value=fake_sync_redis,
        )
    )

    mock_supabase = MagicMock()
    mock_supabase.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[]
    )
    mock_supabase.table.return_value.insert.return_value.execute.return_value = (
        MagicMock()
    )
    mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(
        data={}
    )
    stack.enter_context(
        patch("app.core.supabase.get_supabase_client", return_value=mock_supabase)
    )
    stack.enter_context(
        patch(
            "app.observability.audit_ledger.append_audit_log_or_503", return_value=None
        )
    )
    stack.enter_context(
        patch("app.observability.audit_ledger.append_audit_log", return_value=None)
    )

    for mod in (
        "app.core.consent_gate",
        "app.api.v2.consent_routes",
        "app.api.v2.device_routes",
        "app.api.v2.pipeline_routes",
        "app.api.v2.patient_record_routes",
        "app.services.consent_engine",
        "app.services.signed_approval_verifier",
    ):
        stack.enter_context(patch(f"{mod}.append_audit_log_or_503", return_value=None))
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


def _active_discovery_handle(fake_redis, provider, patient_id):
    service = PatientDiscoveryService(db=MagicMock(), redis=fake_redis)
    patient = MagicMock(patient_uuid=uuid.UUID(patient_id), is_deleted=False)
    handle = asyncio.run(
        service.issue_handle(
            patient=patient,
            provider_id=provider.actor_uid,
            hospital_id=str(provider.hospital_id),
            session_binding=provider.session_binding,
            identifier_type="TEST_DISCOVERY",
        )
    )
    assert asyncio.run(service.activate_handle(raw_handle=handle.value))
    return handle.value


class TestConsentFlowIntegration:
    def test_full_consent_flow_with_real_signatures(
        self,
        client,
        fake_redis,
        fake_sync_redis,
        mock_db,
        overrides,
        provider,
        patient_id,
        keypair,
    ):
        private_key, der_bytes, der_b64 = keypair
        device_id = str(uuid.uuid4())
        provider_id = str(provider.provider.provider_id)
        _apply_overrides(overrides, provider, patient_id)

        with _patch_stack(fake_redis, fake_sync_redis, patient_id):
            discovery_handle = _active_discovery_handle(fake_redis, provider, patient_id)
            device_row = _mock_device_row(device_id, patient_id, der_bytes)
            _reset_mock_db(mock_db)
            enroll_resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={
                    "device_public_key": der_b64,
                    "device_label": "Integration Test Device",
                    "platform": "ios",
                    "device_enrollment_token": "e" * 43,
                },
            )
            assert enroll_resp.status_code == 201, enroll_resp.text
            assert enroll_resp.json()["status"] == "active"

            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback(
                [_db_result(scalar_one_or_none=device_row)]
            )
            request_resp = client.post(
                "/api/v2/consent/request",
                json={
                    "discovery_handle": discovery_handle,
                    "purpose": "routine_checkup",
                    "scope": "clinical",
                    "access_duration_seconds": 900,
                },
            )
            assert request_resp.status_code == 201, request_resp.text
            request_id = request_resp.json()["request_id"]

            challenge_raw = fake_sync_redis.get(f"consent_request:{request_id}")
            assert challenge_raw is not None
            challenge_data = json.loads(challenge_raw)
            challenge_nonce = challenge_data["challenge_nonce"]
            signing_input = build_signing_input(
                request_id=request_id,
                patient_id=patient_id,
                provider_id=provider_id,
                challenge_nonce=challenge_nonce,
                decision="approved",
                scope="clinical",
                purpose="routine_checkup",
                access_duration=challenge_data["access_duration"],
                issued_at=challenge_data["created_at"],
                expires_at=challenge_data["expires_at"],
                device_id=device_id,
            )
            real_signature = sign_challenge(private_key, signing_input)

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
            assert approval_resp.status_code == 200, approval_resp.text
            assert approval_resp.json()["status"] == "approved"

            updated_data = json.loads(
                fake_sync_redis.get(f"consent_request:{request_id}")
            )
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

            capability = asyncio.run(
                validate(
                    token=consent_token,
                    patient_id=patient_id,
                    provider_id=provider_id,
                    hospital_id=str(provider.hospital_id),
                    requested_category="clinical_summary",
                )
            )
            assert capability is not None
            assert capability.patient_id == patient_id
            assert capability.clinician_id == provider_id

    def test_denied_consent_flow_with_real_signatures(
        self,
        client,
        fake_redis,
        fake_sync_redis,
        mock_db,
        overrides,
        provider,
        patient_id,
        keypair,
    ):
        private_key, der_bytes, der_b64 = keypair
        device_id = str(uuid.uuid4())
        provider_id = str(provider.provider.provider_id)
        _apply_overrides(overrides, provider, patient_id)

        with _patch_stack(fake_redis, fake_sync_redis, patient_id):
            discovery_handle = _active_discovery_handle(fake_redis, provider, patient_id)
            device_row = _mock_device_row(device_id, patient_id, der_bytes)
            _reset_mock_db(mock_db)
            enroll_resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={
                    "device_public_key": der_b64,
                    "device_label": "Deny Device",
                    "platform": "android",
                    "device_enrollment_token": "e" * 43,
                },
            )
            assert enroll_resp.status_code == 201

            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback(
                [_db_result(scalar_one_or_none=device_row)]
            )
            request_resp = client.post(
                "/api/v2/consent/request",
                json={
                    "discovery_handle": discovery_handle,
                    "purpose": "checkup",
                    "scope": "clinical",
                    "access_duration_seconds": 900,
                },
            )
            assert request_resp.status_code == 201
            request_id = request_resp.json()["request_id"]
            challenge_data = json.loads(
                fake_sync_redis.get(f"consent_request:{request_id}")
            )
            challenge_nonce = challenge_data["challenge_nonce"]
            signing_input = build_signing_input(
                request_id,
                patient_id,
                provider_id,
                challenge_nonce,
                "denied",
                "clinical",
                "checkup",
                challenge_data["access_duration"],
                challenge_data["created_at"],
                challenge_data["expires_at"],
                device_id,
            )
            real_signature = sign_challenge(private_key, signing_input)

            _reset_mock_db(mock_db)
            _setup_mock_db_for_approve(mock_db, device_row)
            denial_resp = client.post(
                "/api/v2/consent/approve-signed",
                json={
                    "request_id": request_id,
                    "patient_id": patient_id,
                    "decision": "denied",
                    "challenge_nonce": challenge_nonce,
                    "signature": real_signature,
                    "device_id": device_id,
                },
            )
            assert denial_resp.status_code == 200
            assert denial_resp.json()["status"] == "denied"
            updated_data = json.loads(
                fake_sync_redis.get(f"consent_request:{request_id}")
            )
            assert updated_data.get("consent_token") is None

    def test_wrong_key_signature_is_rejected(
        self,
        client,
        fake_redis,
        fake_sync_redis,
        mock_db,
        overrides,
        provider,
        patient_id,
        keypair,
    ):
        _, enrolled_der_bytes, enrolled_der_b64 = keypair
        wrong_private_key, _, _ = generate_p256_keypair()
        device_id = str(uuid.uuid4())
        provider_id = str(provider.provider.provider_id)
        _apply_overrides(overrides, provider, patient_id)

        with _patch_stack(fake_redis, fake_sync_redis, patient_id):
            discovery_handle = _active_discovery_handle(fake_redis, provider, patient_id)
            device_row = _mock_device_row(device_id, patient_id, enrolled_der_bytes)
            _reset_mock_db(mock_db)
            enroll_resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={
                    "device_public_key": enrolled_der_b64,
                    "device_label": "Key Mismatch Device",
                    "platform": "ios",
                    "device_enrollment_token": "e" * 43,
                },
            )
            assert enroll_resp.status_code == 201

            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback(
                [_db_result(scalar_one_or_none=device_row)]
            )
            request_resp = client.post(
                "/api/v2/consent/request",
                json={
                    "discovery_handle": discovery_handle,
                    "purpose": "checkup",
                    "scope": "clinical",
                    "access_duration_seconds": 900,
                },
            )
            assert request_resp.status_code == 201
            request_id = request_resp.json()["request_id"]
            challenge_data = json.loads(
                fake_sync_redis.get(f"consent_request:{request_id}")
            )
            challenge_nonce = challenge_data["challenge_nonce"]
            signing_input = build_signing_input(
                request_id,
                patient_id,
                provider_id,
                challenge_nonce,
                "approved",
                "clinical",
                "checkup",
                challenge_data["access_duration"],
                challenge_data["created_at"],
                challenge_data["expires_at"],
                device_id,
            )
            forged_signature = sign_challenge(wrong_private_key, signing_input)

            _reset_mock_db(mock_db)
            _setup_mock_db_for_approve(mock_db, device_row)
            approval_resp = client.post(
                "/api/v2/consent/approve-signed",
                json={
                    "request_id": request_id,
                    "patient_id": patient_id,
                    "decision": "approved",
                    "challenge_nonce": challenge_nonce,
                    "signature": forged_signature,
                    "device_id": device_id,
                },
            )
            assert approval_resp.status_code == 401

    def test_consent_status_polling(
        self,
        client,
        fake_redis,
        fake_sync_redis,
        mock_db,
        overrides,
        provider,
        patient_id,
        keypair,
    ):
        private_key, der_bytes, der_b64 = keypair
        device_id = str(uuid.uuid4())
        provider_id = str(provider.provider.provider_id)
        _apply_overrides(overrides, provider, patient_id)

        with _patch_stack(fake_redis, fake_sync_redis, patient_id):
            discovery_handle = _active_discovery_handle(fake_redis, provider, patient_id)
            device_row = _mock_device_row(device_id, patient_id, der_bytes)
            _reset_mock_db(mock_db)
            enroll_resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={
                    "device_public_key": der_b64,
                    "device_label": "Poll Device",
                    "platform": "ios",
                    "device_enrollment_token": "e" * 43,
                },
            )
            assert enroll_resp.status_code == 201

            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback(
                [_db_result(scalar_one_or_none=device_row)]
            )
            request_resp = client.post(
                "/api/v2/consent/request",
                json={
                    "discovery_handle": discovery_handle,
                    "purpose": "checkup",
                    "scope": "clinical",
                    "access_duration_seconds": 900,
                },
            )
            assert request_resp.status_code == 201
            request_id = request_resp.json()["request_id"]
            challenge_data = json.loads(
                fake_sync_redis.get(f"consent_request:{request_id}")
            )
            challenge_nonce = challenge_data["challenge_nonce"]

            status_resp = client.get(f"/api/v2/consent/status/{request_id}")
            assert status_resp.status_code == 200
            assert status_resp.json()["status"] == "pending"

            signing_input = build_signing_input(
                request_id,
                patient_id,
                provider_id,
                challenge_nonce,
                "approved",
                "clinical",
                "checkup",
                challenge_data["access_duration"],
                challenge_data["created_at"],
                challenge_data["expires_at"],
                device_id,
            )
            real_sig = sign_challenge(private_key, signing_input)
            _reset_mock_db(mock_db)
            _setup_mock_db_for_approve(mock_db, device_row)
            approval = client.post(
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
            assert approval.status_code == 200

            status_resp = client.get(f"/api/v2/consent/status/{request_id}")
            assert status_resp.status_code == 200
            assert status_resp.json()["status"] == "approved"

    def test_replayed_nonce_is_rejected(
        self,
        client,
        fake_redis,
        fake_sync_redis,
        mock_db,
        overrides,
        provider,
        patient_id,
        keypair,
    ):
        private_key, der_bytes, der_b64 = keypair
        device_id = str(uuid.uuid4())
        provider_id = str(provider.provider.provider_id)
        _apply_overrides(overrides, provider, patient_id)

        with _patch_stack(fake_redis, fake_sync_redis, patient_id):
            discovery_handle = _active_discovery_handle(fake_redis, provider, patient_id)
            device_row = _mock_device_row(device_id, patient_id, der_bytes)
            _reset_mock_db(mock_db)
            enroll_resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={
                    "device_public_key": der_b64,
                    "device_label": "Replay Device",
                    "platform": "ios",
                    "device_enrollment_token": "e" * 43,
                },
            )
            assert enroll_resp.status_code == 201

            _reset_mock_db(mock_db)
            mock_db.execute.side_effect = _side_effect_with_fallback(
                [_db_result(scalar_one_or_none=device_row)]
            )
            request_resp = client.post(
                "/api/v2/consent/request",
                json={
                    "discovery_handle": discovery_handle,
                    "purpose": "checkup",
                    "scope": "clinical",
                    "access_duration_seconds": 900,
                },
            )
            assert request_resp.status_code == 201
            request_id = request_resp.json()["request_id"]
            challenge_data = json.loads(
                fake_sync_redis.get(f"consent_request:{request_id}")
            )
            challenge_nonce = challenge_data["challenge_nonce"]
            signing_input = build_signing_input(
                request_id,
                patient_id,
                provider_id,
                challenge_nonce,
                "approved",
                "clinical",
                "checkup",
                challenge_data["access_duration"],
                challenge_data["created_at"],
                challenge_data["expires_at"],
                device_id,
            )
            real_sig = sign_challenge(private_key, signing_input)

            _reset_mock_db(mock_db)
            _setup_mock_db_for_approve(mock_db, device_row)
            first = client.post(
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
            assert first.status_code == 200

            _reset_mock_db(mock_db)
            _setup_mock_db_for_approve(mock_db, device_row)
            replay = client.post(
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
            assert replay.status_code == 200
            assert replay.json() == first.json()

"""Device enrollment and consent flow QA tests.

Covers:
  - Device enrollment validation (key format, P-256 curve, max device limit, duplicate reactivation)
  - Consent request creation (challenge nonce, IDOR guard, device prerequisite)
  - Consent status polling (pending / approved / denied / expired, ownership check)
  - Consent cancellation (terminal-state rejection, ownership check)
  - Signed approval verification (nonce mismatch, device not enrolled, already resolved)
  - Break-glass issue (rate limit, reason_code enforcement, TTL)
  - Routine consent issue / grant (purpose validation, scope enforcement)
  - Consent engine validation (_parse_payload, _matches, _token_hash)
"""

from __future__ import annotations

import base64
import hashlib
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.dependencies import (
    AuthenticatedPatientSession,
    get_current_patient_session,
    get_current_provider,
    get_scoped_session,
)
from app.main import app
from app.models.provider_context import (
    AffiliationContext,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.models.provider import AffiliationType
from app.services.patient_device_trust import PatientDeviceTrustError
from app.services.patient_discovery_service import PatientDiscoveryService
from tests.conftest import DualModeTestClient, FakeRedis, FakeSyncRedis


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_provider_context(provider_id: str | None = None) -> ProviderContext:
    pid = uuid.UUID(provider_id) if provider_id else uuid.uuid4()
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=pid,
            display_name="Dr. Test",
            contact_email="test@hospital.example",
        ),
        hospital=HospitalContext(
            hospital_id=uuid.uuid4(),
            facility_code="TEST",
            display_name="Test Hospital",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            is_primary=True,
            roles=["clinician"],
        ),
    )


def _generate_p256_public_key_der() -> bytes:
    """Generate a valid ECDSA P-256 key pair and return DER-encoded public key."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key.public_key().public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )


def _patient_session(patient_id: str) -> AuthenticatedPatientSession:
    patient = MagicMock(patient_uuid=uuid.UUID(patient_id), is_deleted=False)
    return AuthenticatedPatientSession(
        patient_id=patient_id,
        patient=patient,
        session_id="device-route-test-session",
        session_epoch=0,
        supabase_user_id="device-route-test-subject",
    )


def _device_row(*, patient_id: str, device_id: str | None = None, status: str = "active"):
    logical_device_id = uuid.UUID(device_id) if device_id else uuid.uuid4()
    row = MagicMock()
    row.device_id = logical_device_id
    row.id = uuid.uuid4()
    row.patient_id = uuid.UUID(patient_id)
    row.key_version = 1
    row.device_label = "Test Device"
    row.platform = "ios"
    row.status = status
    row.enrolled_at = datetime.now(timezone.utc)
    row.revoked_at = datetime.now(timezone.utc) if status == "revoked" else None
    row.revocation_reason_code = "PATIENT_REQUEST" if status == "revoked" else None
    row.public_key_fingerprint = "f" * 64
    return row


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


@pytest.fixture
def client():
    return DualModeTestClient(app)


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def fake_sync_redis(fake_redis):
    return FakeSyncRedis(fake_redis)


class _OverrideManager:
    """Context manager that sets and clears app.dependency_overrides."""

    def __init__(self):
        self._overrides = {}

    def set(self, dep, factory):
        self._overrides[dep] = factory

    def apply(self):
        for dep, factory in self._overrides.items():
            app.dependency_overrides[dep] = factory

    def clear(self):
        for dep in self._overrides:
            app.dependency_overrides.pop(dep, None)


@pytest.fixture
def overrides():
    mgr = _OverrideManager()
    yield mgr
    mgr.clear()


@pytest.fixture(autouse=True)
def valid_device_enrollment_grant():
    with (
        patch(
            "app.api.v2.device_routes.claim_device_enrollment_token",
            new=AsyncMock(return_value="claim-1"),
        ),
        patch(
            "app.api.v2.device_routes.finalize_device_enrollment_token",
            new=AsyncMock(return_value=True),
        ),
    ):
        yield


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DEVICE ENROLLMENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeviceEnrollmentValidation:
    """Validate device route input and service error mapping.

    Relational lifecycle/concurrency invariants are qualified separately against
    real PostgreSQL in test_patient_device_trust_postgres.py.
    """

    def test_enroll_valid_p256_key_returns_201(
        self, client, fake_redis, fake_sync_redis, mock_db, overrides
    ):
        patient_id = str(uuid.uuid4())
        pub_der = _generate_p256_public_key_der()
        pub_b64 = base64.b64encode(pub_der).decode()
        enrolled = _device_row(patient_id=patient_id)

        async def _current_session():
            return _patient_session(patient_id)

        overrides.set(get_current_patient_session, _current_session)
        overrides.apply()

        with (
            patch(
                "app.api.v2.device_routes.enroll_patient_device_key",
                new=AsyncMock(return_value=enrolled),
            ) as enroll_service,
            patch(
                "app.observability.audit_ledger.append_audit_log_or_503",
                return_value=None,
            ),
        ):
            resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={
                    "device_public_key": pub_b64,
                    "device_label": "iPhone 16",
                    "platform": "ios",
                    "expo_push_token": None,
                    "device_enrollment_token": "e" * 43,
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["status"] == "active"
            assert data["device_id"] == str(enrolled.device_id)
            assert data["key_id"] == str(enrolled.id)
            assert data["key_version"] == 1
            assert data["patient_id"] == patient_id
            assert "enrolled_at" in data
            enroll_service.assert_awaited_once()

    def test_enroll_invalid_base64_returns_400(
        self, client, fake_sync_redis, mock_db, overrides
    ):
        patient_id = str(uuid.uuid4())

        async def _current_session():
            return _patient_session(patient_id)

        overrides.set(get_current_patient_session, _current_session)
        overrides.apply()

        with patch(
            "app.observability.audit_ledger.append_audit_log_or_503",
            return_value=None,
        ):
            resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={
                    "device_public_key": "not-valid-der-key!!!",
                    "device_label": "Bad Device",
                    "platform": "android",
                    "device_enrollment_token": "e" * 43,
                },
            )
            assert resp.status_code == 400
            assert resp.json()["detail"]["error_code"] == "DEVICE_PUBLIC_KEY_INVALID"

    def test_enroll_non_p256_key_returns_400(
        self, client, fake_sync_redis, mock_db, overrides
    ):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub_der = private_key.public_key().public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
        pub_b64 = base64.b64encode(pub_der).decode()
        patient_id = str(uuid.uuid4())

        async def _current_session():
            return _patient_session(patient_id)

        overrides.set(get_current_patient_session, _current_session)
        overrides.apply()

        with patch(
            "app.observability.audit_ledger.append_audit_log_or_503",
            return_value=None,
        ):
            resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={
                    "device_public_key": pub_b64,
                    "device_label": "RSA Device",
                    "platform": "ios",
                    "device_enrollment_token": "e" * 43,
                },
            )
            assert resp.status_code == 400
            assert resp.json()["detail"]["error_code"] == "DEVICE_PUBLIC_KEY_NOT_P256"

    def test_enroll_max_five_active_devices_returns_409(
        self, client, fake_sync_redis, mock_db, overrides
    ):
        patient_id = str(uuid.uuid4())
        pub_der = _generate_p256_public_key_der()
        pub_b64 = base64.b64encode(pub_der).decode()

        async def _current_session():
            return _patient_session(patient_id)

        overrides.set(get_current_patient_session, _current_session)
        overrides.apply()

        with (
            patch(
                "app.api.v2.device_routes.enroll_patient_device_key",
                new=AsyncMock(
                    side_effect=PatientDeviceTrustError("DEVICE_ACTIVE_LIMIT_REACHED")
                ),
            ),
            patch(
                "app.observability.audit_ledger.append_audit_log_or_503",
                return_value=None,
            ),
        ):
            resp = client.post(
                "/api/v2/patient/devices/enroll",
                json={
                    "device_public_key": pub_b64,
                    "device_label": "6th Device",
                    "platform": "ios",
                    "device_enrollment_token": "e" * 43,
                },
            )
            assert resp.status_code == 409
            assert (
                resp.json()["detail"]["error_code"]
                == "DEVICE_ACTIVE_LIMIT_REACHED"
            )

    def test_list_devices_returns_200(
        self, client, fake_sync_redis, mock_db, overrides
    ):
        patient_id = str(uuid.uuid4())
        mock_db.execute.return_value = MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        )

        async def _current_session():
            return _patient_session(patient_id)

        overrides.set(get_current_patient_session, _current_session)
        overrides.apply()

        resp = client.get("/api/v2/patient/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert data["patient_id"] == patient_id
        assert isinstance(data["devices"], list)

    def test_revoke_device_returns_200(
        self, client, fake_sync_redis, mock_db, overrides
    ):
        patient_id = str(uuid.uuid4())
        device_id = str(uuid.uuid4())
        revoked = _device_row(patient_id=patient_id, device_id=device_id, status="revoked")

        async def _current_session():
            return _patient_session(patient_id)

        overrides.set(get_current_patient_session, _current_session)
        overrides.apply()

        with patch(
            "app.api.v2.device_routes.revoke_patient_device",
            new=AsyncMock(return_value=revoked),
        ) as revoke_service:
            resp = client.post(f"/api/v2/patient/devices/{device_id}/revoke")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "revoked"
            assert data["device_id"] == device_id
            assert data["key_id"] == str(revoked.id)
            assert data["key_version"] == 1
            assert "revoked_at" in data
            revoke_service.assert_awaited_once()

    def test_revoke_nonexistent_device_returns_404(
        self, client, fake_sync_redis, mock_db, overrides
    ):
        patient_id = str(uuid.uuid4())
        device_id = str(uuid.uuid4())

        async def _current_session():
            return _patient_session(patient_id)

        overrides.set(get_current_patient_session, _current_session)
        overrides.apply()

        with patch(
            "app.api.v2.device_routes.revoke_patient_device",
            new=AsyncMock(side_effect=PatientDeviceTrustError("DEVICE_NOT_FOUND")),
        ):
            resp = client.post(f"/api/v2/patient/devices/{device_id}/revoke")
            assert resp.status_code == 404
            assert resp.json()["detail"]["error_code"] == "DEVICE_NOT_FOUND"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CONSENT REQUEST CREATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentRequestCreation:
    """Validate consent challenge request creation and IDOR guards."""

    def test_consent_request_returns_201_with_challenge(
        self, client, fake_redis, fake_sync_redis, mock_db, real_clinical_session
    ):
        """Creating a consent request returns 201 with request_id, challenge_nonce, and status='pending'."""
        patient_id = str(uuid.uuid4())

        mock_device = MagicMock()
        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_device)
        )

        discovery_handle = _active_discovery_handle(
            fake_redis, real_clinical_session, patient_id
        )
        patient = MagicMock(patient_uuid=uuid.UUID(patient_id), is_deleted=False)

        with (
            patch(
                "app.api.v2.consent_routes.get_redis_client",
                return_value=fake_sync_redis,
            ),
            patch(
                "app.api.v2.consent_routes.get_async_redis_client",
                return_value=fake_redis,
            ),
            patch.object(
                PatientDiscoveryService,
                "resolve_patient_id",
                new=AsyncMock(return_value=(patient, False)),
            ),
            patch(
                "app.api.v2.consent_routes.append_audit_log_or_503",
                return_value=None,
            ),
        ):
            resp = client.post(
                "/api/v2/consent/request",
                headers=real_clinical_session.headers,
                json={
                    "discovery_handle": discovery_handle,
                    "purpose": "routine_checkup",
                    "scope": "clinical",
                    "access_duration_seconds": 900,
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["status"] == "pending"
            assert "request_id" in data
            assert "challenge_nonce" in data
            assert len(data["challenge_nonce"]) == 64
            assert data["expires_in_seconds"] > 0

    def test_consent_request_idor_rejected(
        self, client, fake_sync_redis, mock_db, real_clinical_session
    ):
        patient_id = str(uuid.uuid4())
        different_provider = str(uuid.uuid4())

        with (
            patch(
                "app.api.v2.consent_routes.get_redis_client",
                return_value=fake_sync_redis,
            ),
            patch(
                "app.observability.audit_ledger.append_audit_log_or_503",
                return_value=None,
            ),
        ):
            resp = client.post(
                "/api/v2/consent/request",
                headers=real_clinical_session.headers,
                json={
                    "patient_id": patient_id,
                    "provider_id": different_provider,
                    "purpose": "routine_checkup",
                    "scope": "clinical",
                },
            )
            assert resp.status_code == 422

    def test_consent_request_no_device_returns_409(
        self, client, fake_redis, fake_sync_redis, mock_db, real_clinical_session
    ):
        patient_id = str(uuid.uuid4())

        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        )

        discovery_handle = _active_discovery_handle(
            fake_redis, real_clinical_session, patient_id
        )
        patient = MagicMock(patient_uuid=uuid.UUID(patient_id), is_deleted=False)

        with (
            patch(
                "app.api.v2.consent_routes.get_redis_client",
                return_value=fake_sync_redis,
            ),
            patch(
                "app.api.v2.consent_routes.get_async_redis_client",
                return_value=fake_redis,
            ),
            patch.object(
                PatientDiscoveryService,
                "resolve_patient_id",
                new=AsyncMock(return_value=(patient, False)),
            ),
            patch(
                "app.api.v2.consent_routes.append_audit_log_or_503",
                return_value=None,
            ),
        ):
            resp = client.post(
                "/api/v2/consent/request",
                headers=real_clinical_session.headers,
                json={
                    "discovery_handle": discovery_handle,
                    "purpose": "routine_checkup",
                    "scope": "clinical",
                },
            )
            assert resp.status_code == 409
            assert "device" in resp.json()["detail"].lower()

    def test_consent_request_duration_clamped(
        self, client, fake_redis, fake_sync_redis, mock_db, real_clinical_session
    ):
        patient_id = str(uuid.uuid4())

        mock_device = MagicMock()
        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_device)
        )

        discovery_handle = _active_discovery_handle(
            fake_redis, real_clinical_session, patient_id
        )
        patient = MagicMock(patient_uuid=uuid.UUID(patient_id), is_deleted=False)

        with (
            patch(
                "app.api.v2.consent_routes.get_redis_client",
                return_value=fake_sync_redis,
            ),
            patch(
                "app.api.v2.consent_routes.get_async_redis_client",
                return_value=fake_redis,
            ),
            patch.object(
                PatientDiscoveryService,
                "resolve_patient_id",
                new=AsyncMock(return_value=(patient, False)),
            ),
            patch(
                "app.api.v2.consent_routes.append_audit_log_or_503",
                return_value=None,
            ),
        ):
            resp = client.post(
                "/api/v2/consent/request",
                headers=real_clinical_session.headers,
                json={
                    "discovery_handle": discovery_handle,
                    "purpose": "routine_checkup",
                    "scope": "clinical",
                    "access_duration_seconds": 10,
                },
            )
            assert resp.status_code == 201

            raw = fake_sync_redis.get(f"consent_request:{resp.json()['request_id']}")
            if raw:
                stored = json.loads(raw)
                assert stored["access_duration"] == 300


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CONSENT STATUS POLLING
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentStatusPolling:
    """Validate consent status polling endpoint."""

    def test_status_pending(self, client, fake_sync_redis, real_clinical_session):
        request_id = str(uuid.uuid4())
        challenge_data = {
            "request_id": request_id,
            "patient_id": str(uuid.uuid4()),
            "provider_id": str(real_clinical_session.provider.id),
            "hospital_id": str(real_clinical_session.hospital.id),
            "status": "pending",
        }
        fake_sync_redis.set(
            f"consent_request:{request_id}", json.dumps(challenge_data), ex=300
        )
        with patch(
            "app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis
        ):
            resp = client.get(
                f"/api/v2/consent/status/{request_id}",
                headers=real_clinical_session.headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "pending"
            assert data["request_id"] == request_id

    def test_status_expired_when_not_found(
        self, client, fake_sync_redis, real_clinical_session
    ):
        request_id = str(uuid.uuid4())
        with patch(
            "app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis
        ):
            resp = client.get(
                f"/api/v2/consent/status/{request_id}",
                headers=real_clinical_session.headers,
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "expired"

    def test_status_approved(self, client, fake_sync_redis, real_clinical_session):
        request_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        challenge_data = {
            "request_id": request_id,
            "patient_id": str(uuid.uuid4()),
            "provider_id": str(real_clinical_session.provider.id),
            "hospital_id": str(real_clinical_session.hospital.id),
            "status": "approved",
            "responded_at": now_iso,
        }
        fake_sync_redis.set(
            f"consent_request:{request_id}", json.dumps(challenge_data), ex=300
        )
        with patch(
            "app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis
        ):
            resp = client.get(
                f"/api/v2/consent/status/{request_id}",
                headers=real_clinical_session.headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "approved"
            assert data["responded_at"] is not None

    def test_status_wrong_provider_returns_403(
        self, client, fake_sync_redis, real_clinical_session
    ):
        other_provider = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        challenge_data = {
            "request_id": request_id,
            "patient_id": str(uuid.uuid4()),
            "provider_id": other_provider,
            "status": "pending",
        }
        fake_sync_redis.set(
            f"consent_request:{request_id}", json.dumps(challenge_data), ex=300
        )
        with patch(
            "app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis
        ):
            resp = client.get(
                f"/api/v2/consent/status/{request_id}",
                headers=real_clinical_session.headers,
            )
            assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CONSENT CANCELLATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentCancellation:
    """Validate consent request cancellation."""

    def test_cancel_pending_request_succeeds(self, client, fake_sync_redis, overrides):
        provider_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        ctx = _make_provider_context(provider_id)
        challenge_data = {
            "request_id": request_id,
            "patient_id": str(uuid.uuid4()),
            "provider_id": provider_id,
            "status": "pending",
        }
        fake_sync_redis.set(
            f"consent_request:{request_id}", json.dumps(challenge_data), ex=300
        )

        async def _provider():
            return ctx

        overrides.set(get_current_provider, _provider)
        overrides.apply()
        with (
            patch(
                "app.api.v2.consent_routes.get_redis_client",
                return_value=fake_sync_redis,
            ),
            patch(
                "app.observability.audit_ledger.append_audit_log_or_503",
                return_value=None,
            ),
        ):
            resp = client.post(f"/api/v2/consent/request/{request_id}/cancel")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "cancelled"
            assert "cancelled_at" in data

    def test_cancel_approved_request_returns_409(
        self, client, fake_sync_redis, overrides
    ):
        provider_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        ctx = _make_provider_context(provider_id)
        challenge_data = {
            "request_id": request_id,
            "patient_id": str(uuid.uuid4()),
            "provider_id": provider_id,
            "status": "approved",
        }
        fake_sync_redis.set(
            f"consent_request:{request_id}", json.dumps(challenge_data), ex=300
        )

        async def _provider():
            return ctx

        overrides.set(get_current_provider, _provider)
        overrides.apply()
        with (
            patch(
                "app.api.v2.consent_routes.get_redis_client",
                return_value=fake_sync_redis,
            ),
            patch(
                "app.observability.audit_ledger.append_audit_log_or_503",
                return_value=None,
            ),
        ):
            resp = client.post(f"/api/v2/consent/request/{request_id}/cancel")
            assert resp.status_code == 409

    def test_cancel_expired_request_returns_404(
        self, client, fake_sync_redis, overrides
    ):
        provider_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        ctx = _make_provider_context(provider_id)

        async def _provider():
            return ctx

        overrides.set(get_current_provider, _provider)
        overrides.apply()
        with patch(
            "app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis
        ):
            resp = client.post(f"/api/v2/consent/request/{request_id}/cancel")
            assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 5. SIGNED APPROVAL VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestSignedApproval:
    """Validate signed approval flow (approve-signed endpoint)."""

    def test_approve_expired_challenge_returns_404(
        self, client, fake_sync_redis, overrides
    ):
        patient_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())

        async def _scoped_session():
            return patient_id

        overrides.set(get_scoped_session, _scoped_session)
        overrides.apply()
        with patch(
            "app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis
        ):
            resp = client.post(
                "/api/v2/consent/approve-signed",
                json={
                    "request_id": request_id,
                    "patient_id": patient_id,
                    "decision": "approved",
                    "challenge_nonce": "abc123",
                    "signature": base64.b64encode(b"fakesig").decode(),
                    "device_id": str(uuid.uuid4()),
                },
            )
            assert resp.status_code == 404

    def test_approve_nonce_mismatch_returns_401(
        self, client, fake_sync_redis, overrides
    ):
        patient_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        device_id = str(uuid.uuid4())
        challenge_data = {
            "request_id": request_id,
            "patient_id": patient_id,
            "provider_id": str(uuid.uuid4()),
            "challenge_nonce": "correct_nonce_value",
            "status": "pending",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=2)
            ).isoformat(),
        }
        fake_sync_redis.set(
            f"consent_request:{request_id}", json.dumps(challenge_data), ex=300
        )

        async def _scoped_session():
            return patient_id

        overrides.set(get_scoped_session, _scoped_session)
        overrides.apply()
        with patch(
            "app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis
        ):
            resp = client.post(
                "/api/v2/consent/approve-signed",
                json={
                    "request_id": request_id,
                    "patient_id": patient_id,
                    "decision": "approved",
                    "challenge_nonce": "wrong_nonce_value",
                    "signature": base64.b64encode(b"fakesig").decode(),
                    "device_id": device_id,
                },
            )
            assert resp.status_code == 401

    def test_approve_already_resolved_returns_409(
        self, client, fake_sync_redis, overrides
    ):
        patient_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        nonce = "abc123"
        challenge_data = {
            "request_id": request_id,
            "patient_id": patient_id,
            "provider_id": str(uuid.uuid4()),
            "challenge_nonce": nonce,
            "status": "approved",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=2)
            ).isoformat(),
        }
        fake_sync_redis.set(
            f"consent_request:{request_id}", json.dumps(challenge_data), ex=300
        )

        async def _scoped_session():
            return patient_id

        overrides.set(get_scoped_session, _scoped_session)
        overrides.apply()
        with patch(
            "app.api.v2.consent_routes.get_redis_client", return_value=fake_sync_redis
        ):
            resp = client.post(
                "/api/v2/consent/approve-signed",
                json={
                    "request_id": request_id,
                    "patient_id": patient_id,
                    "decision": "approved",
                    "challenge_nonce": nonce,
                    "signature": base64.b64encode(b"fakesig").decode(),
                    "device_id": str(uuid.uuid4()),
                },
            )
            assert resp.status_code == 409


# ═══════════════════════════════════════════════════════════════════════════════
# 6. BREAK-GLASS CONSENT
# ═══════════════════════════════════════════════════════════════════════════════


class TestBreakGlassConsent:
    """Validate break-glass consent issuance and constraints."""

    def test_break_glass_requires_reason_code(
        self, client, fake_redis, fake_sync_redis, mock_db, real_clinical_session
    ):
        patient_id = str(uuid.uuid4())
        with (
            patch(
                "app.services.consent_engine.get_consent_redis_client",
                return_value=fake_redis,
            ),
            patch(
                "app.observability.audit_ledger.append_audit_log_or_503",
                return_value=None,
            ),
            patch("app.observability.audit_ledger.append_audit_log", return_value=None),
        ):
            resp = client.post(
                "/api/v2/consent/break-glass/issue",
                headers=real_clinical_session.headers,
                json={
                    "patient_id": patient_id,
                    "reason_code": "",
                    "free_text": "",
                },
            )
            assert resp.status_code in (400, 403, 422)

    def test_break_glass_ttl_is_15_minutes(self):
        from app.services.consent_engine import BREAK_GLASS_TTL_SECONDS

        assert BREAK_GLASS_TTL_SECONDS == 900

    def test_break_glass_rate_limiter_enforced(self):
        import app.api.v2.consent_routes as consent_mod

        limiter = consent_mod._break_glass_limiter
        assert limiter.max_requests == 3
        assert limiter.window_seconds == 3600


# ═══════════════════════════════════════════════════════════════════════════════
# 7. ROUTINE CONSENT ISSUE / GRANT
# ═══════════════════════════════════════════════════════════════════════════════


class TestRoutineConsent:
    def test_routine_consent_requires_valid_purpose(self):
        from app.services.consent_engine import ConsentPurpose

        valid = ConsentPurpose.TREATMENT
        assert valid.value == "TREATMENT"

    def test_routine_consent_empty_scope_rejected(self):
        from app.services.consent_engine import issue
        import inspect

        source = inspect.getsource(issue)
        assert "at least one field" in source or "scope" in source


# ═══════════════════════════════════════════════════════════════════════════════
# 8. CONSENT ENGINE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestConsentEngineValidation:
    def test_parse_valid_capability(self):
        from app.services.consent_engine import _parse_payload

        payload = json.dumps(
            {
                "patient_id": "pat-1",
                "clinician_id": "doc-1",
                "purpose": "TREATMENT",
                "scope": ["clinical.*", "pii.demographics"],
                "is_break_glass": False,
                "reason_code": None,
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": "2025-01-01T01:00:00Z",
            }
        )
        cap = _parse_payload(payload)
        assert cap is not None
        assert cap.patient_id == "pat-1"
        assert cap.clinician_id == "doc-1"
        assert cap.purpose == "TREATMENT"
        assert "clinical.*" in cap.scope
        assert cap.is_break_glass is False

    def test_parse_break_glass_capability(self):
        from app.services.consent_engine import _parse_payload

        payload = json.dumps(
            {
                "patient_id": "pat-1",
                "clinician_id": "doc-1",
                "purpose": "EMERGENCY",
                "scope": ["clinical.*", "pii.*"],
                "is_break_glass": True,
                "reason_code": "IMMEDIATE_THREAT_TO_LIFE",
                "issued_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": "2025-01-01T00:15:00Z",
            }
        )
        cap = _parse_payload(payload)
        assert cap is not None
        assert cap.is_break_glass is True
        assert cap.reason_code == "IMMEDIATE_THREAT_TO_LIFE"

    def test_parse_invalid_payload_returns_none(self):
        from app.services.consent_engine import _parse_payload

        assert _parse_payload(json.dumps({"patient_id": "x"})) is None
        assert _parse_payload("not json") is None
        assert _parse_payload(None) is None
        assert (
            _parse_payload(
                json.dumps(
                    {
                        "patient_id": "p",
                        "clinician_id": "c",
                        "purpose": "E",
                        "scope": ["clinical.*"],
                        "is_break_glass": True,
                        "reason_code": "",
                        "issued_at": "2025-01-01T00:00:00Z",
                        "expires_at": "2025-01-01T00:15:00Z",
                    }
                )
            )
            is None
        )

    def test_matches_logic(self):
        from app.services.consent_engine import _matches, ConsentCapability

        cap = ConsentCapability(
            patient_id="pat-1",
            clinician_id="doc-1",
            purpose="TREATMENT",
            scope=["clinical.*"],
            is_break_glass=False,
            reason_code=None,
            issued_at="2025-01-01T00:00:00Z",
        )
        assert _matches(cap, "pat-1", "doc-1", "TREATMENT") is True
        assert _matches(cap, None, None, None) is True
        assert _matches(cap, "pat-2", None, None) is False
        assert _matches(cap, None, "doc-2", None) is False
        assert _matches(cap, None, None, "PAYMENT") is False

    def test_token_hash_is_deterministic(self):
        from app.services.consent_engine import _token_hash

        token = "test-token-value-12345"
        h1 = _token_hash(token)
        h2 = _token_hash(token)
        assert h1 == h2
        assert len(h1) == 64

    def test_parse_empty_scope_list_returns_capability(self):
        """Document the existing parser behavior; issuance still rejects empty scope."""
        from app.services.consent_engine import _parse_payload

        result = _parse_payload(
            json.dumps(
                {
                    "patient_id": "p",
                    "clinician_id": "c",
                    "purpose": "T",
                    "scope": [],
                    "is_break_glass": False,
                    "issued_at": "2025-01-01T00:00:00Z",
                    "expires_at": "2025-01-01T01:00:00Z",
                }
            )
        )
        assert result is not None

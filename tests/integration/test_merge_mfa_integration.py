"""Integration tests for merge with MFA re-authentication (Squad C Day 3).

DEFECT 5: exercises the real /api/v2/auth/challenge/merge,
/challenge/merge/verify, and /api/v2/patient/merge routes end to end,
with a genuine pyotp TOTP code checked against a Fernet-encrypted secret
-- the actual production MFA path, not a stub. PatientMergeService.merge_patients
itself (the business logic of merging two patient records) is mocked out
here deliberately: that is a distinct concern with its own test coverage,
and mixing it in would make failures here ambiguous about whether MFA or
merge logic broke.
"""

import base64
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_current_provider, get_db_session
from app.main import app
from app.models.provider import AffiliationType, ProviderCredential
from app.models.provider_context import AffiliationContext, HospitalContext, ProviderContext, ProviderIdentityContext


class FakeAsyncRedis:
    """In-memory stand-in for the async redis client used by the merge
    challenge flow (get/setex/get/delete/getdel/ttl)."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def set(self, key, value, ex=None, nx=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        existed = key in self.store
        self.store.pop(key, None)
        return 1 if existed else 0

    async def getdel(self, key):
        return self.store.pop(key, None)

    async def ttl(self, key):
        return 300 if key in self.store else -2


class FakeSyncRedisPipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    def incr(self, key):
        self.ops.append(("incr", key))
        return self

    def expire(self, key, seconds):
        self.ops.append(("expire", key))
        return self

    def execute(self):
        results = []
        for op, key in self.ops:
            if op == "incr":
                self.redis.counters[key] = self.redis.counters.get(key, 0) + 1
                results.append(self.redis.counters[key])
        self.ops.clear()
        return results


class FakeSyncRedis:
    """Stand-in for the sync redis client used by MFA fail-counter tracking."""

    def __init__(self):
        self.counters: dict[str, int] = {}

    def get(self, key):
        value = self.counters.get(key)
        return str(value) if value is not None else None

    def pipeline(self):
        return FakeSyncRedisPipeline(self)

    def delete(self, key):
        self.counters.pop(key, None)


def _provider(roles=("admin",)) -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(provider_id=uuid.uuid4(), display_name="Dr. Merge", contact_email="m@ex.com"),
        hospital=HospitalContext(hospital_id=uuid.uuid4(), facility_code="H", display_name="H"),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(), affiliation_type=AffiliationType.PERMANENT,
            is_primary=True, roles=list(roles),
        ),
    )


AUTH_HEADER = {"Authorization": "Bearer test-session-token-merge-flow"}


@pytest.fixture
def mfa_env(monkeypatch):
    key = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8")
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", key)
    return key


@pytest.mark.asyncio
async def test_merge_requires_fresh_challenge(mfa_env):
    """No X-Merge-Challenge header / no prior challenge -> merge is
    refused, exercised against the real /api/v2/patient/merge route."""
    provider = _provider()
    app.dependency_overrides[get_current_provider] = lambda: provider
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    try:
        client = TestClient(app)
        response = client.post(
            "/api/v2/patient/merge",
            json={
                "old_patient_uuid": str(uuid.uuid4()),
                "canonical_patient_uuid": str(uuid.uuid4()),
                "reason": "duplicate record",
            },
            headers={**AUTH_HEADER, "X-Merge-Challenge": "never-issued-challenge-token"},
        )
        assert response.status_code == 403
        assert "challenge" in response.text.lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_merge_with_verified_mfa_challenge_succeeds(mfa_env):
    """Full real cycle: create challenge -> verify with a genuine TOTP code
    against a Fernet-encrypted secret -> merge succeeds. Proves the merge
    gate actually requires -- and accepts -- fresh, real MFA, not a stub."""
    from app.core.security import encrypt_mfa_secret

    provider = _provider()
    totp_secret = pyotp.random_base32()
    credential = MagicMock(spec=ProviderCredential)
    credential.mfa_enabled = True
    credential.mfa_secret_encrypted = encrypt_mfa_secret(totp_secret)

    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = credential
    db.execute = AsyncMock(return_value=db_result)

    async_redis = FakeAsyncRedis()
    sync_redis = FakeSyncRedis()
    fake_tombstone = MagicMock(tombstone_id=uuid.uuid4(), canonical_patient_uuid=uuid.uuid4())

    app.dependency_overrides[get_current_provider] = lambda: provider
    app.dependency_overrides[get_db_session] = lambda: db

    try:
        with patch("app.api.v2.auth_routes.get_async_redis_client", return_value=async_redis), \
             patch("app.api.v2.merge_routes.get_redis_client", return_value=async_redis), \
             patch("app.services.provider_auth_service.get_redis_client", return_value=sync_redis), \
             patch("app.api.v2.auth_routes.append_audit_log", AsyncMock(return_value=True)), \
             patch("app.observability.audit_ledger.append_audit_log", AsyncMock(return_value=True)), \
             patch("app.observability.audit_ledger.append_audit_log_or_503", AsyncMock(return_value=None)), \
             patch(
                 "app.services.merge_service.PatientMergeService.merge_patients",
                 new=AsyncMock(return_value=fake_tombstone),
             ):
            client = TestClient(app)

            challenge_resp = client.post("/api/v2/auth/challenge/merge", headers=AUTH_HEADER)
            assert challenge_resp.status_code == 200, challenge_resp.text
            challenge_token = challenge_resp.json()["challenge_token"]

            # A genuine, real-time-derived TOTP code -- not a hardcoded stub.
            valid_code = pyotp.TOTP(totp_secret).now()

            verify_resp = client.post(
                "/api/v2/auth/challenge/merge/verify",
                json={"challenge_token": challenge_token, "totp_code": valid_code},
                headers=AUTH_HEADER,
            )
            assert verify_resp.status_code == 200, verify_resp.text
            assert verify_resp.json()["verified"] is True

            merge_resp = client.post(
                "/api/v2/patient/merge",
                json={
                    "old_patient_uuid": str(uuid.uuid4()),
                    "canonical_patient_uuid": str(uuid.uuid4()),
                    "reason": "duplicate record confirmed by front desk",
                },
                headers={**AUTH_HEADER, "X-Merge-Challenge": challenge_token},
            )
            assert merge_resp.status_code == 201, merge_resp.text
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_merge_challenge_cannot_be_replayed(mfa_env):
    """A verified challenge is single-use: attempting to merge a second
    time with the same (already-consumed) challenge token fails."""
    from app.core.security import encrypt_mfa_secret

    provider = _provider()
    totp_secret = pyotp.random_base32()
    credential = MagicMock(spec=ProviderCredential)
    credential.mfa_enabled = True
    credential.mfa_secret_encrypted = encrypt_mfa_secret(totp_secret)

    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = credential
    db.execute = AsyncMock(return_value=db_result)

    async_redis = FakeAsyncRedis()
    sync_redis = FakeSyncRedis()
    fake_tombstone = MagicMock(tombstone_id=uuid.uuid4(), canonical_patient_uuid=uuid.uuid4())

    app.dependency_overrides[get_current_provider] = lambda: provider
    app.dependency_overrides[get_db_session] = lambda: db

    try:
        with patch("app.api.v2.auth_routes.get_async_redis_client", return_value=async_redis), \
             patch("app.api.v2.merge_routes.get_redis_client", return_value=async_redis), \
             patch("app.services.provider_auth_service.get_redis_client", return_value=sync_redis), \
             patch("app.api.v2.auth_routes.append_audit_log", AsyncMock(return_value=True)), \
             patch("app.observability.audit_ledger.append_audit_log", AsyncMock(return_value=True)), \
             patch("app.observability.audit_ledger.append_audit_log_or_503", AsyncMock(return_value=None)), \
             patch(
                 "app.services.merge_service.PatientMergeService.merge_patients",
                 new=AsyncMock(return_value=fake_tombstone),
             ):
            client = TestClient(app)

            challenge_token = client.post("/api/v2/auth/challenge/merge", headers=AUTH_HEADER).json()["challenge_token"]
            valid_code = pyotp.TOTP(totp_secret).now()
            client.post(
                "/api/v2/auth/challenge/merge/verify",
                json={"challenge_token": challenge_token, "totp_code": valid_code},
                headers=AUTH_HEADER,
            )

            merge_payload = {
                "old_patient_uuid": str(uuid.uuid4()),
                "canonical_patient_uuid": str(uuid.uuid4()),
                "reason": "first merge",
            }
            first = client.post(
                "/api/v2/patient/merge", json=merge_payload,
                headers={**AUTH_HEADER, "X-Merge-Challenge": challenge_token},
            )
            assert first.status_code == 201

            second = client.post(
                "/api/v2/patient/merge", json=merge_payload,
                headers={**AUTH_HEADER, "X-Merge-Challenge": challenge_token},
            )
            assert second.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_merge_rejects_wrong_totp_code(mfa_env):
    from app.core.security import encrypt_mfa_secret

    provider = _provider()
    totp_secret = pyotp.random_base32()
    credential = MagicMock(spec=ProviderCredential)
    credential.mfa_enabled = True
    credential.mfa_secret_encrypted = encrypt_mfa_secret(totp_secret)

    db = AsyncMock()
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = credential
    db.execute = AsyncMock(return_value=db_result)

    async_redis = FakeAsyncRedis()
    sync_redis = FakeSyncRedis()

    app.dependency_overrides[get_current_provider] = lambda: provider
    app.dependency_overrides[get_db_session] = lambda: db

    try:
        with patch("app.api.v2.auth_routes.get_async_redis_client", return_value=async_redis), \
             patch("app.services.provider_auth_service.get_redis_client", return_value=sync_redis), \
             patch("app.api.v2.auth_routes.append_audit_log", AsyncMock(return_value=True)):
            client = TestClient(app)
            challenge_token = client.post("/api/v2/auth/challenge/merge", headers=AUTH_HEADER).json()["challenge_token"]

            wrong_code_resp = client.post(
                "/api/v2/auth/challenge/merge/verify",
                json={"challenge_token": challenge_token, "totp_code": "000000"},
                headers=AUTH_HEADER,
            )
            assert wrong_code_resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_merge_challenge_bound_to_issuing_session(mfa_env):
    """A challenge created under one session token cannot be verified
    using a different session token, even for the same provider identity."""
    provider = _provider()
    async_redis = FakeAsyncRedis()

    app.dependency_overrides[get_current_provider] = lambda: provider
    app.dependency_overrides[get_db_session] = lambda: AsyncMock()

    try:
        with patch("app.api.v2.auth_routes.get_async_redis_client", return_value=async_redis), \
             patch("app.api.v2.auth_routes.append_audit_log", AsyncMock(return_value=True)):
            client = TestClient(app)
            challenge_token = client.post("/api/v2/auth/challenge/merge", headers=AUTH_HEADER).json()["challenge_token"]

            verify_resp = client.post(
                "/api/v2/auth/challenge/merge/verify",
                json={"challenge_token": challenge_token, "totp_code": "123456"},
                headers={"Authorization": "Bearer a-completely-different-session-token"},
            )
            assert verify_resp.status_code == 403
            assert verify_resp.json()["detail"]["error_code"] == "MERGE_CHALLENGE_BINDING_MISMATCH"
    finally:
        app.dependency_overrides.clear()
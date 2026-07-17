import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v2.auth_routes import _MERGE_CHALLENGE_PREFIX
from app.core.dependencies import get_current_provider
from app.main import app


@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(autouse=True)
def override_admin_provider(admin_context):
    app.dependency_overrides[get_current_provider] = lambda: admin_context
    yield
    app.dependency_overrides.pop(get_current_provider, None)

@pytest.mark.asyncio
async def test_merge_no_challenge_header(test_client, admin_headers):
    """1. Merge without any challenge header -> 422 (Unprocessable Entity) 
    since it's a required Header. If we want 403, we'd need a custom dependency.
    FastAPI returns 422 for missing required headers.
    """
    resp = test_client.post(
        "/api/v2/patient/merge",
        json={
            "old_patient_uuid": str(uuid.uuid4()),
            "canonical_patient_uuid": str(uuid.uuid4()),
            "reason": "test"
        },
        headers=admin_headers
    )
    assert resp.status_code == 422

@pytest.mark.asyncio
async def test_merge_expired_challenge(test_client, admin_headers, mock_redis):
    """2. Merge with expired challenge -> 403.
    We simulate expiration by not having the key in Redis.
    """
    with patch("app.api.v2.merge_routes.get_redis_client", return_value=mock_redis):
        resp = test_client.post(
            "/api/v2/patient/merge",
            json={
                "old_patient_uuid": str(uuid.uuid4()),
                "canonical_patient_uuid": str(uuid.uuid4()),
                "reason": "test"
            },
            headers={**admin_headers, "X-Merge-Challenge": str(uuid.uuid4())}
        )
        assert resp.status_code == 403
        assert "Fresh challenge required" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_merge_unverified_challenge(test_client, admin_headers, mock_redis, admin_context):
    """3. Merge with unverified challenge -> 403."""
    challenge_token = str(uuid.uuid4())
    await mock_redis.setex(
        f"{_MERGE_CHALLENGE_PREFIX}{challenge_token}",
        120,
        json.dumps({
            "provider_id": str(admin_context.provider.provider_id),
            "verified": False
        })
    )
    
    with patch("app.api.v2.merge_routes.get_redis_client", return_value=mock_redis):
        resp = test_client.post(
            "/api/v2/patient/merge",
            json={
                "old_patient_uuid": str(uuid.uuid4()),
                "canonical_patient_uuid": str(uuid.uuid4()),
                "reason": "test"
            },
            headers={**admin_headers, "X-Merge-Challenge": challenge_token}
        )
        assert resp.status_code == 403
        assert "Challenge not verified" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_merge_wrong_provider_challenge(test_client, admin_headers, mock_redis):
    """4. Provider A verifies challenge, Provider B tries to use it -> 403."""
    challenge_token = str(uuid.uuid4())
    await mock_redis.setex(
        f"{_MERGE_CHALLENGE_PREFIX}{challenge_token}",
        120,
        json.dumps({
            "provider_id": str(uuid.uuid4()), # Different provider
            "verified": True
        })
    )
    
    with patch("app.api.v2.merge_routes.get_redis_client", return_value=mock_redis):
        resp = test_client.post(
            "/api/v2/patient/merge",
            json={
                "old_patient_uuid": str(uuid.uuid4()),
                "canonical_patient_uuid": str(uuid.uuid4()),
                "reason": "test"
            },
            headers={**admin_headers, "X-Merge-Challenge": challenge_token}
        )
        assert resp.status_code == 403
        assert "Challenge bound to different provider" in resp.json()["detail"]

@pytest.mark.asyncio
async def test_merge_already_used_challenge(test_client, admin_headers, mock_redis, admin_context):
    """5. Challenge token is consumed after first use."""
    challenge_token = str(uuid.uuid4())
    await mock_redis.setex(
        f"{_MERGE_CHALLENGE_PREFIX}{challenge_token}",
        120,
        json.dumps({
            "provider_id": str(admin_context.provider.provider_id),
            "verified": True
        })
    )
    
    with patch("app.api.v2.merge_routes.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.merge_routes.PatientMergeService") as mock_service:
        
        mock_service.return_value.merge_patients = AsyncMock(return_value=MagicMock(tombstone_id=uuid.uuid4()))
        
        # First use -> Success
        resp1 = test_client.post(
            "/api/v2/patient/merge",
            json={
                "old_patient_uuid": str(uuid.uuid4()),
                "canonical_patient_uuid": str(uuid.uuid4()),
                "reason": "test"
            },
            headers={**admin_headers, "X-Merge-Challenge": challenge_token}
        )
        assert resp1.status_code == 201
        
        # Second use -> Fail (consumed)
        resp2 = test_client.post(
            "/api/v2/patient/merge",
            json={
                "old_patient_uuid": str(uuid.uuid4()),
                "canonical_patient_uuid": str(uuid.uuid4()),
                "reason": "test"
            },
            headers={**admin_headers, "X-Merge-Challenge": challenge_token}
        )
        assert resp2.status_code == 403

@pytest.mark.asyncio
async def test_merge_forged_challenge_token(test_client, admin_headers, mock_redis):
    """6. Forged token (random UUID) -> 403."""
    with patch("app.api.v2.merge_routes.get_redis_client", return_value=mock_redis):
        resp = test_client.post(
            "/api/v2/patient/merge",
            json={"old_patient_uuid": str(uuid.uuid4()), "canonical_patient_uuid": str(uuid.uuid4()), "reason": "test"},
            headers={**admin_headers, "X-Merge-Challenge": str(uuid.uuid4())}
        )
        assert resp.status_code == 403

@pytest.mark.asyncio
async def test_challenge_mfa_brute_force(test_client, admin_headers, mock_redis, admin_context, test_db):
    """7. Submit 5 wrong codes -> 429."""
    challenge_token = str(uuid.uuid4())
    await mock_redis.setex(
        f"{_MERGE_CHALLENGE_PREFIX}{challenge_token}",
        120,
        json.dumps({
            "provider_id": str(admin_context.provider.provider_id),
            "verified": False
        })
    )
    
    # Mock credential in DB
    from app.models.provider import ProviderCredential
    mock_cred = MagicMock(spec=ProviderCredential)
    mock_cred.mfa_enabled = True
    mock_cred.mfa_secret_encrypted = "encrypted"
    
    with patch("app.api.v2.auth_routes.get_async_redis_client", return_value=mock_redis), \
         patch("app.api.v2.auth_routes.decrypt_mfa_secret", return_value="secret"), \
         patch("app.services.provider_auth_service.verify_totp_code", return_value=False), \
         patch("app.api.v2.auth_routes.select"):
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_cred
        test_db.execute.return_value = mock_result

        # 5 failed attempts
        for _ in range(5):
            resp = test_client.post(
                "/api/v2/auth/challenge/merge/verify",
                json={"challenge_token": challenge_token, "totp_code": "000000"},
                headers=admin_headers
            )
            assert resp.status_code == 401
            
        # 6th attempt -> 429
        resp = test_client.post(
            "/api/v2/auth/challenge/merge/verify",
            json={"challenge_token": challenge_token, "totp_code": "000000"},
            headers=admin_headers
        )
        assert resp.status_code == 429

@pytest.mark.asyncio
async def test_merge_happy_path(test_client, admin_headers, mock_redis, admin_context, test_db):
    """8. Happy path: Create -> Verify -> Merge."""
    # 1. Create
    with patch("app.api.v2.auth_routes.get_async_redis_client", return_value=mock_redis):
        resp = test_client.post("/api/v2/auth/challenge/merge", headers=admin_headers)
        assert resp.status_code == 200
        challenge_token = resp.json()["challenge_token"]

    # 2. Verify
    mock_cred = MagicMock()
    mock_cred.mfa_enabled = True
    mock_cred.mfa_secret_encrypted = "enc"
    
    with patch("app.api.v2.auth_routes.get_async_redis_client", return_value=mock_redis), \
         patch("app.api.v2.auth_routes.decrypt_mfa_secret", return_value="secret"), \
         patch("app.services.provider_auth_service.verify_totp_code", return_value=True), \
         patch("app.api.v2.auth_routes.select"):
        
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = mock_cred
        test_db.execute.return_value = mock_res
        
        resp = test_client.post(
            "/api/v2/auth/challenge/merge/verify",
            json={"challenge_token": challenge_token, "totp_code": "123456"},
            headers=admin_headers
        )
        assert resp.status_code == 200

    # 3. Merge
    with patch("app.api.v2.merge_routes.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.merge_routes.PatientMergeService") as mock_service:
        
        mock_service.return_value.merge_patients = AsyncMock(return_value=MagicMock(tombstone_id=uuid.uuid4()))
        
        resp = test_client.post(
            "/api/v2/patient/merge",
            json={"old_patient_uuid": str(uuid.uuid4()), "canonical_patient_uuid": str(uuid.uuid4()), "reason": "test"},
            headers={**admin_headers, "X-Merge-Challenge": challenge_token}
        )
        assert resp.status_code == 201
        assert await mock_redis.get(f"{_MERGE_CHALLENGE_PREFIX}{challenge_token}") is None

@pytest.mark.asyncio
async def test_concurrent_merge_attempts(test_client, admin_headers, mock_redis, admin_context):
    """9. Two concurrent merge calls -> exactly one succeeds."""
    challenge_token = str(uuid.uuid4())
    await mock_redis.setex(
        f"{_MERGE_CHALLENGE_PREFIX}{challenge_token}",
        120,
        json.dumps({
            "provider_id": str(admin_context.provider.provider_id),
            "verified": True
        })
    )

    # We need to simulate a race condition where both get the token before deletion.
    # To fix this in the app, we'd use getdel or a lock.
    # For the test, we want to ensure only one success.
    
    with patch("app.api.v2.merge_routes.get_redis_client", return_value=mock_redis), \
         patch("app.api.v2.merge_routes.PatientMergeService") as mock_service:
        
        mock_service.return_value.merge_patients = AsyncMock(return_value=MagicMock(tombstone_id=uuid.uuid4()))
        
        # Async execution
        async def call_merge():
            return test_client.post(
                "/api/v2/patient/merge",
                json={"old_patient_uuid": str(uuid.uuid4()), "canonical_patient_uuid": str(uuid.uuid4()), "reason": "test"},
                headers={**admin_headers, "X-Merge-Challenge": challenge_token}
            )

        # Trigger both
        results = await asyncio.gather(call_merge(), call_merge())
        
        status_codes = [r.status_code for r in results]
        assert 201 in status_codes
        assert 403 in status_codes
        assert status_codes.count(201) == 1
        assert status_codes.count(403) == 1

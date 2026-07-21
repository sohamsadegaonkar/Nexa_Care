import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

from app.core.dependencies import get_current_provider
from app.main import app


@pytest.fixture(autouse=True)
def override_admin_provider(admin_context):
    app.dependency_overrides[get_current_provider] = lambda: admin_context
    yield
    app.dependency_overrides.pop(get_current_provider, None)


@pytest.mark.asyncio
async def test_merge_challenge_flow(test_client, test_db, admin_token, admin_context):
    # 1. Create challenge
    resp = test_client.post(
        "/api/v2/auth/challenge/merge",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    challenge_token = data["challenge_token"]
    assert challenge_token is not None

    # 2. Verify TOTP (Mocked)
    with (
        patch("app.services.provider_auth_service.verify_totp_code", return_value=True),
        patch("app.api.v2.auth_routes.decrypt_mfa_secret", return_value="secret"),
        patch("app.api.v2.auth_routes.select"),
    ):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock(
            mfa_enabled=True, mfa_secret_encrypted="encrypted"
        )
        test_db.execute.return_value = mock_result

        resp = test_client.post(
            "/api/v2/auth/challenge/merge/verify",
            json={"challenge_token": challenge_token, "totp_code": "123456"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["verified"] is True

    # 3. Perform Merge (Mocked service)
    old_uuid = str(uuid.uuid4())
    canonical_uuid = str(uuid.uuid4())

    with patch("app.api.v2.merge_routes.PatientMergeService") as mock_service:
        mock_instance = mock_service.return_value
        mock_instance.merge_patients = AsyncMock(
            return_value=MagicMock(tombstone_id=uuid.uuid4())
        )

        resp = test_client.post(
            "/api/v2/patient/merge",
            json={
                "old_patient_uuid": old_uuid,
                "canonical_patient_uuid": canonical_uuid,
                "reason": "testing",
            },
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Merge-Challenge": challenge_token,
            },
        )
        assert resp.status_code == 201
        assert "merged successfully" in resp.json()["message"]


@pytest.mark.asyncio
async def test_merge_without_challenge_fails(test_client, admin_token):
    old_uuid = str(uuid.uuid4())
    canonical_uuid = str(uuid.uuid4())

    resp = test_client.post(
        "/api/v2/patient/merge",
        json={
            "old_patient_uuid": old_uuid,
            "canonical_patient_uuid": canonical_uuid,
            "reason": "testing",
        },
        headers={
            "Authorization": f"Bearer {admin_token}",
            # Missing X-Merge-Challenge
        },
    )
    # FastAPI returns 422 for missing required header by default if defined as Header(...)
    # But I used Header(..., alias="X-Merge-Challenge")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_merge_with_unverified_challenge_fails(test_client, admin_token):
    # Create challenge but don't verify
    resp = test_client.post(
        "/api/v2/auth/challenge/merge",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    challenge_token = resp.json()["challenge_token"]

    old_uuid = str(uuid.uuid4())
    canonical_uuid = str(uuid.uuid4())

    resp = test_client.post(
        "/api/v2/patient/merge",
        json={
            "old_patient_uuid": old_uuid,
            "canonical_patient_uuid": canonical_uuid,
            "reason": "testing",
        },
        headers={
            "Authorization": f"Bearer {admin_token}",
            "X-Merge-Challenge": challenge_token,
        },
    )
    assert resp.status_code == 403
    assert "Challenge not verified" in resp.json()["detail"]

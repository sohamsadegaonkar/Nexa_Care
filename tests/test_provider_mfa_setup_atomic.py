"""Regression proof that MFA authority enablement cannot outlive audit staging."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.v2.auth_routes import ProviderMfaSetupVerifyRequest, provider_mfa_setup_verify


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


def _provider(provider_id: uuid.UUID):
    return SimpleNamespace(
        actor_uid="synthetic-provider",
        provider=SimpleNamespace(provider_id=provider_id),
    )


@pytest.mark.asyncio
async def test_mfa_success_audit_stage_failure_rolls_back_authority_enablement():
    provider_id = uuid.uuid4()
    credential = SimpleNamespace(mfa_enabled=False, mfa_secret_encrypted="ciphertext")
    async def rollback() -> None:
        credential.mfa_enabled = False
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(credential)),
        commit=AsyncMock(),
        rollback=AsyncMock(side_effect=rollback),
    )
    with (
        patch("app.api.v2.auth_routes.decrypt_mfa_secret", return_value="totp-secret"),
        patch(
            "app.services.provider_auth_service.verify_totp_code_once",
            AsyncMock(return_value=True),
        ),
        patch("app.api.v2.auth_routes.get_async_redis_client", return_value=object()),
        patch(
            "app.api.v2.auth_routes.enqueue_audit_event",
            AsyncMock(side_effect=RuntimeError("outbox unavailable")),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await provider_mfa_setup_verify(
                ProviderMfaSetupVerifyRequest(totp_code="123456"),
                db,
                _provider(provider_id),
            )

    assert exc_info.value.status_code == 503
    assert credential.mfa_enabled is False
    db.commit.assert_not_awaited()
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_mfa_success_stages_audit_before_the_only_commit():
    provider_id = uuid.uuid4()
    credential = SimpleNamespace(mfa_enabled=False, mfa_secret_encrypted="ciphertext")
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_Result(credential)),
        commit=AsyncMock(),
        rollback=AsyncMock(),
    )
    audit = AsyncMock()
    with (
        patch("app.api.v2.auth_routes.decrypt_mfa_secret", return_value="totp-secret"),
        patch(
            "app.services.provider_auth_service.verify_totp_code_once",
            AsyncMock(return_value=True),
        ),
        patch("app.api.v2.auth_routes.get_async_redis_client", return_value=object()),
        patch("app.api.v2.auth_routes.enqueue_audit_event", audit),
    ):
        response = await provider_mfa_setup_verify(
            ProviderMfaSetupVerifyRequest(totp_code="123456"),
            db,
            _provider(provider_id),
        )

    assert response == {"message": "MFA has been successfully enabled."}
    assert credential.mfa_enabled is True
    audit.assert_awaited_once()
    db.commit.assert_awaited_once()
    db.rollback.assert_not_awaited()

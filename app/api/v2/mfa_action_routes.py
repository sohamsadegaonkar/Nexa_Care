"""Step-up MFA verification for privileged actions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.redis import get_async_redis_client
from app.core.dependencies import get_current_provider
from app.core.session_binding import provider_session_token
from app.core.security import decrypt_mfa_secret
from app.models.provider import ProviderCredential
from app.models.provider_context import ProviderContext
from app.services.provider_auth_service import (
    mark_provider_session_mfa_verified,
    verify_totp_code_once,
)
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context

router = APIRouter(prefix="/api/v2/auth/mfa", tags=["mfa-action"])


class VerifyActionRequest(BaseModel):
    code: str


@router.post("/verify-action")
async def verify_action_mfa(
    payload: VerifyActionRequest,
    request: Request,
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
):
    """Verify and consume a fresh TOTP code for a privileged action."""
    stmt = select(ProviderCredential).where(
        ProviderCredential.provider_id == provider.provider.provider_id
    )
    result = await db.execute(stmt)
    credential = result.scalar_one_or_none()
    if (
        not credential
        or not credential.mfa_enabled
        or not credential.mfa_secret_encrypted
    ):
        raise HTTPException(status_code=400, detail="MFA not enabled or configured")

    secret = decrypt_mfa_secret(credential.mfa_secret_encrypted)
    if not secret or not await verify_totp_code_once(
        provider.provider.provider_id,
        secret,
        payload.code,
        redis_client=get_async_redis_client(),
    ):
        raise HTTPException(status_code=401, detail="Invalid MFA code")

    session_token = provider_session_token(request)
    if not session_token or not await mark_provider_session_mfa_verified(
        session_token, provider.provider.provider_id
    ):
        raise HTTPException(
            status_code=401, detail={"error_code": "PROVIDER_SESSION_INVALID"}
        )
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.AUTH),
        actor_uid=provider.actor_uid,
        event_type="PROVIDER_STEP_UP_MFA_VERIFIED",
        target_id=str(provider.provider.provider_id),
        status="SUCCESS",
        metadata={"assurance": "totp", "session_bound": True},
    )
    return {"verified": True}

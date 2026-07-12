"""Step-up MFA verification for privileged actions."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.redis import get_redis_client
from app.core.dependencies import get_provider_context
from app.core.security import decrypt_mfa_secret
from app.models.provider import ProviderCredential
from app.models.provider_context import ProviderContext
from app.services.provider_auth_service import verify_totp_code_once

router = APIRouter(prefix="/api/v2/auth/mfa", tags=["mfa-action"])


class VerifyActionRequest(BaseModel):
    code: str


@router.post("/verify-action")
async def verify_action_mfa(
    payload: VerifyActionRequest,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
):
    """Verify and consume a fresh TOTP code for a privileged action."""
    stmt = select(ProviderCredential).where(ProviderCredential.provider_id == provider.provider.provider_id)
    result = await db.execute(stmt)
    credential = result.scalar_one_or_none()
    if not credential or not credential.mfa_enabled or not credential.mfa_secret_encrypted:
        raise HTTPException(status_code=400, detail="MFA not enabled or configured")

    secret = decrypt_mfa_secret(credential.mfa_secret_encrypted)
    if not secret or not await verify_totp_code_once(provider.provider.provider_id, secret, payload.code, redis_client=get_redis_client()):
        raise HTTPException(status_code=401, detail="Invalid MFA code")

    return {"verified": True, "provider_id": str(provider.provider.provider_id)}

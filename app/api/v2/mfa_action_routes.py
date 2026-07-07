"""
Step-up MFA verification for privileged actions (e.g. Patient Merge)
Uses the existing authenticated session instead of login's pending token.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.dependencies import get_provider_context
from app.models.provider_context import ProviderContext
from app.services.provider_auth_service import verify_totp_code

router = APIRouter(prefix="/api/v2/auth/mfa", tags=["mfa-action"])


class VerifyActionRequest(BaseModel):
    code: str


@router.post("/verify-action")
async def verify_action_mfa(
    payload: VerifyActionRequest,
    provider: ProviderContext = Depends(get_provider_context),
):
    """
    Verify a fresh TOTP code for a privileged action.
    The provider must already be authenticated via access_token.
    """
    is_valid = await verify_totp_code(provider.provider_id, payload.code)

    if not is_valid:
        raise HTTPException(status_code=401, detail="Invalid MFA code")

    return {"verified": True, "provider_id": provider.provider_id}

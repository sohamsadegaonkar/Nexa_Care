"""Step-up MFA verification for privileged actions."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    ProviderStepUpPrincipal,
    get_provider_step_up_principal,
)
from app.core.redis import get_async_redis_client
from app.core.security import decrypt_mfa_secret
from app.models.provider import ProviderCredential, ProviderIdentity
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditContext, AuditDomain
from app.services.provider_auth_service import (
    mark_provider_session_mfa_verified,
    resolve_provider_session_context,
    verify_totp_code_once,
)

router = APIRouter(prefix="/api/v2/auth/mfa", tags=["mfa-action"])


class VerifyActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str


@router.post("/verify-action")
async def verify_action_mfa(
    payload: VerifyActionRequest,
    principal: ProviderStepUpPrincipal = Depends(get_provider_step_up_principal),
    db: AsyncSession = Depends(get_db_session),
):
    """Verify and consume a fresh TOTP code for a privileged action.

    Operates strictly from base provider session authentication + current PostgreSQL
    provider account and credential state.  Does NOT require clinical affiliations,
    roles, or clinical capabilities.
    """
    code = payload.code.strip()
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=401, detail={"error_code": "INVALID_MFA_CODE"})

    # Load current provider identity and credential
    stmt = (
        select(ProviderIdentity, ProviderCredential)
        .join(
            ProviderCredential,
            ProviderCredential.provider_id == ProviderIdentity.id,
        )
        .where(ProviderIdentity.id == principal.provider_id)
    )
    result = await db.execute(stmt)
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=401, detail={"error_code": "PROVIDER_ACCOUNT_INACTIVE"}
        )
    identity, credential = row

    if not identity.is_active or identity.status != "active":
        raise HTTPException(
            status_code=401, detail={"error_code": "PROVIDER_ACCOUNT_INACTIVE"}
        )

    if not credential.is_active:
        raise HTTPException(
            status_code=401, detail={"error_code": "PROVIDER_CREDENTIAL_INACTIVE"}
        )

    if not credential.mfa_enabled or not credential.mfa_secret_encrypted:
        raise HTTPException(
            status_code=400, detail={"error_code": "MFA_NOT_CONFIGURED"}
        )

    try:
        secret = decrypt_mfa_secret(credential.mfa_secret_encrypted)
    except Exception:
        secret = None

    if not secret:
        raise HTTPException(
            status_code=400, detail={"error_code": "MFA_SECRET_INVALID"}
        )

    try:
        redis_client = get_async_redis_client()
    except Exception:
        redis_client = None

    try:
        valid_totp = await verify_totp_code_once(
            principal.provider_id,
            secret,
            code,
            redis_client=redis_client,
        )
    except Exception:
        valid_totp = False

    if not valid_totp:
        raise HTTPException(status_code=401, detail={"error_code": "INVALID_MFA_CODE"})

    # Revalidate that the session is still active and bound in Redis
    current_session = await resolve_provider_session_context(principal.session_token)
    if (
        current_session is None
        or current_session.get("authenticated") is not True
        or str(current_session.get("provider_id")) != str(principal.provider_id)
    ):
        raise HTTPException(
            status_code=401, detail={"error_code": "PROVIDER_SESSION_INVALID"}
        )
    try:
        sess_exp = datetime.fromisoformat(str(current_session["expires_at"]))
        if (
            sess_exp.tzinfo is None
            or sess_exp.utcoffset() is None
            or sess_exp <= datetime.now(timezone.utc)
        ):
            raise HTTPException(
                status_code=401,
                detail={"error_code": "PROVIDER_SESSION_INVALID"},
            )
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=401, detail={"error_code": "PROVIDER_SESSION_INVALID"}
        )

    # Stage / append audit event BEFORE updating session MFA timestamp
    await append_audit_log_or_503(
        audit_context=AuditContext.platform(domain=AuditDomain.AUTH),
        actor_uid=identity.provider_uid,
        event_type="PROVIDER_STEP_UP_MFA_VERIFIED",
        target_id=str(principal.provider_id),
        status="SUCCESS",
        metadata={
            "assurance": "totp",
            "session_bound": True,
            "purpose": "privileged_action",
        },
    )

    # Only after audit success, refresh mfa_verified_at on the exact same session
    refreshed = await mark_provider_session_mfa_verified(
        principal.session_token, principal.provider_id
    )
    if not refreshed:
        raise HTTPException(
            status_code=401,
            detail={"error_code": "PROVIDER_SESSION_INVALID"},
        )

    return {"verified": True}

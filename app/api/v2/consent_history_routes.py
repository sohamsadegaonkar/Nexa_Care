from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_otp_rate_limit_config
from app.core.database import get_db_session
from app.core.dependencies import get_provider_context, get_scoped_session
from app.core.redis import get_async_redis_client
from app.models.consent_grant import ConsentGrantLog
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.approved_access_capability import invalidate_request
from app.services.clinical_access_session_store import (
    revoke_by_request as revoke_clinical_access_session_by_request,
)
from app.services.treatment_session_v1_mint import (
    TREATMENT_SESSION_V1_SCOPE,
    invalidate_treatment_session_v1_request,
)

router = APIRouter(prefix="/api/v2/consent", tags=["consent-history"])


def _get_grant_ref_secret() -> bytes:
    return get_otp_rate_limit_config().hmac_secret.encode("utf-8")


def mint_public_grant_ref(patient_id: str, grant_id: UUID) -> str:
    """Generate a server-owned, HMAC-signed public reference for a patient's grant.

    This binds the grant ID to the patient ID cryptographically, concealing the internal
    database UUID and preventing cross-patient grant selection.
    """
    secret = _get_grant_ref_secret()
    tag = hmac.new(
        secret,
        f"patient_grant_ref_v1:{patient_id}:{grant_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"gref_{grant_id.hex}_{tag}"


def parse_and_verify_public_grant_ref(public_ref: str, expected_patient_id: str) -> UUID:
    """Validate public grant reference and extract grant ID, failing closed on mismatch."""
    parts = public_ref.split("_")
    if len(parts) != 3 or parts[0] != "gref" or len(parts[1]) != 32 or len(parts[2]) != 32:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "GRANT_NOT_FOUND"},
        )
    try:
        grant_id = UUID(hex=parts[1])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "GRANT_NOT_FOUND"},
        ) from exc

    secret = _get_grant_ref_secret()
    expected_tag = hmac.new(
        secret,
        f"patient_grant_ref_v1:{expected_patient_id}:{grant_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    if not secrets.compare_digest(parts[2], expected_tag):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "GRANT_NOT_FOUND"},
        )
    return grant_id


class ConsentHistoryItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    public_ref: str
    patient_id: str
    purpose: str
    status: str
    scope: list[str]
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    type: str
    is_treatment_session: bool = False


class ConsentSelfRevokeResponsePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    public_ref: str
    status: Literal["revoked"] = "revoked"
    revoked_at: str


def _serialize_history(
    rows: list[ConsentGrantLog], patient_id: str | None = None
) -> list[ConsentHistoryItem]:
    now = datetime.now(timezone.utc)
    result: list[ConsentHistoryItem] = []
    for row in rows:
        status_value = (
            "revoked"
            if row.revoked_at
            else ("expired" if row.expires_at <= now else "active")
        )
        is_treatment = any(
            s in (TREATMENT_SESSION_V1_SCOPE, "treatment", "treatment.session.v1")
            for s in row.scope
        )
        if patient_id:
            ref = mint_public_grant_ref(patient_id=patient_id, grant_id=row.id)
            item_id = ref
        else:
            ref = ""
            item_id = str(row.id)

        result.append(
            ConsentHistoryItem(
                id=item_id,
                public_ref=ref,
                patient_id=row.patient_id,
                purpose=row.purpose,
                status=status_value,
                scope=list(row.scope),
                issued_at=row.issued_at,
                expires_at=row.expires_at,
                revoked_at=row.revoked_at,
                type="break-glass" if row.is_break_glass else "routine",
                is_treatment_session=is_treatment,
            )
        )
    return result


@router.get("/history/self", response_model=list[ConsentHistoryItem])
async def get_self_consent_history(
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        canonical_id = str(UUID(patient_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error_code": "INVALID_PATIENT_ID"}
        ) from exc
    rows = (
        (
            await db.execute(
                select(ConsentGrantLog)
                .where(ConsentGrantLog.patient_id == canonical_id)
                .order_by(ConsentGrantLog.issued_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return _serialize_history(rows, patient_id=canonical_id)


@router.delete(
    "/history/self/{public_ref}",
    status_code=status.HTTP_200_OK,
    response_model=ConsentSelfRevokeResponsePayload,
)
async def revoke_self_consent_grant(
    public_ref: str,
    patient_id: str = Depends(get_scoped_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Revoke one consent or treatment grant owned by the authenticated patient.

    The public_ref is cryptographically bound to the authenticated patient session.
    Revocation immediately invalidates:
    - Durable ConsentGrantLog (marked revoked)
    - Durable ClinicalAccessSessionRecord (marked REVOKED)
    - Live Redis capability (treatment and/or standard consent)
    - Subsequent clinical writes fail at the gate
    """
    try:
        canonical_id = str(UUID(patient_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error_code": "INVALID_PATIENT_ID"}
        ) from exc

    grant_id = parse_and_verify_public_grant_ref(public_ref, canonical_id)

    grant = (
        await db.execute(
            select(ConsentGrantLog)
            .where(
                ConsentGrantLog.id == grant_id,
                ConsentGrantLog.patient_id == canonical_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()

    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "GRANT_NOT_FOUND"},
        )

    if grant.revoked_at is not None:
        return ConsentSelfRevokeResponsePayload(
            public_ref=public_ref,
            status="revoked",
            revoked_at=grant.revoked_at.isoformat(),
        )

    now = datetime.now(timezone.utc)
    grant.revoked_at = now
    grant.revoked_reason = "patient_revoked"

    if grant.request_id:
        req_id = grant.request_id
        # Revoke durable clinical access session
        await revoke_clinical_access_session_by_request(
            db,
            consent_request_id=req_id,
            reason="PATIENT_REVOKED",
            revoked_at=now,
        )

        is_treatment = any(
            s in (TREATMENT_SESSION_V1_SCOPE, "treatment", "treatment.session.v1")
            for s in grant.scope
        )
        if is_treatment:
            try:
                await invalidate_treatment_session_v1_request(req_id)
            except Exception:
                pass
            try:
                redis = get_async_redis_client()
                raw = await redis.get(f"treatment_session_request:{req_id}")
                if raw:
                    data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                    data["status"] = "revoked"
                    data["revoked_at"] = now.isoformat()
                    await redis.set(
                        f"treatment_session_request:{req_id}",
                        json.dumps(data, sort_keys=True),
                        ex=300,
                    )
            except Exception:
                pass

        try:
            await invalidate_request(req_id)
        except Exception:
            pass

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.CONSENT),
        actor_uid=canonical_id,
        event_type="PATIENT_CONSENT_REVOKED",
        target_id=public_ref,
        status="SUCCESS",
        metadata={
            "patient_id": canonical_id,
            "request_id": grant.request_id,
            "is_treatment_session": any(
                s in (TREATMENT_SESSION_V1_SCOPE, "treatment", "treatment.session.v1")
                for s in grant.scope
            ),
            "provider_id": grant.clinician_id,
            "hospital_id": str(grant.hospital_id) if grant.hospital_id else None,
        },
    )

    await db.commit()

    return ConsentSelfRevokeResponsePayload(
        public_ref=public_ref,
        status="revoked",
        revoked_at=now.isoformat(),
    )


@router.get("/history", response_model=list[ConsentHistoryItem])
async def get_consent_history(
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
):
    roles = set(provider.affiliation.roles or [])
    stmt = select(ConsentGrantLog).where(
        ConsentGrantLog.hospital_id == provider.hospital_id
    )
    if not roles.intersection({"admin", "privacy_officer", "auditor"}):
        stmt = stmt.where(ConsentGrantLog.clinician_id == provider.actor_uid)
    rows = (
        (await db.execute(stmt.order_by(ConsentGrantLog.issued_at.desc())))
        .scalars()
        .all()
    )
    return _serialize_history(rows)

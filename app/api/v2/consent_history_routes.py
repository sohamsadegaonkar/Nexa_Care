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

from app.core.config import get_patient_grant_reference_config
from app.core.database import get_db_session
from app.core.dependencies import get_provider_context, get_scoped_session
from app.core.redis import get_async_redis_client
from app.models.consent_grant import ConsentGrantLog
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.approved_access_capability import (
    ApprovedAccessStoreUnavailable,
    invalidate_request,
)
from app.services.clinical_access_session_store import (
    revoke_by_request as revoke_clinical_access_session_by_request,
)
from app.services.treatment_session_v1_mint import (
    TREATMENT_SESSION_V1_SCOPE,
    TreatmentSessionV1MintStoreUnavailable,
    invalidate_treatment_session_v1_request,
)

router = APIRouter(prefix="/api/v2/consent", tags=["consent-history"])


def _get_grant_ref_secret() -> bytes:
    return get_patient_grant_reference_config().hmac_secret.encode("utf-8")


def mint_public_grant_ref(patient_id: str, grant_id: UUID) -> str:
    """Generate a server-owned, HMAC-derived opaque public reference for a patient's grant.

    The token is entirely opaque (gref_v2_<64-hex-digest>) and contains NO internal UUID or database
    primary key information. It is deterministically derived from (patient_id, grant_id)
    using the dedicated PATIENT_GRANT_REFERENCE_HMAC_SECRET.
    """
    secret = _get_grant_ref_secret()
    tag = hmac.new(
        secret,
        f"patient_grant_ref_v2:{patient_id}:{grant_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"gref_v2_{tag}"


def verify_public_grant_ref_format(public_ref: str) -> bool:
    """Check that public_ref is well-formed gref_v2_<64_hex>."""
    if not (
        isinstance(public_ref, str)
        and public_ref.startswith("gref_v2_")
        and len(public_ref) == 72
    ):
        return False
    hex_part = public_ref[8:]
    return len(hex_part) == 64 and all(c in "0123456789abcdef" for c in hex_part)


async def find_grant_by_public_ref(
    db: AsyncSession,
    public_ref: str,
    canonical_id: str,
    *,
    for_update: bool = False,
) -> ConsentGrantLog:
    """Find a grant by its opaque public reference within the authenticated patient's grants.

    Rejects v1 references and malformed references with 404.
    Performs constant-time comparison across all grants owned by canonical_id.
    """
    if not verify_public_grant_ref_format(public_ref):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "GRANT_NOT_FOUND"},
        )

    # Scoped strictly to authenticated patient
    grants = (
        (
            await db.execute(
                select(ConsentGrantLog).where(
                    ConsentGrantLog.patient_id == canonical_id
                )
            )
        )
        .scalars()
        .all()
    )

    matched_id: UUID | None = None
    for grant in grants:
        candidate_ref = mint_public_grant_ref(canonical_id, grant.id)
        if secrets.compare_digest(public_ref, candidate_ref):
            matched_id = grant.id

    if matched_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "GRANT_NOT_FOUND"},
        )

    if for_update:
        grant = (
            await db.execute(
                select(ConsentGrantLog)
                .where(
                    ConsentGrantLog.id == matched_id,
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
        return grant

    return next(g for g in grants if g.id == matched_id)


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


class PatientConsentHistoryItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    public_ref: str
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


def _serialize_provider_history(
    rows: list[ConsentGrantLog],
) -> list[ConsentHistoryItem]:
    now = datetime.now(timezone.utc)
    result: list[ConsentHistoryItem] = []
    for row in rows:
        status_value = (
            "revoked"
            if row.revoked_at
            else ("expired" if row.expires_at <= now else "active")
        )
        is_treatment = TREATMENT_SESSION_V1_SCOPE in row.scope
        result.append(
            ConsentHistoryItem(
                id=str(row.id),
                public_ref="",
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


def _serialize_patient_history(
    rows: list[ConsentGrantLog],
    patient_id: str,
) -> list[PatientConsentHistoryItem]:
    now = datetime.now(timezone.utc)
    result: list[PatientConsentHistoryItem] = []
    for row in rows:
        status_value = (
            "revoked"
            if row.revoked_at
            else ("expired" if row.expires_at <= now else "active")
        )
        is_treatment = TREATMENT_SESSION_V1_SCOPE in row.scope
        ref = mint_public_grant_ref(patient_id=patient_id, grant_id=row.id)
        result.append(
            PatientConsentHistoryItem(
                id=ref,
                public_ref=ref,
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


@router.get("/history/self", response_model=list[PatientConsentHistoryItem])
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
    return _serialize_patient_history(rows, patient_id=canonical_id)


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
    - Live Redis capability (treatment and/or standard consent) - must succeed first
    - Durable ConsentGrantLog (marked revoked)
    - Durable ClinicalAccessSessionRecord (marked REVOKED)
    - Subsequent clinical writes fail at the gate
    """
    try:
        canonical_id = str(UUID(patient_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"error_code": "INVALID_PATIENT_ID"}
        ) from exc

    grant = await find_grant_by_public_ref(
        db, public_ref, canonical_id, for_update=True
    )

    if grant.revoked_at is not None:
        return ConsentSelfRevokeResponsePayload(
            public_ref=public_ref,
            status="revoked",
            revoked_at=grant.revoked_at.isoformat(),
        )

    now = datetime.now(timezone.utc)

    # 1. Redis capability invalidation must succeed first.
    # If Redis fails, return deterministic HTTP 503 and do not commit PostgreSQL.
    if grant.request_id:
        req_id = grant.request_id
        is_treatment = TREATMENT_SESSION_V1_SCOPE in grant.scope
        try:
            if is_treatment:
                await invalidate_treatment_session_v1_request(req_id)
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
            await invalidate_request(req_id)
        except (
            ApprovedAccessStoreUnavailable,
            TreatmentSessionV1MintStoreUnavailable,
            Exception,
        ) as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error_code": "CONSENT_ACCESS_STORE_UNAVAILABLE"},
            ) from exc

    # 2. Redis invalidation succeeded; now update durable PostgreSQL records.
    grant.revoked_at = now
    grant.revoked_reason = "patient_revoked"

    if grant.request_id:
        await revoke_clinical_access_session_by_request(
            db,
            consent_request_id=grant.request_id,
            reason="PATIENT_REVOKED",
            revoked_at=now,
        )

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.CONSENT),
        actor_uid=canonical_id,
        event_type="PATIENT_CONSENT_REVOKED",
        target_id=public_ref,
        status="SUCCESS",
        metadata={
            "patient_id": canonical_id,
            "request_id": grant.request_id,
            "is_treatment_session": TREATMENT_SESSION_V1_SCOPE in grant.scope,
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
    return _serialize_provider_history(rows)

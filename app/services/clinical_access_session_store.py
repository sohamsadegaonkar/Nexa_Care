"""Durable lifecycle helpers for bounded clinical access sessions."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clinical_access_session import ClinicalAccessSessionRecord
from app.security.clinical_access_policy import CLINICAL_ACCESS_POLICY_VERSION


ACTIVE = "ACTIVE"
REVOKED = "REVOKED"
_ALLOWED_REVOCATION_REASONS = frozenset(
    {
        "PATIENT_REVOKED",
        "PROVIDER_TRUST_LOST",
        "PROVIDER_SESSION_ENDED",
        "PATIENT_MERGED",
        "PATIENT_DELETED",
        "PATIENT_ERASED",
        "CLAIM_FINALIZATION_FAILED",
        "CAPABILITY_INVALIDATED",
        "ADMINISTRATIVE",
    }
)


class ClinicalAccessSessionStoreError(RuntimeError):
    """Durable clinical-access authority cannot be safely established or read."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _uuid(value: str | uuid.UUID, *, code: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ClinicalAccessSessionStoreError(code) from exc


def _hash64(value: str | None, *, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ClinicalAccessSessionStoreError(code)
    if any(character not in "0123456789abcdef" for character in value):
        raise ClinicalAccessSessionStoreError(code)
    return value


async def stage_session(
    db: AsyncSession,
    *,
    session_id: str,
    patient_id: str,
    provider_id: str,
    hospital_id: str,
    consent_request_id: str,
    token_hash: str,
    purpose: str,
    scope: str,
    allowed_operations: tuple[str, ...],
    provider_session_binding_hash: str,
    policy_version: str,
    issued_at: datetime,
    expires_at: datetime,
) -> ClinicalAccessSessionRecord:
    """Stage one durable session in the caller's current database transaction."""

    if policy_version != CLINICAL_ACCESS_POLICY_VERSION:
        raise ClinicalAccessSessionStoreError("CLINICAL_ACCESS_POLICY_MISMATCH")
    if allowed_operations != ("READ_CLINICAL_HISTORY",):
        raise ClinicalAccessSessionStoreError("CLINICAL_ACCESS_OPERATIONS_INVALID")
    if scope not in {"clinical", "full"}:
        raise ClinicalAccessSessionStoreError("CLINICAL_ACCESS_SCOPE_INVALID")
    if not consent_request_id or len(consent_request_id) > 64:
        raise ClinicalAccessSessionStoreError("CLINICAL_ACCESS_REQUEST_INVALID")
    if not purpose or len(purpose) > 64:
        raise ClinicalAccessSessionStoreError("CLINICAL_ACCESS_PURPOSE_INVALID")
    if issued_at.tzinfo is None or expires_at.tzinfo is None or expires_at <= issued_at:
        raise ClinicalAccessSessionStoreError("CLINICAL_ACCESS_LIFETIME_INVALID")

    record = ClinicalAccessSessionRecord(
        session_id=_uuid(session_id, code="CLINICAL_ACCESS_SESSION_ID_INVALID"),
        patient_id=_uuid(patient_id, code="CLINICAL_ACCESS_PATIENT_INVALID"),
        provider_id=_uuid(provider_id, code="CLINICAL_ACCESS_PROVIDER_INVALID"),
        hospital_id=_uuid(hospital_id, code="CLINICAL_ACCESS_HOSPITAL_INVALID"),
        consent_request_id=consent_request_id,
        token_hash=_hash64(token_hash, code="CLINICAL_ACCESS_TOKEN_HASH_INVALID"),
        purpose=purpose,
        scope=scope,
        allowed_operations=list(allowed_operations),
        provider_session_binding_hash=_hash64(
            provider_session_binding_hash,
            code="CLINICAL_ACCESS_SESSION_BINDING_INVALID",
        ),
        policy_version=policy_version,
        issued_at=issued_at,
        expires_at=expires_at,
        status=ACTIVE,
        encounter_id=None,
        revoked_at=None,
        revocation_reason=None,
    )
    db.add(record)
    await db.flush()
    return record


async def find_by_request(
    db: AsyncSession, consent_request_id: str, *, for_update: bool = False
) -> ClinicalAccessSessionRecord | None:
    statement = select(ClinicalAccessSessionRecord).where(
        ClinicalAccessSessionRecord.consent_request_id == consent_request_id
    )
    if for_update:
        statement = statement.with_for_update()
    return (await db.execute(statement)).scalar_one_or_none()


async def validate_active_session(
    db: AsyncSession,
    *,
    session_id: str,
    token_hash: str,
    patient_id: str,
    provider_id: str,
    hospital_id: str,
    consent_request_id: str,
    provider_session_binding_hash: str,
    allowed_operations: tuple[str, ...],
    policy_version: str,
    now: datetime | None = None,
) -> bool:
    """Fail closed unless Redis capability and durable authority agree exactly."""

    try:
        sid = _uuid(session_id, code="CLINICAL_ACCESS_SESSION_ID_INVALID")
        pid = _uuid(patient_id, code="CLINICAL_ACCESS_PATIENT_INVALID")
        clinician = _uuid(provider_id, code="CLINICAL_ACCESS_PROVIDER_INVALID")
        hospital = _uuid(hospital_id, code="CLINICAL_ACCESS_HOSPITAL_INVALID")
        digest = _hash64(token_hash, code="CLINICAL_ACCESS_TOKEN_HASH_INVALID")
        binding_hash = _hash64(
            provider_session_binding_hash,
            code="CLINICAL_ACCESS_SESSION_BINDING_INVALID",
        )
    except ClinicalAccessSessionStoreError:
        return False

    row = (
        await db.execute(
            select(ClinicalAccessSessionRecord).where(
                ClinicalAccessSessionRecord.session_id == sid
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if (
        row.status != ACTIVE
        or row.revoked_at is not None
        or current >= row.expires_at
        or row.patient_id != pid
        or row.provider_id != clinician
        or row.hospital_id != hospital
        or row.consent_request_id != consent_request_id
        or row.policy_version != policy_version
        or tuple(row.allowed_operations) != allowed_operations
    ):
        return False
    return secrets.compare_digest(row.token_hash, digest) and secrets.compare_digest(
        row.provider_session_binding_hash, binding_hash
    )


async def revoke_by_request(
    db: AsyncSession,
    *,
    consent_request_id: str,
    reason: str,
    revoked_at: datetime | None = None,
) -> int:
    if reason not in _ALLOWED_REVOCATION_REASONS:
        raise ClinicalAccessSessionStoreError("CLINICAL_ACCESS_REVOCATION_REASON_INVALID")
    rows = (
        (
            await db.execute(
                select(ClinicalAccessSessionRecord)
                .where(
                    ClinicalAccessSessionRecord.consent_request_id == consent_request_id
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    when = revoked_at or datetime.now(timezone.utc)
    changed = 0
    for row in rows:
        if row.status == ACTIVE and row.revoked_at is None:
            row.status = REVOKED
            row.revoked_at = when
            row.revocation_reason = reason
            changed += 1
    return changed


async def revoke_by_patient(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    reason: str,
    revoked_at: datetime | None = None,
) -> list[str]:
    """Revoke every active durable session for a patient and return request IDs."""

    if reason not in _ALLOWED_REVOCATION_REASONS:
        raise ClinicalAccessSessionStoreError("CLINICAL_ACCESS_REVOCATION_REASON_INVALID")
    rows = (
        (
            await db.execute(
                select(ClinicalAccessSessionRecord)
                .where(
                    ClinicalAccessSessionRecord.patient_id == patient_id,
                    ClinicalAccessSessionRecord.status == ACTIVE,
                    ClinicalAccessSessionRecord.revoked_at.is_(None),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    when = revoked_at or datetime.now(timezone.utc)
    request_ids: list[str] = []
    for row in rows:
        row.status = REVOKED
        row.revoked_at = when
        row.revocation_reason = reason
        request_ids.append(row.consent_request_id)
    return request_ids

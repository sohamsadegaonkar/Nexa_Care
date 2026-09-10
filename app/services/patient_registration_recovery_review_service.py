"""Durable manual-review case creation for verified registration recovery.

Only the already-verified patient-facing recovery classifier may call this
service. Case creation records authority metadata only and never upgrades the
patient to login, device, consent, or provider authority.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_registration_recovery_review import (
    REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
    REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
    PatientRegistrationRecoveryReviewCase,
    RegistrationRecoveryReviewReason,
    RegistrationRecoveryReviewStatus,
)
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event
from app.services.patient_registration_recovery_service import (
    RegistrationRecoveryInspection,
)

_REVIEW_OPENED_EVENT = "PATIENT_REGISTRATION_RECOVERY_REVIEW_OPENED"
_PROVIDER = "supabase"

_REASON_MAP: dict[str, RegistrationRecoveryReviewReason] = {
    "PATIENT_RECORD_ANCHOR_MISSING": RegistrationRecoveryReviewReason.MISSING_RECORD_ANCHOR,
    "MERGED_IDENTITY_RECONCILIATION_REQUIRED": RegistrationRecoveryReviewReason.MERGED_IDENTITY_REBIND_REQUIRED,
    "IDENTITY_REVOKED": RegistrationRecoveryReviewReason.IDENTITY_REVOKED,
    "DELETED_PATIENT_WITHOUT_MERGE_TOMBSTONE": RegistrationRecoveryReviewReason.PATIENT_DELETED_WITHOUT_MERGE,
    "ERASURE_STATE_PRESENT": RegistrationRecoveryReviewReason.ERASURE_STATE_PRESENT,
    "CANONICAL_ERASURE_STATE_PRESENT": RegistrationRecoveryReviewReason.ERASURE_STATE_PRESENT,
    "MULTIPLE_SOURCE_IDENTITIES": RegistrationRecoveryReviewReason.MULTIPLE_IDENTITIES,
    "CANONICAL_IDENTITY_CONFLICT": RegistrationRecoveryReviewReason.MULTIPLE_IDENTITIES,
    "MERGE_TOMBSTONE_CYCLE": RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS,
    "MERGE_TOMBSTONE_CHAIN_TOO_DEEP": RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS,
    "CANONICAL_PATIENT_UNAVAILABLE": RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS,
    "LINKED_PATIENT_MISSING": RegistrationRecoveryReviewReason.SECURITY_CONCERN,
}


class RegistrationRecoveryReviewCaseError(RuntimeError):
    """Stable case-creation failure without authority-bearing detail."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RegistrationRecoveryReviewCaseResult:
    case_id: str
    case_reference: str
    status: str
    version: int
    created: bool


def _provider_subject_hash(provider_subject: str) -> str:
    canonical = f"registration-recovery-review-subject:v1:{provider_subject.strip()}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_reasons(reason_code: str | None) -> list[str]:
    if not reason_code:
        return [RegistrationRecoveryReviewReason.SECURITY_CONCERN.value]
    reason = _REASON_MAP.get(
        reason_code,
        RegistrationRecoveryReviewReason.SECURITY_CONCERN,
    )
    return [reason.value]


def _creation_idempotency_key(*, subject_hash: str, graph_fingerprint: str) -> str:
    return f"rr-review-create:{subject_hash}:{graph_fingerprint}"


def _operation_hash(
    *,
    subject_hash: str,
    patient_id: str | None,
    graph_fingerprint: str,
    reason_codes: list[str],
) -> str:
    payload = {
        "contract_version": REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
        "graph_fingerprint": graph_fingerprint,
        "patient_id": patient_id,
        "policy_version": REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
        "provider": _PROVIDER,
        "provider_subject_hash": subject_hash,
        "reason_codes": sorted(reason_codes),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _existing_case(
    db: AsyncSession,
    *,
    subject_hash: str,
    graph_fingerprint: str,
) -> PatientRegistrationRecoveryReviewCase | None:
    return await db.scalar(
        select(PatientRegistrationRecoveryReviewCase).where(
            PatientRegistrationRecoveryReviewCase.provider == _PROVIDER,
            PatientRegistrationRecoveryReviewCase.provider_subject_hash == subject_hash,
            PatientRegistrationRecoveryReviewCase.graph_fingerprint == graph_fingerprint,
        )
    )


async def _candidate_patient_id(
    db: AsyncSession,
    inspection: RegistrationRecoveryInspection,
) -> uuid.UUID | None:
    try:
        candidate = uuid.UUID(inspection.target_patient_id)
    except (TypeError, ValueError):
        return None
    row = await db.get(Patient, candidate)
    return candidate if row is not None else None


async def create_registration_recovery_review_case(
    db: AsyncSession,
    *,
    inspection: RegistrationRecoveryInspection,
) -> RegistrationRecoveryReviewCaseResult:
    """Idempotently create one durable case for one verified graph fingerprint.

    The caller owns the outer transaction. The newly-created case and its
    OPENED audit event are staged together. Concurrent duplicate inserts are
    linearized by database uniqueness without rolling back unrelated outer work.
    """

    if inspection.disposition != "manual_review":
        raise RegistrationRecoveryReviewCaseError(
            "REGISTRATION_RECOVERY_REVIEW_NOT_REQUIRED"
        )
    provider_subject = inspection.provider_subject.strip()
    if not provider_subject or len(provider_subject) > 255:
        raise RegistrationRecoveryReviewCaseError(
            "REGISTRATION_RECOVERY_REVIEW_CLASSIFICATION_INVALID"
        )
    if len(inspection.graph_fingerprint) != 64:
        raise RegistrationRecoveryReviewCaseError(
            "REGISTRATION_RECOVERY_REVIEW_CLASSIFICATION_INVALID"
        )

    subject_hash = _provider_subject_hash(provider_subject)
    reason_codes = _normalize_reasons(inspection.reason_code)
    patient_uuid = await _candidate_patient_id(db, inspection)
    patient_id = str(patient_uuid) if patient_uuid is not None else None
    idempotency_key = _creation_idempotency_key(
        subject_hash=subject_hash,
        graph_fingerprint=inspection.graph_fingerprint,
    )

    existing = await _existing_case(
        db,
        subject_hash=subject_hash,
        graph_fingerprint=inspection.graph_fingerprint,
    )
    if existing is not None:
        return RegistrationRecoveryReviewCaseResult(
            case_id=str(existing.id),
            case_reference=existing.case_reference,
            status=existing.status,
            version=existing.version,
            created=False,
        )

    now = datetime.now(timezone.utc)
    row = PatientRegistrationRecoveryReviewCase(
        case_reference=f"RRV-{secrets.token_hex(8).upper()}",
        provider=_PROVIDER,
        provider_subject_hash=subject_hash,
        patient_id=patient_uuid,
        graph_fingerprint=inspection.graph_fingerprint,
        reason_codes=reason_codes,
        status=RegistrationRecoveryReviewStatus.PENDING.value,
        version=1,
        assigned_reviewer_id=None,
        assigned_reviewer_role=None,
        reviewer_authority_version=None,
        review_session_binding=None,
        creation_idempotency_key=idempotency_key,
        creation_operation_hash=_operation_hash(
            subject_hash=subject_hash,
            patient_id=patient_id,
            graph_fingerprint=inspection.graph_fingerprint,
            reason_codes=reason_codes,
        ),
        contract_version=REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
        policy_version=REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
        created_at=now,
        claimed_at=None,
        resolved_at=None,
    )

    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        existing = await _existing_case(
            db,
            subject_hash=subject_hash,
            graph_fingerprint=inspection.graph_fingerprint,
        )
        if existing is None:
            raise RegistrationRecoveryReviewCaseError(
                "REGISTRATION_RECOVERY_REVIEW_CREATE_CONFLICT"
            ) from None
        return RegistrationRecoveryReviewCaseResult(
            case_id=str(existing.id),
            case_reference=existing.case_reference,
            status=existing.status,
            version=existing.version,
            created=False,
        )

    await enqueue_audit_event(
        db,
        audit_context=current_audit_context(AuditDomain.AUTH),
        idempotency_key=f"registration-recovery-review-opened:{idempotency_key}",
        actor_id="PATIENT_REGISTRATION_RECOVERY",
        event_type=_REVIEW_OPENED_EVENT,
        target_id=row.case_reference,
        patient_id=patient_id,
        status="PENDING",
        metadata={
            "case_reference": row.case_reference,
            "reason_codes": reason_codes,
            "contract_version": REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
            "policy_version": REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
        },
    )

    return RegistrationRecoveryReviewCaseResult(
        case_id=str(row.id),
        case_reference=row.case_reference,
        status=row.status,
        version=row.version,
        created=True,
    )


__all__ = [
    "RegistrationRecoveryReviewCaseError",
    "RegistrationRecoveryReviewCaseResult",
    "create_registration_recovery_review_case",
]

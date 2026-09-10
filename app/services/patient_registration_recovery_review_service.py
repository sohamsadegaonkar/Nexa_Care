"""Durable case creation for verified registration-recovery manual review.

This service may be entered only after the patient-facing recovery flow has
verified the external Supabase identity and classified the current registration
graph as requiring manual review. It stores a stable auth-identity UUID plus a
one-way provider-subject hash; the raw provider subject is never persisted in
the review case or audit metadata.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_registration_recovery_review import (
    REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
    REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
    PatientRegistrationRecoveryReviewCase,
    RegistrationRecoveryReviewReason,
    RegistrationRecoveryReviewStatus,
)
from app.security.audit_context import AuditContext, AuditDomain
from app.services.audit_outbox import enqueue_audit_event
from app.services.patient_registration_recovery_service import (
    SUPABASE_PROVIDER,
    RegistrationRecoveryInspection,
)

REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT = (
    "REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT"
)
REGISTRATION_RECOVERY_REVIEW_CASE_INVALID_ORIGIN = (
    "REGISTRATION_RECOVERY_REVIEW_CASE_INVALID_ORIGIN"
)

_REASON_MAP: dict[str, RegistrationRecoveryReviewReason] = {
    "ERASURE_STATE_PRESENT": RegistrationRecoveryReviewReason.ERASURE_STATE_PRESENT,
    "CANONICAL_ERASURE_STATE_PRESENT": RegistrationRecoveryReviewReason.ERASURE_STATE_PRESENT,
    "IDENTITY_REVOKED": RegistrationRecoveryReviewReason.IDENTITY_REVOKED,
    "MULTIPLE_SOURCE_IDENTITIES": RegistrationRecoveryReviewReason.MULTIPLE_IDENTITIES,
    "CANONICAL_IDENTITY_CONFLICT": RegistrationRecoveryReviewReason.MULTIPLE_IDENTITIES,
    "DELETED_PATIENT_WITHOUT_MERGE_TOMBSTONE": RegistrationRecoveryReviewReason.PATIENT_DELETED_WITHOUT_MERGE,
    "MERGE_TOMBSTONE_CYCLE": RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS,
    "MERGE_TOMBSTONE_CHAIN_TOO_DEEP": RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS,
    "CANONICAL_PATIENT_UNAVAILABLE": RegistrationRecoveryReviewReason.MERGE_AMBIGUOUS,
    "LINKED_PATIENT_MISSING": RegistrationRecoveryReviewReason.SECURITY_CONCERN,
}


class PatientRegistrationRecoveryReviewError(RuntimeError):
    """Stable, value-free durable review-case failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def normalize_registration_recovery_review_reason(
    source_reason: str | None,
) -> RegistrationRecoveryReviewReason:
    """Map internal classifier detail to the closed durable review vocabulary."""

    if not source_reason:
        return RegistrationRecoveryReviewReason.SECURITY_CONCERN
    return _REASON_MAP.get(
        source_reason, RegistrationRecoveryReviewReason.SECURITY_CONCERN
    )


def provider_subject_hash(provider_subject: str) -> str:
    """Return a domain-separated one-way identifier for the verified subject."""

    return hashlib.sha256(
        (
            "registration-recovery-review:provider-subject:"
            f"{SUPABASE_PROVIDER}:{provider_subject}"
        ).encode("utf-8")
    ).hexdigest()


def _operation_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _creation_idempotency_key(attempt_id: str) -> str:
    digest = hashlib.sha256(
        f"registration-recovery-review:create:{attempt_id}".encode("utf-8")
    ).hexdigest()
    return f"registration-recovery-review:create:{digest}"


def _case_reference() -> str:
    # 96 bits of entropy, opaque and short enough for the 32-character column.
    return f"RRC-{secrets.token_hex(12).upper()}"


def _valid_graph_fingerprint(value: str) -> bool:
    if len(value) != 64:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _audit_context() -> AuditContext:
    return AuditContext.platform(domain=AuditDomain.AUTH)


def _assert_existing_matches(
    case: PatientRegistrationRecoveryReviewCase,
    *,
    identity_id: uuid.UUID,
    patient_id: uuid.UUID | None,
    subject_hash: str,
    graph_fingerprint: str,
    reason_code: str,
    operation_hash: str,
) -> None:
    """Reject any uniqueness collision that is not the exact durable graph case."""

    if (
        case.provider != SUPABASE_PROVIDER
        or case.provider_subject_hash != subject_hash
        or case.identity_id != identity_id
        or case.patient_id != patient_id
        or case.graph_fingerprint != graph_fingerprint
        or list(case.reason_codes or []) != [reason_code]
        or case.creation_operation_hash != operation_hash
        or case.contract_version != REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION
        or case.policy_version != REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION
    ):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT
        )


async def open_registration_recovery_review_case(
    db: AsyncSession,
    *,
    inspection: RegistrationRecoveryInspection,
    attempt_id: str,
) -> PatientRegistrationRecoveryReviewCase:
    """Open or return the exact durable case for a verified manual-review graph.

    The exact auth-identity row is locked first. This serializes concurrent case
    creation for one verified external identity and prevents the identity from
    being rebound between classification and durable anchoring.

    The caller owns the surrounding transaction and must commit the case together
    with the recovery-required and review-opened audit outbox events.
    """

    if (
        inspection.disposition != "manual_review"
        or not inspection.provider_subject
        or not attempt_id
        or not _valid_graph_fingerprint(inspection.graph_fingerprint)
    ):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_INVALID_ORIGIN
        )

    try:
        inspected_patient_id = uuid.UUID(str(inspection.patient_id))
    except (TypeError, ValueError) as exc:
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_INVALID_ORIGIN
        ) from exc

    identity = (
        await db.execute(
            select(PatientAuthIdentity)
            .where(
                PatientAuthIdentity.provider == SUPABASE_PROVIDER,
                PatientAuthIdentity.provider_subject == inspection.provider_subject,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        identity is None
        or identity.provider != SUPABASE_PROVIDER
        or identity.provider_subject != inspection.provider_subject
        or identity.patient_id != inspected_patient_id
    ):
        raise PatientRegistrationRecoveryReviewError(
            REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT
        )

    current_patient = await db.get(Patient, inspected_patient_id)
    stored_patient_id = inspected_patient_id if current_patient is not None else None
    normalized_reason = normalize_registration_recovery_review_reason(
        inspection.reason_code
    ).value
    subject_hash = provider_subject_hash(inspection.provider_subject)
    operation_payload = {
        "contract_version": REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
        "graph_fingerprint": inspection.graph_fingerprint,
        "identity_id": str(identity.identity_id),
        "patient_id": str(stored_patient_id) if stored_patient_id else None,
        "policy_version": REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
        "provider": SUPABASE_PROVIDER,
        "provider_subject_hash": subject_hash,
        "reason_codes": [normalized_reason],
    }
    operation_hash = _operation_hash(operation_payload)
    idempotency_key = _creation_idempotency_key(attempt_id)

    keyed = (
        await db.execute(
            select(PatientRegistrationRecoveryReviewCase).where(
                PatientRegistrationRecoveryReviewCase.creation_idempotency_key
                == idempotency_key
            )
        )
    ).scalar_one_or_none()
    if keyed is not None:
        _assert_existing_matches(
            keyed,
            identity_id=identity.identity_id,
            patient_id=stored_patient_id,
            subject_hash=subject_hash,
            graph_fingerprint=inspection.graph_fingerprint,
            reason_code=normalized_reason,
            operation_hash=operation_hash,
        )
        case = keyed
    else:
        existing = (
            await db.execute(
                select(PatientRegistrationRecoveryReviewCase).where(
                    PatientRegistrationRecoveryReviewCase.provider
                    == SUPABASE_PROVIDER,
                    PatientRegistrationRecoveryReviewCase.provider_subject_hash
                    == subject_hash,
                    PatientRegistrationRecoveryReviewCase.graph_fingerprint
                    == inspection.graph_fingerprint,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            _assert_existing_matches(
                existing,
                identity_id=identity.identity_id,
                patient_id=stored_patient_id,
                subject_hash=subject_hash,
                graph_fingerprint=inspection.graph_fingerprint,
                reason_code=normalized_reason,
                operation_hash=operation_hash,
            )
            case = existing
        else:
            now = datetime.now(timezone.utc)
            case = PatientRegistrationRecoveryReviewCase(
                case_reference=_case_reference(),
                provider=SUPABASE_PROVIDER,
                provider_subject_hash=subject_hash,
                identity_id=identity.identity_id,
                patient_id=stored_patient_id,
                graph_fingerprint=inspection.graph_fingerprint,
                reason_codes=[normalized_reason],
                status=RegistrationRecoveryReviewStatus.PENDING.value,
                version=1,
                assigned_reviewer_id=None,
                assigned_reviewer_role=None,
                reviewer_authority_version=None,
                review_session_binding=None,
                creation_idempotency_key=idempotency_key,
                creation_operation_hash=operation_hash,
                contract_version=REGISTRATION_RECOVERY_REVIEW_CONTRACT_VERSION,
                policy_version=REGISTRATION_RECOVERY_REVIEW_POLICY_VERSION,
                created_at=now,
                claimed_at=None,
                resolved_at=None,
            )
            db.add(case)
            await db.flush()

    await enqueue_audit_event(
        db,
        audit_context=_audit_context(),
        idempotency_key=f"registration-recovery-review:{case.id}:opened",
        actor_id="PATIENT_REGISTRATION_RECOVERY",
        event_type="PATIENT_REGISTRATION_RECOVERY_REVIEW_OPENED",
        target_id=case.case_reference,
        patient_id=str(case.patient_id) if case.patient_id is not None else None,
        status="PENDING",
        metadata={
            "case_reference": case.case_reference,
            "reason_codes": list(case.reason_codes),
            "status": case.status,
            "contract_version": case.contract_version,
            "policy_version": case.policy_version,
        },
    )
    return case


__all__ = [
    "REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT",
    "REGISTRATION_RECOVERY_REVIEW_CASE_INVALID_ORIGIN",
    "PatientRegistrationRecoveryReviewError",
    "normalize_registration_recovery_review_reason",
    "open_registration_recovery_review_case",
    "provider_subject_hash",
]

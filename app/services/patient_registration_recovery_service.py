"""Patient-registration graph inspection and narrowly bounded self-service repair.

Fresh Supabase OTP control is necessary but intentionally not sufficient to
resurrect arbitrary historical state. Automatic repair is limited to mutations
whose provenance is unambiguous from Nexa's durable graph:

* restore a missing, data-free ``PatientRecord`` anchor for one active patient;
* rebind one non-revoked Supabase identity from a merge tombstone to the single
  active canonical patient, optionally restoring that canonical record anchor.

Revoked identities, erasure state, unexplained soft deletion, missing patients,
multiple Supabase identities, merge cycles/chains with ambiguity, or canonical
identity conflicts require manual review. Recovery never clears ``revoked_at``
and never undeletes a patient.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.erasure_tombstone import PatientErasureTombstone
from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_records import PatientRecord
from app.models.patient_tombstone import PatientTombstone
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event
from app.services.patient_registration_recovery_authority import (
    RegistrationRecoveryCapability,
)


SUPABASE_PROVIDER = "supabase"
REGISTRATION_RECOVERY_NOT_AVAILABLE = "REGISTRATION_RECOVERY_NOT_AVAILABLE"
REGISTRATION_RECOVERY_NOT_REQUIRED = "REGISTRATION_RECOVERY_NOT_REQUIRED"
REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED = (
    "REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED"
)
REGISTRATION_RECOVERY_STATE_CHANGED = "REGISTRATION_RECOVERY_STATE_CHANGED"

REPAIR_RESTORE_RECORD = "restore_patient_record_anchor"
REPAIR_REBIND_MERGED_IDENTITY = "rebind_merged_identity"
REPAIR_REBIND_AND_RESTORE_RECORD = "rebind_merged_identity_and_restore_record_anchor"

_RECOVERY_REQUIRED_EVENT = "PATIENT_REGISTRATION_RECOVERY_REQUIRED"
_RECOVERY_COMPLETED_EVENT = "PATIENT_REGISTRATION_RECOVERY_COMPLETED"
_MAX_MERGE_HOPS = 16


class PatientRegistrationRecoveryError(RuntimeError):
    """Stable patient-registration recovery failure."""

    def __init__(self, code: str, *, reason_code: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class RegistrationRecoveryInspection:
    disposition: str
    provider_subject: str
    patient_id: str
    target_patient_id: str
    graph_fingerprint: str
    repair_kind: str | None = None
    reason_code: str | None = None

    @property
    def repairable(self) -> bool:
        return self.disposition == "repairable" and self.repair_kind is not None


@dataclass(frozen=True, slots=True)
class RegistrationRecoveryResult:
    patient_id: str
    provider_subject: str
    repair_kind: str


def _lock_key(provider_subject: str) -> int:
    digest = hashlib.sha256(
        f"registration-recovery:{SUPABASE_PROVIDER}:{provider_subject}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _fingerprint(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _supabase_identities(
    db: AsyncSession, patient_id: uuid.UUID
) -> list[PatientAuthIdentity]:
    result = await db.scalars(
        select(PatientAuthIdentity)
        .where(
            PatientAuthIdentity.patient_id == patient_id,
            PatientAuthIdentity.provider == SUPABASE_PROVIDER,
        )
        .order_by(PatientAuthIdentity.identity_id)
        .limit(3)
    )
    return list(result.all())


async def _record_exists(db: AsyncSession, patient_id: uuid.UUID) -> bool:
    row_id = await db.scalar(
        select(PatientRecord.id).where(PatientRecord.patient_id == patient_id).limit(1)
    )
    return row_id is not None


async def _erasure_status(db: AsyncSession, patient_id: uuid.UUID) -> str | None:
    return await db.scalar(
        select(PatientErasureTombstone.status)
        .where(PatientErasureTombstone.patient_ref == str(patient_id))
        .limit(1)
    )


async def _resolve_merge_chain(
    db: AsyncSession, patient_id: uuid.UUID
) -> tuple[list[tuple[str, str]], uuid.UUID] | None:
    """Resolve an unambiguous merge chain; return None when no tombstone exists."""

    chain: list[tuple[str, str]] = []
    current = patient_id
    seen: set[uuid.UUID] = set()
    for _ in range(_MAX_MERGE_HOPS):
        if current in seen:
            raise PatientRegistrationRecoveryError(
                REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED,
                reason_code="MERGE_TOMBSTONE_CYCLE",
            )
        seen.add(current)
        tombstone = await db.scalar(
            select(PatientTombstone).where(
                PatientTombstone.old_patient_uuid == current
            )
        )
        if tombstone is None:
            return (chain, current) if chain else None
        target = uuid.UUID(str(tombstone.canonical_patient_uuid))
        chain.append((str(current), str(target)))
        current = target
    raise PatientRegistrationRecoveryError(
        REGISTRATION_RECOVERY_MANUAL_REVIEW_REQUIRED,
        reason_code="MERGE_TOMBSTONE_CHAIN_TOO_DEEP",
    )


async def inspect_patient_registration_recovery(
    db: AsyncSession, *, provider_subject: str
) -> RegistrationRecoveryInspection:
    """Classify a verified Supabase subject without mutating account authority."""

    subject = provider_subject.strip()
    if not subject or len(subject) > 255:
        raise PatientRegistrationRecoveryError(REGISTRATION_RECOVERY_NOT_AVAILABLE)

    identity = await db.scalar(
        select(PatientAuthIdentity).where(
            PatientAuthIdentity.provider == SUPABASE_PROVIDER,
            PatientAuthIdentity.provider_subject == subject,
        )
    )
    if identity is None:
        raise PatientRegistrationRecoveryError(REGISTRATION_RECOVERY_NOT_AVAILABLE)

    source_patient_id = uuid.UUID(str(identity.patient_id))
    patient = await db.get(Patient, source_patient_id)
    if patient is None:
        payload = {
            "identity_id": str(identity.identity_id),
            "patient_id": str(source_patient_id),
            "patient_exists": False,
            "identity_revoked": identity.revoked_at is not None,
        }
        return RegistrationRecoveryInspection(
            disposition="manual_review",
            provider_subject=subject,
            patient_id=str(source_patient_id),
            target_patient_id=str(source_patient_id),
            graph_fingerprint=_fingerprint(payload),
            reason_code="LINKED_PATIENT_MISSING",
        )

    source_erasure = await _erasure_status(db, source_patient_id)
    source_identities = await _supabase_identities(db, source_patient_id)
    source_record = await _record_exists(db, source_patient_id)

    base_payload: dict[str, object] = {
        "identity_id": str(identity.identity_id),
        "patient_id": str(source_patient_id),
        "patient_exists": True,
        "patient_deleted": bool(patient.is_deleted),
        "identity_revoked": identity.revoked_at is not None,
        "source_erasure_status": source_erasure,
        "source_record_exists": source_record,
        "source_supabase_identity_ids": [str(row.identity_id) for row in source_identities],
    }

    if source_erasure is not None:
        return RegistrationRecoveryInspection(
            disposition="manual_review",
            provider_subject=subject,
            patient_id=str(source_patient_id),
            target_patient_id=str(source_patient_id),
            graph_fingerprint=_fingerprint(base_payload),
            reason_code="ERASURE_STATE_PRESENT",
        )
    if identity.revoked_at is not None:
        return RegistrationRecoveryInspection(
            disposition="manual_review",
            provider_subject=subject,
            patient_id=str(source_patient_id),
            target_patient_id=str(source_patient_id),
            graph_fingerprint=_fingerprint(base_payload),
            reason_code="IDENTITY_REVOKED",
        )
    if len(source_identities) != 1 or source_identities[0].identity_id != identity.identity_id:
        return RegistrationRecoveryInspection(
            disposition="manual_review",
            provider_subject=subject,
            patient_id=str(source_patient_id),
            target_patient_id=str(source_patient_id),
            graph_fingerprint=_fingerprint(base_payload),
            reason_code="MULTIPLE_SOURCE_IDENTITIES",
        )

    if not patient.is_deleted:
        disposition = "not_required" if source_record else "repairable"
        return RegistrationRecoveryInspection(
            disposition=disposition,
            provider_subject=subject,
            patient_id=str(source_patient_id),
            target_patient_id=str(source_patient_id),
            graph_fingerprint=_fingerprint(base_payload),
            repair_kind=None if source_record else REPAIR_RESTORE_RECORD,
            reason_code=None if source_record else "PATIENT_RECORD_ANCHOR_MISSING",
        )

    try:
        resolved = await _resolve_merge_chain(db, source_patient_id)
    except PatientRegistrationRecoveryError as exc:
        return RegistrationRecoveryInspection(
            disposition="manual_review",
            provider_subject=subject,
            patient_id=str(source_patient_id),
            target_patient_id=str(source_patient_id),
            graph_fingerprint=_fingerprint(
                {**base_payload, "merge_resolution_error": exc.reason_code}
            ),
            reason_code=exc.reason_code,
        )
    if resolved is None:
        return RegistrationRecoveryInspection(
            disposition="manual_review",
            provider_subject=subject,
            patient_id=str(source_patient_id),
            target_patient_id=str(source_patient_id),
            graph_fingerprint=_fingerprint(base_payload),
            reason_code="DELETED_PATIENT_WITHOUT_MERGE_TOMBSTONE",
        )

    chain, target_patient_id = resolved
    target_patient = await db.get(Patient, target_patient_id)
    target_erasure = await _erasure_status(db, target_patient_id)
    target_identities = await _supabase_identities(db, target_patient_id)
    target_record = await _record_exists(db, target_patient_id)
    payload = {
        **base_payload,
        "merge_chain": chain,
        "target_patient_id": str(target_patient_id),
        "target_patient_exists": target_patient is not None,
        "target_patient_deleted": (
            bool(target_patient.is_deleted) if target_patient is not None else None
        ),
        "target_erasure_status": target_erasure,
        "target_record_exists": target_record,
        "target_supabase_identity_ids": [str(row.identity_id) for row in target_identities],
    }
    graph_fingerprint = _fingerprint(payload)

    if target_patient is None or target_patient.is_deleted:
        return RegistrationRecoveryInspection(
            disposition="manual_review",
            provider_subject=subject,
            patient_id=str(source_patient_id),
            target_patient_id=str(target_patient_id),
            graph_fingerprint=graph_fingerprint,
            reason_code="CANONICAL_PATIENT_UNAVAILABLE",
        )
    if target_erasure is not None:
        return RegistrationRecoveryInspection(
            disposition="manual_review",
            provider_subject=subject,
            patient_id=str(source_patient_id),
            target_patient_id=str(target_patient_id),
            graph_fingerprint=graph_fingerprint,
            reason_code="CANONICAL_ERASURE_STATE_PRESENT",
        )
    if target_identities:
        return RegistrationRecoveryInspection(
            disposition="manual_review",
            provider_subject=subject,
            patient_id=str(source_patient_id),
            target_patient_id=str(target_patient_id),
            graph_fingerprint=graph_fingerprint,
            reason_code="CANONICAL_IDENTITY_CONFLICT",
        )

    repair_kind = (
        REPAIR_REBIND_MERGED_IDENTITY
        if target_record
        else REPAIR_REBIND_AND_RESTORE_RECORD
    )
    return RegistrationRecoveryInspection(
        disposition="repairable",
        provider_subject=subject,
        patient_id=str(source_patient_id),
        target_patient_id=str(target_patient_id),
        graph_fingerprint=graph_fingerprint,
        repair_kind=repair_kind,
        reason_code="MERGED_IDENTITY_RECONCILIATION_REQUIRED",
    )


async def audit_registration_recovery_required(
    db: AsyncSession,
    *,
    inspection: RegistrationRecoveryInspection,
    attempt_id: str,
) -> None:
    """Durably record recovery detection after fresh identity proof."""

    await enqueue_audit_event(
        db,
        audit_context=current_audit_context(AuditDomain.AUTH),
        idempotency_key=(
            "patient-registration-recovery-required:"
            + hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()
        ),
        actor_id="PATIENT_REGISTRATION_RECOVERY",
        event_type=_RECOVERY_REQUIRED_EVENT,
        target_id=inspection.target_patient_id,
        patient_id=inspection.target_patient_id,
        status=("MANUAL_REVIEW" if inspection.disposition == "manual_review" else "REQUIRED"),
        metadata={
            "disposition": inspection.disposition,
            "repair_kind": inspection.repair_kind,
            "reason_code": inspection.reason_code,
        },
    )


async def repair_patient_registration_account(
    db: AsyncSession,
    *,
    capability: RegistrationRecoveryCapability,
) -> RegistrationRecoveryResult:
    """Apply only the exact repair authorized by a still-matching graph."""

    try:
        source_patient_id = uuid.UUID(capability.patient_id)
    except ValueError as exc:
        raise PatientRegistrationRecoveryError(REGISTRATION_RECOVERY_STATE_CHANGED) from exc

    async with db.begin():
        bind = db.get_bind()
        if getattr(getattr(bind, "dialect", None), "name", None) == "postgresql":
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _lock_key(capability.provider_subject)},
            )

        inspection = await inspect_patient_registration_recovery(
            db, provider_subject=capability.provider_subject
        )
        if (
            not inspection.repairable
            or inspection.patient_id != str(source_patient_id)
            or inspection.repair_kind != capability.repair_kind
            or inspection.graph_fingerprint != capability.graph_fingerprint
        ):
            raise PatientRegistrationRecoveryError(REGISTRATION_RECOVERY_STATE_CHANGED)

        identity = await db.scalar(
            select(PatientAuthIdentity).where(
                PatientAuthIdentity.provider == SUPABASE_PROVIDER,
                PatientAuthIdentity.provider_subject == capability.provider_subject,
            )
        )
        if identity is None or identity.revoked_at is not None:
            raise PatientRegistrationRecoveryError(REGISTRATION_RECOVERY_STATE_CHANGED)

        target_patient_id = uuid.UUID(inspection.target_patient_id)
        if capability.repair_kind == REPAIR_RESTORE_RECORD:
            db.add(PatientRecord(patient_id=target_patient_id))
        elif capability.repair_kind in {
            REPAIR_REBIND_MERGED_IDENTITY,
            REPAIR_REBIND_AND_RESTORE_RECORD,
        }:
            identity.patient_id = target_patient_id
            if capability.repair_kind == REPAIR_REBIND_AND_RESTORE_RECORD:
                db.add(PatientRecord(patient_id=target_patient_id))
        else:
            raise PatientRegistrationRecoveryError(REGISTRATION_RECOVERY_STATE_CHANGED)

        await db.flush()
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.AUTH),
            idempotency_key=(
                "patient-registration-recovery-completed:"
                + hashlib.sha256(capability.token.encode("utf-8")).hexdigest()
            ),
            actor_id="PATIENT_REGISTRATION_RECOVERY",
            event_type=_RECOVERY_COMPLETED_EVENT,
            target_id=str(target_patient_id),
            patient_id=str(target_patient_id),
            status="SUCCESS",
            metadata={"repair_kind": capability.repair_kind},
        )

    return RegistrationRecoveryResult(
        patient_id=str(target_patient_id),
        provider_subject=capability.provider_subject,
        repair_kind=capability.repair_kind,
    )

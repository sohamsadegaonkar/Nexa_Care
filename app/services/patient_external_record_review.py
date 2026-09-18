"""Patient-self review/correction for external-record extraction candidates.

This service is intentionally patient-authority only. It decrypts only candidates
bound to the authenticated patient's import graph, preserves extracted values as
immutable evidence, stores patient corrections under a distinct encryption
context, and stops at READY_TO_SAVE. It never commits typed clinical records or
timeline entries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ConfigError
from app.models.patient import Patient
from app.models.patient_external_record_import import (
    PatientExternalRecordCandidate,
    PatientExternalRecordImport,
)
from app.security.audit_context import AuditDomain, current_audit_context
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
    check_erasure_registry,
)
from app.services.audit_outbox import enqueue_audit_event
from app.services.crypto_kms import (
    EncryptedField,
    EncryptionError,
    PatientDataErased,
    get_encryption_provider,
)

_REVIEWABLE_IMPORT_STATES = frozenset({"REVIEW_REQUIRED", "READY_TO_SAVE"})
_REVIEW_DECISIONS = frozenset({"accept", "correct", "reject"})
_PUBLIC_REVIEW_STATUS = {
    "NEEDS_REVIEW": "pending",
    "ACCEPTED": "accepted",
    "CORRECTED": "corrected",
    "REJECTED": "rejected",
}
_MAX_CORRECTION_CHARS = 4096


@dataclass(frozen=True, slots=True)
class PatientExternalRecordReviewItem:
    candidate_id: uuid.UUID
    field_name: str
    display_label: str
    extracted_value: str
    corrected_value: str | None
    decision: str
    source_page: int | None
    source_text: str | None
    evidence_complete: bool
    confirmation_required: bool = True


@dataclass(frozen=True, slots=True)
class PatientExternalRecordReviewSnapshot:
    import_id: uuid.UUID
    category: str
    status: str
    items: tuple[PatientExternalRecordReviewItem, ...]


def _display_label(field_name: str) -> str:
    label = " ".join(part for part in field_name.replace("-", "_").split("_") if part)
    return (label or "Report detail").title()[:128]


def _candidate_contexts(candidate_id: uuid.UUID) -> tuple[str, str, str]:
    return (
        f"patient_external_record_candidate_value:{candidate_id}",
        f"patient_external_record_candidate_source:{candidate_id}",
        f"patient_external_record_candidate_reviewed:{candidate_id}",
    )


def _public_review_status(status: str) -> str:
    return _PUBLIC_REVIEW_STATUS.get(status, "pending")


def _normalized_correction(decision: str, corrected_value: str | None) -> str | None:
    if decision not in _REVIEW_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "INVALID_REVIEW_DECISION"},
        )
    if decision != "correct":
        if corrected_value is not None:
            raise HTTPException(
                status_code=422,
                detail={"error_code": "UNEXPECTED_CORRECTION_VALUE"},
            )
        return None

    if corrected_value is None:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "CORRECTION_VALUE_REQUIRED"},
        )
    normalized = corrected_value.strip()
    if not normalized:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "CORRECTION_VALUE_REQUIRED"},
        )
    if len(normalized) > _MAX_CORRECTION_CHARS:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "CORRECTION_VALUE_TOO_LONG"},
        )
    return normalized


async def _assert_patient_active(
    db: AsyncSession,
    *,
    patient_uuid: uuid.UUID,
    patient_id: str,
) -> None:
    patient = await db.get(Patient, patient_uuid)
    if patient is None or patient.is_deleted:
        await db.rollback()
        raise HTTPException(
            status_code=410,
            detail={"error_code": "PATIENT_RECORD_RETIRED"},
        )
    try:
        await check_erasure_registry(patient_id, db)
    except _PatientErasedSignal as exc:
        await db.rollback()
        raise HTTPException(
            status_code=410,
            detail={"error_code": "PATIENT_DATA_ERASED"},
        ) from exc
    except ErasureRegistryUnavailable as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "PATIENT_DATA_UNAVAILABLE", "retryable": True},
        ) from exc


async def _load_owned_import_for_review(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    import_id: uuid.UUID,
    for_update: bool,
) -> PatientExternalRecordImport | None:
    stmt = select(PatientExternalRecordImport).where(
        PatientExternalRecordImport.id == import_id,
        PatientExternalRecordImport.patient_id == patient_id,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def _load_owned_candidate_for_update(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    import_row: PatientExternalRecordImport,
    candidate_id: uuid.UUID,
) -> PatientExternalRecordCandidate | None:
    return (
        await db.execute(
            select(PatientExternalRecordCandidate)
            .where(
                PatientExternalRecordCandidate.id == candidate_id,
                PatientExternalRecordCandidate.import_id == import_row.id,
                PatientExternalRecordCandidate.patient_id == patient_id,
                PatientExternalRecordCandidate.source_document_id
                == import_row.source_document_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _load_owned_candidates(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    import_row: PatientExternalRecordImport,
) -> list[PatientExternalRecordCandidate]:
    return list(
        (
            await db.execute(
                select(PatientExternalRecordCandidate)
                .where(
                    PatientExternalRecordCandidate.import_id == import_row.id,
                    PatientExternalRecordCandidate.patient_id == patient_id,
                    PatientExternalRecordCandidate.source_document_id
                    == import_row.source_document_id,
                )
                .order_by(
                    PatientExternalRecordCandidate.created_at,
                    PatientExternalRecordCandidate.id,
                )
            )
        )
        .scalars()
        .all()
    )


async def _decrypt_review_item(
    db: AsyncSession,
    *,
    patient_id: str,
    candidate: PatientExternalRecordCandidate,
) -> PatientExternalRecordReviewItem:
    raw_context, source_context, reviewed_context = _candidate_contexts(candidate.id)
    kms = get_encryption_provider()
    extracted_value = await kms.decrypt_field(
        patient_id,
        raw_context,
        EncryptedField.deserialize(candidate.encrypted_raw_value, raw_context),
        db,
    )
    source_text = None
    if candidate.encrypted_source_text:
        source_text = await kms.decrypt_field(
            patient_id,
            source_context,
            EncryptedField.deserialize(candidate.encrypted_source_text, source_context),
            db,
        )
    corrected_value = None
    if candidate.review_status == "CORRECTED" and candidate.encrypted_reviewed_value:
        corrected_value = await kms.decrypt_field(
            patient_id,
            reviewed_context,
            EncryptedField.deserialize(
                candidate.encrypted_reviewed_value,
                reviewed_context,
            ),
            db,
        )
    return PatientExternalRecordReviewItem(
        candidate_id=candidate.id,
        field_name=candidate.field_name,
        display_label=_display_label(candidate.field_name),
        extracted_value=extracted_value,
        corrected_value=corrected_value,
        decision=_public_review_status(candidate.review_status),
        source_page=candidate.source_page,
        source_text=source_text,
        evidence_complete=bool(candidate.evidence_complete),
    )


async def get_patient_external_record_review(
    db: AsyncSession,
    *,
    patient_id: str,
    import_id: uuid.UUID,
) -> PatientExternalRecordReviewSnapshot:
    """Return patient-owned extracted evidence for explicit patient confirmation."""
    try:
        patient_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid patient identity") from exc

    try:
        row = await _load_owned_import_for_review(
            db,
            patient_id=patient_uuid,
            import_id=import_id,
            for_update=False,
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"error_code": "EXTERNAL_RECORD_NOT_FOUND"},
            )
        if row.status not in _REVIEWABLE_IMPORT_STATES:
            raise HTTPException(
                status_code=409,
                detail={"error_code": "EXTERNAL_RECORD_REVIEW_NOT_READY"},
            )

        await _assert_patient_active(
            db,
            patient_uuid=patient_uuid,
            patient_id=patient_id,
        )
        candidates = await _load_owned_candidates(
            db,
            patient_id=patient_uuid,
            import_row=row,
        )
        if not candidates:
            raise HTTPException(
                status_code=409,
                detail={"error_code": "EXTERNAL_RECORD_REVIEW_EMPTY"},
            )

        items = tuple(
            [
                await _decrypt_review_item(
                    db,
                    patient_id=patient_id,
                    candidate=candidate,
                )
                for candidate in candidates
            ]
        )
        return PatientExternalRecordReviewSnapshot(
            import_id=row.id,
            category=row.category,
            status=("ready_to_save" if row.status == "READY_TO_SAVE" else "needs_review"),
            items=items,
        )
    except HTTPException:
        raise
    except PatientDataErased as exc:
        await db.rollback()
        raise HTTPException(
            status_code=410,
            detail={"error_code": "PATIENT_DATA_ERASED"},
        ) from exc
    except ErasureRegistryUnavailable as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "PATIENT_DATA_UNAVAILABLE", "retryable": True},
        ) from exc
    except (EncryptionError, ConfigError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "REVIEW_EVIDENCE_UNAVAILABLE", "retryable": True},
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        ) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "REVIEW_UNAVAILABLE", "retryable": True},
        ) from exc


async def review_patient_external_record_candidate(
    db: AsyncSession,
    *,
    patient_id: str,
    import_id: uuid.UUID,
    candidate_id: uuid.UUID,
    decision: str,
    corrected_value: str | None = None,
) -> PatientExternalRecordImport:
    """Persist one explicit patient review decision without canonicalizing it."""
    normalized_correction = _normalized_correction(decision, corrected_value)
    try:
        patient_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid patient identity") from exc

    try:
        row = await _load_owned_import_for_review(
            db,
            patient_id=patient_uuid,
            import_id=import_id,
            for_update=True,
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"error_code": "EXTERNAL_RECORD_NOT_FOUND"},
            )
        if row.status not in _REVIEWABLE_IMPORT_STATES:
            raise HTTPException(
                status_code=409,
                detail={"error_code": "EXTERNAL_RECORD_REVIEW_NOT_READY"},
            )

        await _assert_patient_active(
            db,
            patient_uuid=patient_uuid,
            patient_id=patient_id,
        )
        candidate = await _load_owned_candidate_for_update(
            db,
            patient_id=patient_uuid,
            import_row=row,
            candidate_id=candidate_id,
        )
        if candidate is None:
            raise HTTPException(
                status_code=404,
                detail={"error_code": "EXTERNAL_RECORD_REVIEW_ITEM_NOT_FOUND"},
            )

        now = datetime.now(timezone.utc)
        if decision == "correct":
            assert normalized_correction is not None
            kms = get_encryption_provider()
            await kms.ensure_active_dek(patient_id, db)
            reviewed_context = _candidate_contexts(candidate.id)[2]
            encrypted = await kms.encrypt_field(
                patient_id,
                reviewed_context,
                normalized_correction,
                db,
            )
            candidate.encrypted_reviewed_value = encrypted.serialize()
            candidate.review_status = "CORRECTED"
        elif decision == "accept":
            candidate.encrypted_reviewed_value = None
            candidate.review_status = "ACCEPTED"
        else:
            candidate.encrypted_reviewed_value = None
            candidate.review_status = "REJECTED"

        candidate.patient_reviewed = True
        candidate.reviewed_at = now
        await db.flush()

        unresolved = (
            await db.execute(
                select(func.count())
                .select_from(PatientExternalRecordCandidate)
                .where(
                    PatientExternalRecordCandidate.import_id == row.id,
                    PatientExternalRecordCandidate.patient_id == patient_uuid,
                    PatientExternalRecordCandidate.source_document_id
                    == row.source_document_id,
                    PatientExternalRecordCandidate.review_status == "NEEDS_REVIEW",
                )
            )
        ).scalar_one()
        row.status = "READY_TO_SAVE" if int(unresolved or 0) == 0 else "REVIEW_REQUIRED"
        row.review_completed_at = now if row.status == "READY_TO_SAVE" else None

        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.PIPELINE),
            idempotency_key=(
                f"patient-external-record-review:{candidate.id}:"
                f"{now.isoformat(timespec='microseconds')}"
            ),
            actor_id=f"patient:{patient_id}",
            event_type="PATIENT_EXTERNAL_RECORD_CANDIDATE_REVIEWED",
            target_id=str(row.id),
            patient_id=patient_id,
            metadata={
                "authority": "patient_self",
                "decision": decision,
                "review_complete": row.status == "READY_TO_SAVE",
            },
        )
        await db.commit()
        return row
    except HTTPException:
        raise
    except PatientDataErased as exc:
        await db.rollback()
        raise HTTPException(
            status_code=410,
            detail={"error_code": "PATIENT_DATA_ERASED"},
        ) from exc
    except ErasureRegistryUnavailable as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "PATIENT_DATA_UNAVAILABLE", "retryable": True},
        ) from exc
    except (EncryptionError, ConfigError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "REVIEW_ENCRYPTION_UNAVAILABLE", "retryable": True},
        ) from exc
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        ) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "REVIEW_UNAVAILABLE", "retryable": True},
        ) from exc

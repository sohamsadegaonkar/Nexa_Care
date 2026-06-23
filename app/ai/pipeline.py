"""Asynchronous remote Document AI pipeline orchestration.

The pipeline uses a hosted VLM client instead of local PyTorch/Transformers so
it can run on small cloud instances. Extracted output must pass a confidence
gate before any write to the primary shards.
"""

from __future__ import annotations

import json
import logging
import os
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.extractor import get_medical_document_extractor
from app.models.ai_models import ExtractedMedicalDocument
from app.models.document_review import DocumentReviewQueue
from app.observability.audit_ledger import append_audit_log
from app.services.sharding import split_pii_and_clinical_fields

logger = logging.getLogger("nexa_logger")

_AUTO_PROCESS_CONFIDENCE = 0.95
_HUMAN_REVIEW_CONFIDENCE = 0.80
_REVIEW_QUEUE_ALLOWED_FIELDS = {
    "patient_id",
    "diagnoses",
    "lab_results",
    "prescriptions",
}


def _sanitize_review_queue_payload(extracted_document: ExtractedMedicalDocument) -> dict:
    """Return clinical-only extraction data safe for the review queue."""

    raw_payload = extracted_document.model_dump()
    sanitized = {
        key: value
        for key, value in raw_payload.items()
        if key in _REVIEW_QUEUE_ALLOWED_FIELDS
    }

    patient_id = sanitized.get("patient_id")
    if patient_id is not None:
        try:
            sanitized["patient_id"] = str(UUID(str(patient_id)))
        except (TypeError, ValueError):
            sanitized.pop("patient_id", None)

    return sanitized


async def _audit_document_event(
    *,
    provider_uid: str,
    event_type: str,
    target_id: str,
    status: str,
) -> bool:
    """Write a non-PII document pipeline audit event, logging on failure."""

    success = await append_audit_log(
        actor_uid=provider_uid,
        event_type=event_type,
        target_id=target_id,
        status=status,
    )
    if not success:
        logger.critical(json.dumps({
            "event": "document_pipeline_audit_failed",
            "provider_uid": provider_uid,
            "event_type": event_type,
            "target_id": target_id,
        }))
    return success


async def _persist_auto_processed_document(
    *,
    db: AsyncSession,
    masked_internal_id: str,
    vault_payload: dict,
    clinical_payload: dict,
    commit: bool = True,
) -> None:
    """Persist confidence-gated extraction output into separated shards.

    ``commit=False`` lets review approval persist shard rows and queue status
    in a single transaction.
    """

    await db.execute(
        text(
            "INSERT INTO nexa_vault "
            "(masked_internal_id, patient_name, phone, aadhaar_abha_id) "
            "VALUES (:masked_internal_id, :patient_name, :phone, :aadhaar_abha_id)"
        ),
        {
            "masked_internal_id": masked_internal_id,
            "patient_name": vault_payload.get("patient_name", ""),
            "phone": vault_payload.get("phone", ""),
            "aadhaar_abha_id": vault_payload.get("aadhaar_abha_id", ""),
        },
    )
    await db.execute(
        text(
            "INSERT INTO nexa_clinical "
            "(masked_internal_id, diagnoses, lab_results, prescriptions) "
            "VALUES (:masked_internal_id, :diagnoses, :lab_results, :prescriptions)"
        ),
        {
            "masked_internal_id": masked_internal_id,
            "diagnoses": clinical_payload.get("diagnoses", []),
            "lab_results": clinical_payload.get("lab_results", []),
            "prescriptions": clinical_payload.get("prescriptions", []),
        },
    )
    if commit:
        await db.commit()


async def process_medical_document_background(
    file_path: str,
    provider_uid: str,
    db: AsyncSession,
) -> None:
    """Process one uploaded medical document in the background.

    Confidence gate:
    - >= 0.95: split and persist to ``nexa_vault`` + ``nexa_clinical``.
    - 0.80 to < 0.95: do not persist; audit for human review.
    - < 0.80: reject and audit low confidence.

    The temporary upload is deleted in ``finally`` regardless of outcome.
    """

    document_event_id = str(uuid4())

    try:
        extractor = get_medical_document_extractor()
        extracted_document: ExtractedMedicalDocument = await extractor.extract_data(file_path)
        confidence = extracted_document.extraction_confidence

        if confidence >= _AUTO_PROCESS_CONFIDENCE:
            extracted_payload = extracted_document.model_dump(exclude={"extraction_confidence"})
            vault_payload, clinical_payload, unrecognized_payload = split_pii_and_clinical_fields(
                extracted_payload
            )

            if unrecognized_payload:
                # Unknown model keys may be PII. Keep them out of clinical data.
                vault_payload.update(unrecognized_payload)

            masked_internal_id = str(uuid4())
            audit_started = await _audit_document_event(
                provider_uid=provider_uid,
                event_type="DOCUMENT_AUTO_PROCESS_STARTED",
                target_id=masked_internal_id,
                status="STARTED",
            )
            if not audit_started:
                raise RuntimeError("Audit ledger write failed before document shard persistence.")

            try:
                await _persist_auto_processed_document(
                    db=db,
                    masked_internal_id=masked_internal_id,
                    vault_payload=vault_payload,
                    clinical_payload=clinical_payload,
                )
            except Exception:
                await db.rollback()
                raise

            await _audit_document_event(
                provider_uid=provider_uid,
                event_type="DOCUMENT_AUTO_PROCESSED",
                target_id=masked_internal_id,
                status="SUCCESS",
            )

            logger.info(json.dumps({
                "event": "document_ai_pipeline_auto_processed",
                "provider_uid": provider_uid,
                "masked_internal_id": masked_internal_id,
                "confidence": confidence,
                "vault_field_count": len(vault_payload),
                "clinical_field_count": len(clinical_payload),
                "unrecognized_field_count": len(unrecognized_payload),
            }))
            return

        if confidence >= _HUMAN_REVIEW_CONFIDENCE:
            review = DocumentReviewQueue(
                provider_uid=provider_uid,
                status="PENDING",
                confidence_score=confidence,
                extracted_data=_sanitize_review_queue_payload(extracted_document),
            )
            db.add(review)
            try:
                await db.commit()
                await db.refresh(review)
            except Exception:
                await db.rollback()
                raise

            await _audit_document_event(
                provider_uid=provider_uid,
                event_type="DOCUMENT_NEEDS_REVIEW",
                target_id=str(review.id),
                status="HUMAN_REVIEW_REQUIRED",
            )
            logger.warning(json.dumps({
                "event": "document_ai_pipeline_needs_review",
                "provider_uid": provider_uid,
                "review_id": str(review.id),
                "confidence": confidence,
            }))
            return

        await _audit_document_event(
            provider_uid=provider_uid,
            event_type="DOCUMENT_REJECTED_LOW_CONFIDENCE",
            target_id=document_event_id,
            status="REJECTED",
        )
        logger.warning(json.dumps({
            "event": "document_ai_pipeline_rejected_low_confidence",
            "provider_uid": provider_uid,
            "document_event_id": document_event_id,
            "confidence": confidence,
        }))
    except Exception as exc:
        logger.exception(json.dumps({
            "event": "document_ai_pipeline_failed",
            "provider_uid": provider_uid,
            "exception_type": type(exc).__name__,
        }))
        raise
    finally:
        try:
            os.remove(file_path)
            logger.info(json.dumps({
                "event": "document_temp_file_deleted",
                "provider_uid": provider_uid,
            }))
        except FileNotFoundError:
            logger.warning(json.dumps({
                "event": "document_temp_file_missing_on_cleanup",
                "provider_uid": provider_uid,
            }))
        except OSError as exc:
            logger.critical(json.dumps({
                "event": "document_temp_file_cleanup_failed",
                "provider_uid": provider_uid,
                "exception": str(exc),
            }))

"""Patient-authority extraction for retained external medical records.

This module deliberately reuses only the configured extraction mechanism,
provider-authentic field evidence, patient envelope encryption, and durable
audit primitives. It never creates provider, hospital, tenant, treatment-
consent, ClinicalAccessSession, or delegated-provider authority.

Successful extraction stops at REVIEW_REQUIRED. Candidates are not canonical
clinical truth and this module never commits typed clinical records or timeline
entries.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.extractor import (
    AwsTextractExtractionProvider,
    DemoExtractionProvider,
    DocumentExtractionError,
    ExtractionProvider,
    ExtractionProviderResult,
    ProviderResponseError,
    RemoteExtractionProvider,
    get_medical_document_extractor,
)
from app.core.config import ConfigError, get_document_extraction_config
from app.models.ai_models import ProviderFieldEvidence
from app.models.patient import Patient
from app.models.patient_external_record_import import (
    PatientExternalRecordCandidate,
    PatientExternalRecordImport,
)
from app.models.pipeline import DocumentStorage as DocumentStorageRecord
from app.security.audit_context import AuditDomain, current_audit_context
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
    check_erasure_registry,
)
from app.services.audit_outbox import enqueue_audit_event
from app.services.crypto_kms import (
    EncryptionError,
    PatientDataErased,
    get_encryption_provider,
)
from app.services.document_storage import DocumentStorageError, get_document_storage

_IDENTITY_FIELDS = frozenset({"patient_name", "phone", "aadhaar_abha_id"})
_TERMINAL_OR_ALREADY_PROCESSED = frozenset(
    {
        "PROCESSING",
        "REVIEW_REQUIRED",
        "READY_TO_SAVE",
        "COMPLETED",
        "FAILED_TERMINAL",
        "CANCELLED",
    }
)
_ALLOWED_START_STATES = frozenset({"UPLOADED", "FAILED_RETRYABLE"})
_EXPECTED_PROVIDER_TYPES: dict[str, type[ExtractionProvider]] = {
    "demo": DemoExtractionProvider,
    "aws_textract": AwsTextractExtractionProvider,
    "remote": RemoteExtractionProvider,
}
_EVIDENCE_NAMESPACE = uuid.UUID("d5fa4134-df29-49a5-a3e7-c7c8999d56b8")


def _failure_status(retryable: bool) -> str:
    return "FAILED_RETRYABLE" if retryable else "FAILED_TERMINAL"


def _candidate_evidence_id(
    import_id: uuid.UUID,
    evidence: ProviderFieldEvidence,
    ordinal: int,
) -> uuid.UUID:
    """Create a deterministic server-owned candidate ID without clinical values."""
    provider_fingerprint = evidence.evidence_hash or "no-provider-hash"
    payload = "|".join(
        (
            "patient-external-record-evidence:v1",
            str(import_id),
            str(ordinal),
            evidence.provider_name,
            evidence.provider_api_version,
            evidence.canonical_field_name,
            provider_fingerprint,
        )
    )
    return uuid.uuid5(_EVIDENCE_NAMESPACE, payload)


def _reviewable_field_evidence(
    result: ExtractionProviderResult,
) -> tuple[ProviderFieldEvidence, ...]:
    """Return only provider-authentic non-identity evidence with a real value."""
    return tuple(
        evidence
        for evidence in result.document.field_evidence
        if evidence.canonical_field_name not in _IDENTITY_FIELDS
        and bool(evidence.raw_value.strip())
    )


def _validated_provider_result(
    *,
    configured_provider: str,
    extractor: ExtractionProvider,
    result: ExtractionProviderResult,
) -> ExtractionProviderResult:
    """Re-check server-owned adapter/result provenance without provider authority."""
    expected_type = _EXPECTED_PROVIDER_TYPES.get(configured_provider)
    if (
        expected_type is None
        or not isinstance(extractor, expected_type)
        or not isinstance(result, ExtractionProviderResult)
        or result.response_complete is not True
        or result.provider_adapter != configured_provider
        or result.provider_adapter != extractor.adapter_identity
        or result.provider_contract_version != extractor.contract_version
    ):
        raise ProviderResponseError("Extraction response failed provenance validation")

    provider_version = result.provider_model_version or result.provider_contract_version
    if (
        not result.provider_adapter
        or len(result.provider_adapter) > 32
        or not provider_version
        or len(provider_version) > 64
    ):
        raise ProviderResponseError("Extraction response failed provenance validation")

    for evidence in result.document.field_evidence:
        if (
            not evidence.provider_name
            or evidence.provider_name != result.provider_adapter
            or not evidence.provider_api_version
            or len(evidence.provider_api_version) > 64
            or not evidence.canonical_field_name
            or len(evidence.canonical_field_name) > 128
            or evidence.extraction_timestamp.tzinfo is None
        ):
            raise ProviderResponseError("Extraction field evidence failed provenance validation")
    return result


def _source_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _load_owned_import_for_update(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    import_id: uuid.UUID,
) -> PatientExternalRecordImport | None:
    result = await db.execute(
        select(PatientExternalRecordImport)
        .where(
            PatientExternalRecordImport.id == import_id,
            PatientExternalRecordImport.patient_id == patient_id,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _load_owned_source(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    row: PatientExternalRecordImport,
) -> DocumentStorageRecord | None:
    result = await db.execute(
        select(DocumentStorageRecord).where(
            DocumentStorageRecord.id == row.source_document_id,
            DocumentStorageRecord.patient_id == patient_id,
            DocumentStorageRecord.tenant_id.is_(None),
            DocumentStorageRecord.source_system == "patient_self",
            DocumentStorageRecord.upload_purpose == "patient_external_record_import",
        )
    )
    return result.scalar_one_or_none()


async def _commit_failure(
    db: AsyncSession,
    *,
    row: PatientExternalRecordImport,
    patient_id: str,
    error_code: str,
    retryable: bool,
) -> PatientExternalRecordImport:
    stable_code = error_code[:64] or "EXTRACTION_FAILED"
    row.status = _failure_status(retryable)
    row.error_code = stable_code
    row.retryable = retryable
    await enqueue_audit_event(
        db,
        audit_context=current_audit_context(AuditDomain.PIPELINE),
        idempotency_key=(
            f"patient-external-record-extraction-failed:{row.id}:{row.attempt_count}"
        ),
        actor_id=f"patient:{patient_id}",
        event_type="EXTRACTION_JOB_FAILED",
        target_id=str(row.id),
        patient_id=patient_id,
        status="FAILURE",
        metadata={
            "authority": "patient_self",
            "error_code": stable_code,
            "retryable": retryable,
        },
    )
    await db.commit()
    return row


async def process_patient_external_record(
    db: AsyncSession,
    *,
    patient_id: str,
    import_id: uuid.UUID,
) -> PatientExternalRecordImport:
    """Extract one owned source and persist encrypted review candidates only."""
    try:
        patient_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid patient identity") from exc

    try:
        row = await _load_owned_import_for_update(
            db,
            patient_id=patient_uuid,
            import_id=import_id,
        )
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"error_code": "EXTERNAL_RECORD_NOT_FOUND"},
            )

        if row.status in _TERMINAL_OR_ALREADY_PROCESSED:
            return row
        if row.status not in _ALLOWED_START_STATES:
            raise HTTPException(
                status_code=409,
                detail={"error_code": "EXTERNAL_RECORD_STATE_CONFLICT"},
            )

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

        source = await _load_owned_source(db, patient_id=patient_uuid, row=row)
        now = datetime.now(timezone.utc)
        row.status = "PROCESSING"
        row.processing_started_at = now
        row.attempt_count = int(row.attempt_count or 0) + 1
        row.error_code = None
        row.retryable = False

        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.PIPELINE),
            idempotency_key=(
                f"patient-external-record-extraction-start:{row.id}:{row.attempt_count}"
            ),
            actor_id=f"patient:{patient_id}",
            event_type="EXTRACTION_JOB_STARTED",
            target_id=str(row.id),
            patient_id=patient_id,
            metadata={"authority": "patient_self", "attempt": row.attempt_count},
        )

        if source is None:
            return await _commit_failure(
                db,
                row=row,
                patient_id=patient_id,
                error_code="SOURCE_DOCUMENT_NOT_FOUND",
                retryable=False,
            )
        if not source.content_hash or not row.content_hash:
            return await _commit_failure(
                db,
                row=row,
                patient_id=patient_id,
                error_code="SOURCE_INTEGRITY_METADATA_MISSING",
                retryable=False,
            )

        try:
            storage = get_document_storage()
            source_bytes = await storage.get_patient_document_bytes(
                source.storage_ref,
                patient_id=patient_id,
            )
        except (DocumentStorageError, ConfigError):
            return await _commit_failure(
                db,
                row=row,
                patient_id=patient_id,
                error_code="SOURCE_DOCUMENT_UNAVAILABLE",
                retryable=True,
            )

        try:
            digest = _source_digest(source_bytes)
            if digest != source.content_hash or digest != row.content_hash:
                return await _commit_failure(
                    db,
                    row=row,
                    patient_id=patient_id,
                    error_code="SOURCE_INTEGRITY_MISMATCH",
                    retryable=False,
                )

            try:
                extraction_config = get_document_extraction_config()
                extractor = get_medical_document_extractor(extraction_config)
            except (ConfigError, ValueError):
                return await _commit_failure(
                    db,
                    row=row,
                    patient_id=patient_id,
                    error_code="EXTRACTION_CONFIGURATION_UNAVAILABLE",
                    retryable=True,
                )

            request_id = (
                f"patient-external-record:{row.id}:attempt:{row.attempt_count}"
            )
            try:
                raw_result = await extractor.extract_bytes(
                    source_bytes,
                    mime_type=source.content_type,
                    request_id=request_id,
                )
                result = _validated_provider_result(
                    configured_provider=extraction_config.provider,
                    extractor=extractor,
                    result=raw_result,
                )
            except DocumentExtractionError as exc:
                return await _commit_failure(
                    db,
                    row=row,
                    patient_id=patient_id,
                    error_code=exc.error_code,
                    retryable=bool(exc.retryable),
                )

            reviewable = _reviewable_field_evidence(result)
            if not reviewable:
                return await _commit_failure(
                    db,
                    row=row,
                    patient_id=patient_id,
                    error_code="NO_REVIEWABLE_EVIDENCE",
                    retryable=False,
                )

            try:
                kms = get_encryption_provider()
                await kms.ensure_active_dek(patient_id, db)
                candidates: list[PatientExternalRecordCandidate] = []
                for ordinal, evidence in enumerate(reviewable, start=1):
                    evidence_id = _candidate_evidence_id(row.id, evidence, ordinal)
                    raw_context = (
                        f"patient_external_record_candidate_value:{evidence_id}"
                    )
                    source_context = (
                        f"patient_external_record_candidate_source:{evidence_id}"
                    )
                    encrypted_raw = await kms.encrypt_field(
                        patient_id,
                        raw_context,
                        evidence.raw_value,
                        db,
                    )
                    encrypted_source = None
                    if evidence.source_text:
                        encrypted_source = await kms.encrypt_field(
                            patient_id,
                            source_context,
                            evidence.source_text,
                            db,
                        )

                    bbox_json = (
                        json.dumps(
                            evidence.bounding_box.model_dump(mode="json"),
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if evidence.bounding_box is not None
                        else None
                    )
                    clinical_fact_key = evidence.trusted_clinical_fact_id
                    if clinical_fact_key is not None and len(clinical_fact_key) > 64:
                        clinical_fact_key = None
                    candidates.append(
                        PatientExternalRecordCandidate(
                            id=evidence_id,
                            import_id=row.id,
                            patient_id=patient_uuid,
                            source_document_id=row.source_document_id,
                            evidence_id=evidence_id,
                            field_name=evidence.canonical_field_name,
                            clinical_fact_key=clinical_fact_key,
                            encrypted_raw_value=encrypted_raw.serialize(),
                            encrypted_source_text=(
                                encrypted_source.serialize()
                                if encrypted_source is not None
                                else None
                            ),
                            encrypted_reviewed_value=None,
                            source_page=evidence.page_number,
                            source_bbox_json=bbox_json,
                            field_confidence=evidence.field_confidence,
                            document_confidence=result.document.extraction_confidence,
                            extractor_provider=result.provider_adapter,
                            extractor_version=(
                                result.provider_model_version
                                or result.provider_contract_version
                            ),
                            evidence_complete=not evidence.incomplete,
                            review_status="NEEDS_REVIEW",
                            patient_reviewed=False,
                            extracted_at=evidence.extraction_timestamp,
                            reviewed_at=None,
                            created_at=now,
                        )
                    )
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
            except (EncryptionError, ConfigError):
                return await _commit_failure(
                    db,
                    row=row,
                    patient_id=patient_id,
                    error_code="CANDIDATE_ENCRYPTION_UNAVAILABLE",
                    retryable=True,
                )

            db.add_all(candidates)
            row.status = "REVIEW_REQUIRED"
            row.extractor_provider = result.provider_adapter
            row.extractor_version = (
                result.provider_model_version or result.provider_contract_version
            )
            row.error_code = None
            row.retryable = False
            await db.flush()
            await enqueue_audit_event(
                db,
                audit_context=current_audit_context(AuditDomain.PIPELINE),
                idempotency_key=(
                    f"patient-external-record-extraction-validated:{row.id}:"
                    f"{row.attempt_count}"
                ),
                actor_id=f"patient:{patient_id}",
                event_type="EXTRACTION_JOB_VALIDATED",
                target_id=str(row.id),
                patient_id=patient_id,
                metadata={
                    "authority": "patient_self",
                    "provider": result.provider_adapter,
                    "candidate_count": len(candidates),
                },
            )
            await db.commit()
            return row
        finally:
            del source_bytes
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error_code": "IMPORT_PERSISTENCE_UNAVAILABLE", "retryable": True},
        ) from exc

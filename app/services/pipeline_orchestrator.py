"""Patient-bound, fail-closed extraction job orchestration."""

from __future__ import annotations

from app.security.audit_context import AuditContext, AuditDomain

import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.extractor import DocumentExtractionError, get_medical_document_extractor
from app.ai.candidate_eligibility import (
    CANDIDATE_ELIGIBILITY_POLICY_VERSION,
    CandidateEligibility,
    classify_semantic_candidate,
)
from app.core.config import get_document_extraction_config
from app.models.pipeline import DocumentStorage as DocumentStorageRecord
from app.models.pipeline import ExtractionCandidateRecord, ExtractionJob
from app.models.extraction_decision import ExtractionDecisionPolicy
from app.models.field_evidence import SnapshotState
from app.models.shards import NexaVault
from app.observability.audit_ledger import append_audit_log_or_503
from app.observability.safe_exceptions import log_safe_exception
from app.services.document_storage import DocumentStorageError, get_document_storage
from app.services.extraction_evidence_adapter import (
    CurrentExtractionBinding,
    adapt_current_extracted_field,
)
from app.services.approved_access_capability import (
    ApprovedAccessStoreUnavailable,
    validate_live_document_processing_request,
)
from app.security.erasure_registry import check_erasure_registry
from app.services.audit_outbox import enqueue_audit_event
from app.services.extraction_routing import evaluate_and_persist_lane
from app.services.crypto_kms import (
    EncryptedField,
    EncryptionError,
    get_encryption_provider,
)

logger = logging.getLogger("nexa_logger")


class ExtractedIdentityMismatch(RuntimeError):
    pass


async def _rollback_and_reload_job(
    db: AsyncSession, job_uuid: uuid.UUID
) -> ExtractionJob:
    await db.rollback()
    return (
        await db.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_uuid).with_for_update()
        )
    ).scalar_one()


async def _validate_extracted_identity(
    extracted: Any, patient_id: uuid.UUID, db: AsyncSession
) -> None:
    """Quarantine non-empty OCR identity that disagrees with the bound patient."""
    extracted_values = {
        "patient_name": str(extracted.patient_name or "").strip(),
        "phone": str(extracted.phone or "").strip(),
        "aadhaar_abha_id": str(extracted.aadhaar_abha_id or "").strip(),
    }
    supplied = {name: value for name, value in extracted_values.items() if value}
    if not supplied:
        return
    row = (
        await db.execute(
            select(NexaVault)
            .where(NexaVault.masked_internal_id == str(patient_id))
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ExtractedIdentityMismatch()
    kms = get_encryption_provider()
    for field_name, extracted_value in supplied.items():
        stored = getattr(row, field_name, None)
        if not stored:
            raise ExtractedIdentityMismatch()
        canonical = await kms.decrypt_field(
            str(patient_id),
            field_name,
            EncryptedField.deserialize(stored, field_name),
            db,
        )

        def normalize(value: str) -> str:
            return re.sub(r"[^a-z0-9]", "", value.casefold())

        if not secrets.compare_digest(normalize(canonical), normalize(extracted_value)):
            raise ExtractedIdentityMismatch()


def _candidate_fields(document: Any) -> list[dict[str, Any]]:
    """Convert clinical arrays only; OCR identity is never a chart candidate."""

    if document.field_evidence:
        from app.ai.semantic_evidence import group_semantic_candidates

        candidates: list[dict[str, Any]] = []
        for candidate in group_semantic_candidates(document.field_evidence):
            if candidate.representative.canonical_field_name in {
                "patient_name",
                "phone",
                "aadhaar_abha_id",
            }:
                continue
            try:
                classification = classify_semantic_candidate(candidate)
                classification_failed = False
            except Exception:
                classification = CandidateEligibility.INELIGIBLE_CLASSIFICATION_FAILED
                classification_failed = True
            eligible = classification is CandidateEligibility.ELIGIBLE
            candidates.append(
                {
                    "field_name": candidate.representative.canonical_field_name,
                    "raw_value": candidate.representative.raw_value,
                    "provider_evidence": candidate.representative,
                    "semantic_candidate": candidate,
                    "routing_eligible": eligible,
                    "eligibility_reason_code": (
                        None if eligible else classification.value
                    ),
                    "eligibility_policy_version": CANDIDATE_ELIGIBILITY_POLICY_VERSION,
                    "eligibility_classification_failed": classification_failed,
                }
            )
        return candidates

    candidates = []
    candidates.extend(
        {"field_name": "diagnosis", "raw_value": str(value)}
        for value in document.diagnoses
    )
    candidates.extend(
        {"field_name": "lab_result", "raw_value": str(value)}
        for value in document.lab_results
    )
    candidates.extend(
        {"field_name": "medication", "raw_value": str(value)}
        for value in document.prescriptions
    )
    return candidates


def _eligibility_counts(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    ineligible = [item for item in candidates if not item.get("routing_eligible", True)]
    by_reason: dict[str, int] = {}
    for item in ineligible:
        reason = item.get("eligibility_reason_code")
        if isinstance(reason, str):
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "eligible_candidate_count": len(candidates) - len(ineligible),
        "ineligible_candidate_count": len(ineligible),
        "ineligible_count_by_reason": dict(sorted(by_reason.items())),
    }


async def process_extraction_job(job_id: str, db: AsyncSession) -> dict[str, Any]:
    try:
        job_uuid = uuid.UUID(str(job_id))
    except (TypeError, ValueError):
        return {"status": "extraction_failed_terminal", "error_code": "INVALID_JOB_ID"}

    job = (
        await db.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_uuid).with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        return {"status": "extraction_failed_terminal", "error_code": "JOB_NOT_FOUND"}
    audit_context = AuditContext.for_tenant(
        tenant_id=str(job.tenant_id),
        domain=AuditDomain.PIPELINE,
    )
    if job.status in {
        "extracted",
        "validation_pending",
        "review_pending",
        "ready_for_commit",
        "committed",
        "source_only",
        "quarantined",
    }:
        return {"job_id": str(job.id), "status": job.status, "idempotent": True}
    now = datetime.now(timezone.utc)
    if job.status == "extracting" and isinstance(job.processing_started_at, datetime):
        started = job.processing_started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if now - started < timedelta(minutes=15):
            return {"job_id": str(job.id), "status": "extracting", "idempotent": True}

    job.status = "extracting"
    job.processing_started_at = now
    job.attempt_count = int(job.attempt_count or 0) + 1
    job.error_code = None
    job.retryable = False
    await db.commit()

    await append_audit_log_or_503(
        audit_context=audit_context,
        actor_uid=job.uploader_id or "SYSTEM_PIPELINE",
        event_type="EXTRACTION_JOB_STARTED",
        target_id=str(job.id),
        status="STARTED",
        metadata={
            "document_id": str(job.document_id),
            "patient_id": str(job.patient_id),
            "tenant_id": str(job.tenant_id) if job.tenant_id else None,
            "request_id": job.request_id,
            "attempt_count": job.attempt_count,
        },
    )

    try:
        document = (
            await db.execute(
                select(DocumentStorageRecord).where(
                    DocumentStorageRecord.id == job.document_id
                )
            )
        ).scalar_one_or_none()
        if document is None or job.tenant_id is None:
            raise DocumentStorageError("Document metadata unavailable")
        storage = get_document_storage()
        document_bytes = await storage.get_document_bytes(
            document.storage_ref,
            tenant_id=str(job.tenant_id),
            patient_id=str(job.patient_id),
        )
        config = get_document_extraction_config()
        job.extractor_provider = config.provider
        extractor = get_medical_document_extractor()
        extracted = await extractor.extract_bytes(
            document_bytes,
            mime_type=document.content_type,
            request_id=job.request_id or str(job.id),
        )
        if extracted.field_evidence:
            job.extractor_version = extracted.field_evidence[0].provider_api_version
        del document_bytes
        await _validate_extracted_identity(extracted, job.patient_id, db)

        consent_active = False
        try:
            if all(
                (
                    job.consent_request_id,
                    job.authorization_provider_id,
                    job.tenant_id,
                )
            ):
                consent_active = (
                    await validate_live_document_processing_request(
                        request_id=str(job.consent_request_id),
                        patient_id=str(job.patient_id),
                        provider_id=str(job.authorization_provider_id),
                        hospital_id=str(job.tenant_id),
                    )
                    is not None
                )
        except ApprovedAccessStoreUnavailable:
            consent_active = False

        erasure_clear = False
        try:
            await check_erasure_registry(str(job.patient_id), db)
            erasure_clear = True
        except Exception:
            erasure_clear = False

        job.status = "extracted"
        await db.flush()
        job.status = "validation_pending"
        candidates = _candidate_fields(extracted)
        eligibility_counts = _eligibility_counts(candidates)
        extracted_at = datetime.now(timezone.utc)
        evidence_records = []
        for item in candidates:
            evidence_records.append(
                adapt_current_extracted_field(
                    document=extracted,
                    field_name=item["field_name"],
                    raw_value=item["raw_value"],
                    provider_evidence=item.get("provider_evidence"),
                    binding=CurrentExtractionBinding(
                        patient_id=str(job.patient_id),
                        tenant_id=str(job.tenant_id),
                        organization_id=str(job.tenant_id),
                        source_document_id=str(job.document_id),
                        source_document_hash=document.content_hash,
                        ingestion_id=str(document.id),
                        job_id=str(job.id),
                        workflow_id=job.consent_request_id,
                        request_id=job.request_id,
                        attempt_number=job.attempt_count,
                        attempt_id=f"{job.id}:{job.attempt_count}",
                        created_at=job.created_at,
                        extracted_at=extracted_at,
                        source_received_at=document.uploaded_at,
                        provider_name=config.provider,
                        model_name=None,
                        model_version=job.extractor_version,
                        consent_reference=job.consent_request_id,
                        consent_state=(
                            SnapshotState.ACTIVE
                            if consent_active
                            else SnapshotState.INACTIVE
                        ),
                        erasure_state=(
                            SnapshotState.NOT_REQUESTED
                            if erasure_clear
                            else SnapshotState.IN_PROGRESS
                        ),
                    ),
                )
            )

        if not evidence_records:
            job.status = (
                "source_only" if consent_active and erasure_clear else "quarantined"
            )
            if job.status == "quarantined":
                job.error_code = "LIVE_PROCESSING_AUTHORIZATION_BLOCKED"
            job.completed_at = datetime.now(timezone.utc)
            await enqueue_audit_event(
                db,
                audit_context=audit_context,
                idempotency_key=f"extraction:{job.id}:{job.attempt_count}:empty-source",
                actor_id=job.uploader_id or "SYSTEM_PIPELINE",
                event_type="EXTRACTION_JOB_ROUTED",
                target_id=str(job.id),
                patient_id=str(job.patient_id),
                metadata={
                    "job_id": str(job.id),
                    "document_id": str(job.document_id),
                    "lane": (
                        "SOURCE_ONLY" if job.status == "source_only" else "QUARANTINE"
                    ),
                    "candidate_count": 0,
                    "eligible_candidate_count": eligibility_counts[
                        "eligible_candidate_count"
                    ],
                    "ineligible_candidate_count": eligibility_counts[
                        "ineligible_candidate_count"
                    ],
                    "ineligible_count_by_reason": eligibility_counts[
                        "ineligible_count_by_reason"
                    ],
                },
            )
            await db.commit()
            return {
                "job_id": str(job.id),
                "status": job.status,
                "source_only_count": 0,
                **eligibility_counts,
            }

        results = []
        routed_at = datetime.now(timezone.utc)
        for item, evidence in zip(candidates, evidence_records, strict=True):
            policy = ExtractionDecisionPolicy(
                patient_id=str(job.patient_id),
                tenant_id=str(job.tenant_id),
                organization_id=str(job.tenant_id),
                source_document_id=str(job.document_id),
                evidence_id=evidence.evidence_id,
                job_id=str(job.id),
                workflow_id=str(job.consent_request_id),
                request_id=str(job.request_id),
                attempt_id=f"{job.id}:{job.attempt_count}",
                force_quarantine=item.get("eligibility_classification_failed", False),
            )
            results.append(
                await evaluate_and_persist_lane(
                    db,
                    evidence=evidence,
                    policy=policy,
                    job=job,
                    audit_context=audit_context,
                    actor_id=job.uploader_id or "SYSTEM_PIPELINE",
                    evaluated_at=routed_at,
                    quarantine_review_deadline=(
                        routed_at if not consent_active or not erasure_clear else None
                    ),
                )
            )

        kms = get_encryption_provider()
        for item, evidence, result in zip(
            candidates, evidence_records, results, strict=True
        ):
            if item.get("provider_evidence") is None:
                continue
            evidence_uuid = uuid.UUID(evidence.evidence_id)
            value_context = f"extraction_candidate_value:{evidence.evidence_id}"
            source_context = f"extraction_candidate_source:{evidence.evidence_id}"
            encrypted_value = await kms.encrypt_field(
                str(job.patient_id),
                value_context,
                evidence.clinical_value.raw_value,
                db,
            )
            encrypted_source = None
            if evidence.visual.source_text:
                encrypted_source = await kms.encrypt_field(
                    str(job.patient_id),
                    source_context,
                    evidence.visual.source_text,
                    db,
                )
            bbox = evidence.visual.bounding_box
            db.add(
                ExtractionCandidateRecord(
                    id=uuid.uuid4(),
                    evidence_id=evidence_uuid,
                    job_id=job.id,
                    source_document_id=job.document_id,
                    patient_id=job.patient_id,
                    tenant_id=job.tenant_id,
                    authorization_provider_id=str(job.authorization_provider_id),
                    field_name=evidence.clinical_value.field_name,
                    encrypted_raw_value=encrypted_value.serialize(),
                    encrypted_source_text=(
                        encrypted_source.serialize() if encrypted_source else None
                    ),
                    source_page=evidence.visual.page_number,
                    source_bbox=(
                        [bbox.left, bbox.top, bbox.right, bbox.bottom] if bbox else None
                    ),
                    field_confidence=evidence.model.field_confidence,
                    document_confidence=evidence.model.document_confidence,
                    provider_name=evidence.model.provider_name or "unknown",
                    provider_version=evidence.model.model_version or "unknown",
                    extracted_at=evidence.model.extracted_at,
                    evidence_complete=evidence.visual_evidence_complete,
                    lane=result.routing.lane,
                    reason_codes=list(result.decision.reason_codes),
                    routing_eligible=item.get("routing_eligible", True),
                    eligibility_reason_code=item.get("eligibility_reason_code"),
                    eligibility_policy_version=item.get(
                        "eligibility_policy_version",
                        CANDIDATE_ELIGIBILITY_POLICY_VERSION,
                    ),
                    created_at=routed_at,
                )
            )

        quarantine_count = sum(
            result.routing.lane == "QUARANTINE" for result in results
        )
        source_only_count = sum(
            result.routing.lane == "SOURCE_ONLY" for result in results
        )
        job.status = "quarantined" if quarantine_count else "source_only"
        job.error_code = "EXTRACTION_EVIDENCE_QUARANTINED" if quarantine_count else None
        job.completed_at = routed_at
        await enqueue_audit_event(
            db,
            audit_context=audit_context,
            idempotency_key=f"extraction:{job.id}:{job.attempt_count}:routed",
            actor_id=job.uploader_id or "SYSTEM_PIPELINE",
            event_type="EXTRACTION_JOB_ROUTED",
            target_id=str(job.id),
            patient_id=str(job.patient_id),
            metadata={
                "job_id": str(job.id),
                "document_id": str(job.document_id),
                "status": job.status,
                "source_only_count": source_only_count,
                "quarantine_count": quarantine_count,
                **eligibility_counts,
            },
        )
        await db.commit()
        return {
            "job_id": str(job.id),
            "status": job.status,
            "source_only_count": source_only_count,
            "quarantine_count": quarantine_count,
            **eligibility_counts,
        }
    except ExtractedIdentityMismatch:
        job = await _rollback_and_reload_job(db, job_uuid)
        job.status = "identity_mismatch"
        job.error_code = "EXTRACTED_IDENTITY_MISMATCH"
        job.retryable = False
    except EncryptionError:
        job = await _rollback_and_reload_job(db, job_uuid)
        job.status = "validation_failed"
        job.error_code = "IDENTITY_VALIDATION_UNAVAILABLE"
        job.retryable = False
    except DocumentExtractionError as exc:
        job = await _rollback_and_reload_job(db, job_uuid)
        retry_budget = get_document_extraction_config().max_attempts
        exhausted = exc.retryable and job.attempt_count >= retry_budget
        job.status = (
            "quarantined"
            if exhausted
            else "extraction_failed_retryable"
            if exc.retryable
            else "extraction_failed_terminal"
        )
        job.error_code = exc.error_code
        job.retryable = exc.retryable and not exhausted
    except DocumentStorageError:
        job = await _rollback_and_reload_job(db, job_uuid)
        job.status = "extraction_failed_terminal"
        job.error_code = "DOCUMENT_STORAGE_UNAVAILABLE"
        job.retryable = False
    except Exception as exc:
        job = await _rollback_and_reload_job(db, job_uuid)
        log_safe_exception(
            logger,
            exc,
            subsystem="extraction",
            operation="extraction_job_processing",
            fields={"job_id": str(job.id), "request_id": job.request_id},
        )
        job.status = "extraction_failed_terminal"
        job.error_code = "EXTRACTION_INTERNAL_ERROR"
        job.retryable = False

    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await append_audit_log_or_503(
        audit_context=audit_context,
        actor_uid=job.uploader_id or "SYSTEM_PIPELINE",
        event_type="EXTRACTION_JOB_FAILED",
        target_id=str(job.id),
        status="FAILED",
        metadata={
            "patient_id": str(job.patient_id),
            "tenant_id": str(job.tenant_id) if job.tenant_id else None,
            "request_id": job.request_id,
            "error_code": job.error_code,
            "retryable": job.retryable,
            "attempt_count": job.attempt_count,
        },
    )
    return {
        "job_id": str(job.id),
        "status": job.status,
        "error_code": job.error_code,
        "retryable": job.retryable,
    }

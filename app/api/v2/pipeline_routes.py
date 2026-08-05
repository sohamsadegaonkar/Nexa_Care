"""AI Ingestion Pipeline & Review Queue API Routes (Workstream 4, 5, 8).

Implements:
- POST /api/v2/pipeline/documents/upload (stages document, creates ExtractionJob, queues background extraction)
- GET /api/v2/pipeline/jobs/{job_id} (returns status and extracted fields)
- GET /api/v2/pipeline/review-queue (returns fields needing human adjudication)
- POST /api/v2/pipeline/fields/{field_id}/review (steward adjudication action)
- POST /api/v2/pipeline/jobs/{job_id}/commit (atomic commit of approved fields into patient records)

ALPHA security note: Pipeline endpoints that reference existing entities (jobs,
fields) now derive patient_id server-side from the DB row instead of trusting
client-supplied values.  This eliminates the patient_id spoofing vector
described in threat-model.md T-06.  The upload and review-queue endpoints
still accept client-provided patient_id because they either create a new entity
or filter by patient — in both cases the consent gate validates that the token
grants access to the requested patient_id.
"""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.document_processing_gate import (
    assert_job_authorization_binding,
    authorize_document_processing,
)
from app.core.config import get_document_extraction_config
from app.core.database import get_db_session, get_session_factory
from app.core.dependencies import get_current_provider
from app.models.extracted_field import ExtractedField
from app.models.adjudication import (
    IDEMPOTENCY_KEY_PATTERN,
    REVIEW_SESSION_PATTERN,
    AdjudicatedClinicalField,
    AdjudicationOutcome,
    AdjudicationReasonCode,
)
from app.models.patient_records import TimelineEvent
from app.models.pipeline import (
    DocumentStorage,
    AdjudicationCaseRecord,
    ExtractionCandidateRecord,
    ExtractedFieldRecord,
    ExtractionJob,
    FieldCorrection,
    ReviewQueueItem,
)
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.document_processing_policy import DocumentProcessingOperation
from app.ai.extractor import TEXTRACT_MAX_SYNC_BYTES
from app.services.pipeline_orchestrator import process_extraction_job
from app.services.record_ingestion import ingest_extracted_fields
from app.services.document_storage import get_document_storage
from app.services.crypto_kms import (
    EncryptedField,
    EncryptionError,
    PatientDataErased,
    get_encryption_provider,
)
from app.services.audit_outbox import enqueue_audit_event
from app.services.adjudication import (
    AdjudicationError,
    commit_submission as commit_adjudication_submission,
    create_case as create_adjudication_case,
    rotate_review_session as rotate_adjudication_review_session,
    read_source_document,
    submit_case as submit_adjudication_case,
)

logger = logging.getLogger("nexa_logger")

router = APIRouter(prefix="/api/v2/pipeline", tags=["pipeline"])


class FieldReviewRequest(BaseModel):
    action: str | None = None
    corrected_value: str | None = None
    review_notes: str | None = None
    version: int | None = None
    units: str | None = None


class RejectFieldRequest(BaseModel):
    reason: str | None = None


class EditFieldRequest(BaseModel):
    corrected_value: str
    units: str | None = None
    version: int | None = None


class CommitJobRequest(BaseModel):
    patient_id: str | None = None
    encounter_summary: str | None = None
    fields: list[dict[str, Any]] | None = None


class CreateAdjudicationCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_session_id: str = Field(
        min_length=8, max_length=96, pattern=REVIEW_SESSION_PATTERN
    )
    idempotency_key: str = Field(
        min_length=8, max_length=192, pattern=IDEMPOTENCY_KEY_PATTERN
    )


class AdjudicationSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_session_id: str = Field(
        min_length=8, max_length=96, pattern=REVIEW_SESSION_PATTERN
    )
    idempotency_key: str = Field(
        min_length=8, max_length=192, pattern=IDEMPOTENCY_KEY_PATTERN
    )
    outcome: AdjudicationOutcome
    fields: list[AdjudicatedClinicalField] = Field(default_factory=list)
    reason_codes: list[AdjudicationReasonCode] = Field(
        default_factory=list, max_length=4
    )
    supersedes_submission_id: uuid.UUID | None = None


class RecoverAdjudicationSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_session_id: str = Field(
        min_length=8, max_length=96, pattern=REVIEW_SESSION_PATTERN
    )


class ExtractionCandidateStatusResponse(BaseModel):
    field_name: str
    raw_value: str
    field_confidence: float | None = None
    source_page: int | None = None
    source_text: str | None = None
    source_bbox: list[float] | None = None
    evidence_complete: bool
    lane: Literal["SOURCE_ONLY", "QUARANTINE"]
    reason_codes: list[str] = Field(default_factory=list)


class ExtractionJobStatusResponse(BaseModel):
    job_id: str
    patient_id: str
    status: str
    document_type: str
    provider: str | None = None
    provider_version: str | None = None
    document_confidence: float | None = None
    routing_lane: Literal["SOURCE_ONLY", "QUARANTINE"] | None = None
    routing_reasons: list[str] = Field(default_factory=list)
    candidate_count: int
    eligible_candidate_count: int = 0
    ineligible_candidate_count: int = 0
    ineligible_count_by_reason: dict[str, int] = Field(default_factory=dict)
    candidates: list[ExtractionCandidateStatusResponse] = Field(default_factory=list)
    identity_validation: Literal["passed", "failed", "not_completed"]
    auto_commit_enabled: Literal[False] = False
    clinician_adjudication_required: Literal[True] = True
    extracted_fields: list[ExtractedField] = Field(default_factory=list)
    created_at: str


def _parse_uuid(id_str: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(id_str))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "INVALID_UUID",
                "message": "identifier must be a valid UUID",
            },
        ) from exc


VALID_RISK_LEVELS = {"LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "CRITICAL_RISK"}
ALLOWED_COMMIT_STATUSES = {"approved", "edited"}


def _assert_candidate_authorization_binding(
    *,
    candidate: ExtractionCandidateRecord,
    job: ExtractionJob,
    provider: ProviderContext,
) -> None:
    """Defense-in-depth before decrypting any staged candidate PHI."""
    if (
        candidate.job_id != job.id
        or candidate.source_document_id != job.document_id
        or candidate.patient_id != job.patient_id
        or candidate.tenant_id != job.tenant_id
        or candidate.authorization_provider_id != provider.actor_uid
    ):
        raise HTTPException(
            status_code=403,
            detail={"error_code": "CANDIDATE_AUTHORIZATION_MISMATCH"},
        )


def _validate_commit_field_metadata(field: dict[str, Any]) -> None:
    if (
        "confidence" not in field
        or "risk_level" not in field
        or field.get("confidence") is None
        or not field.get("risk_level")
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No extracted medical field may be saved without confidence and risk_level metadata.",
        )

    confidence = field["confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not (0.0 <= confidence <= 1.0)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid confidence score.",
        )

    if field["risk_level"] not in VALID_RISK_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid risk_level.",
        )


# ── Upload (client provides patient_id — new entity) ────────────────────────


def _validated_upload_type(
    filename: str, content_type: str, data: bytes
) -> tuple[str, str]:
    safe_name = os.path.basename(filename.replace("\\", "/"))[:255]
    ext = os.path.splitext(safe_name)[1].lower()
    allowed = {
        ".pdf": ("application/pdf", lambda b: b.startswith(b"%PDF-")),
        ".png": ("image/png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
        ".jpg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
        ".jpeg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    }
    if ext not in allowed:
        raise HTTPException(
            status_code=415, detail={"error_code": "UNSUPPORTED_DOCUMENT_TYPE"}
        )
    expected, check = allowed[ext]
    if content_type != expected or not check(data):
        raise HTTPException(
            status_code=415, detail={"error_code": "DOCUMENT_TYPE_MISMATCH"}
        )
    return safe_name, expected


async def _run_extraction_job(job_id: str) -> None:
    async with get_session_factory()() as task_db:
        await process_extraction_job(job_id, task_db)


@router.post("/documents/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_pipeline_document(
    background_tasks: BackgroundTasks,
    patient_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    source_system: str = Form(default="provider_web"),
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: AsyncSession = Depends(get_db_session),
):
    """Store an authorized, explicitly patient-bound document before queuing extraction."""
    capability = await authorize_document_processing(
        token=x_consent_token,
        patient_id=str(patient_id),
        provider=provider,
        operation=DocumentProcessingOperation.UPLOAD_DOCUMENT,
    )
    configured_max = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
    extraction_config = get_document_extraction_config()
    max_bytes = (
        min(configured_max, TEXTRACT_MAX_SYNC_BYTES)
        if extraction_config.provider == "aws_textract"
        else configured_max
    )
    data = await file.read(max_bytes + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=400, detail={"error_code": "EMPTY_DOCUMENT"})
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413, detail={"error_code": "DOCUMENT_TOO_LARGE"}
        )
    fname, mime_type = _validated_upload_type(
        file.filename or "", file.content_type or "", data
    )

    pid_uuid = patient_id
    doc_uuid = uuid.uuid4()
    job_uuid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    tenant_id = provider.hospital.hospital_id
    request_id = idempotency_key or str(uuid.uuid4())
    storage = get_document_storage()
    try:
        stored = await storage.put_document(
            data,
            tenant_id=str(tenant_id),
            patient_id=str(pid_uuid),
            mime_type=mime_type,
        )
    finally:
        del data

    existing = (
        await db.execute(
            select(DocumentStorage).where(
                DocumentStorage.tenant_id == tenant_id,
                DocumentStorage.patient_id == pid_uuid,
                DocumentStorage.content_hash == stored.content_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await storage.delete_document(
            stored.storage_ref, tenant_id=str(tenant_id), patient_id=str(pid_uuid)
        )
        existing_job = (
            (
                await db.execute(
                    select(ExtractionJob)
                    .where(ExtractionJob.document_id == existing.id)
                    .order_by(ExtractionJob.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        return {
            "job_id": str(existing_job.id) if existing_job else None,
            "patient_id": str(pid_uuid),
            "filename": fname,
            "status": existing_job.status if existing_job else "uploaded",
            "duplicate": True,
        }
    ds = DocumentStorage(
        id=doc_uuid,
        patient_id=pid_uuid,
        tenant_id=tenant_id,
        uploader_id=provider.actor_uid,
        storage_ref=stored.storage_ref,
        content_type=mime_type,
        size=stored.size,
        content_hash=stored.content_hash,
        original_filename=fname,
        upload_purpose="ai_document_ingestion",
        consent_session_id=getattr(capability, "request_id", None),
        source_system=source_system[:64],
        uploaded_at=now,
    )
    ej = ExtractionJob(
        id=job_uuid,
        patient_id=pid_uuid,
        tenant_id=tenant_id,
        uploader_id=provider.actor_uid,
        authorization_provider_id=provider.actor_uid,
        consent_request_id=str(getattr(capability, "request_id", "")),
        document_id=doc_uuid,
        document_type=mime_type,
        status="extraction_pending",
        request_id=request_id[:64],
        created_at=now,
    )
    db.add(ds)
    db.add(ej)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        await storage.delete_document(
            stored.storage_ref, tenant_id=str(tenant_id), patient_id=str(pid_uuid)
        )
        raise

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PIPELINE),
        actor_uid=provider.actor_uid,
        event_type="DOCUMENT_UPLOADED",
        target_id=str(doc_uuid),
        status="SUCCESS",
        metadata={
            "job_id": str(job_uuid),
            "patient_id": str(pid_uuid),
            "tenant_id": str(tenant_id),
            "request_id": request_id,
            "size": stored.size,
            "mime_type": mime_type,
        },
    )
    background_tasks.add_task(_run_extraction_job, str(job_uuid))

    return {
        "job_id": str(job_uuid),
        "patient_id": str(pid_uuid),
        "filename": fname,
        "status": "extraction_pending",
        "duplicate": False,
    }


# ── Job status (server-derived patient_id from job entity) ──────────────────


@router.get("/jobs/{job_id}", response_model=ExtractionJobStatusResponse)
async def get_extraction_job(
    job_id: str,
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve extraction job status and summary of auto-approved vs review fields.

    ALPHA: patient_id is derived server-side from the job's DB row, not from
    client-provided query params or headers.  This eliminates the spoofing
    vector where a client claims a different patient_id than the job belongs to.
    """
    job_uuid = _parse_uuid(job_id)

    stmt_j = select(ExtractionJob).where(ExtractionJob.id == job_uuid)
    res_j = await db.execute(stmt_j)
    job = res_j.scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Extraction job not found.",
        )
    if job.tenant_id is not None and job.tenant_id != provider.hospital.hospital_id:
        raise HTTPException(
            status_code=403, detail={"error_code": "CROSS_TENANT_JOB_ACCESS"}
        )

    # ALPHA: Derive patient_id server-side from the job entity
    capability = await authorize_document_processing(
        token=x_consent_token,
        patient_id=str(job.patient_id),
        provider=provider,
        operation=DocumentProcessingOperation.READ_JOB_STATUS,
        consent_request_id=job.consent_request_id,
    )
    assert_job_authorization_binding(job=job, capability=capability, provider=provider)
    pid = capability.patient_id

    candidate_rows = (
        (
            await db.execute(
                select(ExtractionCandidateRecord)
                .where(
                    ExtractionCandidateRecord.job_id == job_uuid,
                    ExtractionCandidateRecord.patient_id == job.patient_id,
                    ExtractionCandidateRecord.tenant_id == job.tenant_id,
                    ExtractionCandidateRecord.authorization_provider_id
                    == provider.actor_uid,
                    ExtractionCandidateRecord.source_document_id == job.document_id,
                )
                .order_by(ExtractionCandidateRecord.created_at)
            )
        )
        .scalars()
        .all()
    )
    eligible_rows = [
        row for row in candidate_rows if getattr(row, "routing_eligible", True)
    ]
    ineligible_rows = [
        row for row in candidate_rows if not getattr(row, "routing_eligible", True)
    ]
    ineligible_by_reason: dict[str, int] = {}
    for row in ineligible_rows:
        reason = getattr(row, "eligibility_reason_code", None)
        if isinstance(reason, str):
            ineligible_by_reason[reason] = ineligible_by_reason.get(reason, 0) + 1
    kms = get_encryption_provider()
    candidates = []
    try:
        for row in eligible_rows:
            _assert_candidate_authorization_binding(
                candidate=row, job=job, provider=provider
            )
            value_context = f"extraction_candidate_value:{row.evidence_id}"
            source_context = f"extraction_candidate_source:{row.evidence_id}"
            raw_value = await kms.decrypt_field(
                str(job.patient_id),
                value_context,
                EncryptedField.deserialize(row.encrypted_raw_value, value_context),
                db,
            )
            source_text = None
            if row.encrypted_source_text:
                source_text = await kms.decrypt_field(
                    str(job.patient_id),
                    source_context,
                    EncryptedField.deserialize(
                        row.encrypted_source_text, source_context
                    ),
                    db,
                )
            candidates.append(
                {
                    "field_name": row.field_name,
                    "raw_value": raw_value,
                    "field_confidence": row.field_confidence,
                    "source_page": row.source_page,
                    "source_text": source_text,
                    "source_bbox": row.source_bbox,
                    "evidence_complete": row.evidence_complete,
                    "lane": row.lane,
                    "reason_codes": row.reason_codes,
                }
            )
    except (EncryptionError, PatientDataErased) as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "CANDIDATE_EVIDENCE_UNAVAILABLE"},
        ) from exc
    lane = None
    reason_codes: list[str] = []
    if candidate_rows:
        lane = (
            "QUARANTINE"
            if any(row.lane == "QUARANTINE" for row in candidate_rows)
            else "SOURCE_ONLY"
        )
        reason_codes = sorted(
            {reason for row in candidate_rows for reason in (row.reason_codes or [])}
        )
    elif job.status == "source_only":
        lane = "SOURCE_ONLY"
        reason_codes = ["NO_CLINICAL_CANDIDATES"]
    elif job.status == "quarantined":
        lane = "QUARANTINE"
        reason_codes = [job.error_code] if job.error_code else []

    identity_validation = (
        "failed"
        if job.status == "identity_mismatch"
        else "passed"
        if job.status
        in {
            "extracted",
            "validation_pending",
            "source_only",
            "quarantined",
        }
        else "not_completed"
    )

    return {
        "job_id": job_id,
        "patient_id": pid,
        "status": job.status,
        "document_type": job.document_type,
        "provider": job.extractor_provider,
        "provider_version": job.extractor_version,
        "document_confidence": (
            candidate_rows[0].document_confidence if candidate_rows else None
        ),
        "routing_lane": lane,
        "routing_reasons": reason_codes,
        "candidate_count": len(candidates),
        "eligible_candidate_count": len(candidates),
        "ineligible_candidate_count": len(ineligible_rows),
        "ineligible_count_by_reason": dict(sorted(ineligible_by_reason.items())),
        "candidates": candidates,
        "identity_validation": identity_validation,
        "auto_commit_enabled": False,
        "clinician_adjudication_required": True,
        "extracted_fields": [],
        "created_at": job.created_at.isoformat(),
    }


@router.get("/jobs/{job_id}/document", status_code=status.HTTP_200_OK)
async def get_extraction_job_document(
    job_id: str,
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    """Stream an authorized original document without exposing storage metadata."""
    job_uuid = _parse_uuid(job_id)
    job = (
        await db.execute(select(ExtractionJob).where(ExtractionJob.id == job_uuid))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=404, detail={"error_code": "DOCUMENT_NOT_FOUND"}
        )
    capability = await authorize_document_processing(
        token=x_consent_token,
        patient_id=str(job.patient_id),
        provider=provider,
        operation=DocumentProcessingOperation.READ_DOCUMENT_SOURCE,
        consent_request_id=job.consent_request_id,
    )
    assert_job_authorization_binding(job=job, capability=capability, provider=provider)
    document = (
        await db.execute(
            select(DocumentStorage).where(DocumentStorage.id == job.document_id)
        )
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=404, detail={"error_code": "DOCUMENT_NOT_FOUND"}
        )
    try:
        content = await get_document_storage().get_document_bytes(
            document.storage_ref,
            tenant_id=str(job.tenant_id),
            patient_id=str(job.patient_id),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "DOCUMENT_STORAGE_UNAVAILABLE"},
        ) from exc
    filename = os.path.basename(document.original_filename or "document")
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PIPELINE),
        actor_uid=provider.actor_uid,
        event_type="DOCUMENT_SOURCE_VIEWED",
        target_id=str(document.id),
        status="SUCCESS",
        metadata={
            "patient_id": str(job.patient_id),
            "provider_id": provider.actor_uid,
            "hospital_id": str(job.tenant_id),
            "consent_request_id": job.consent_request_id,
            "job_id": str(job.id),
            "document_id": str(document.id),
        },
    )
    return Response(
        content=content,
        media_type=document.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


# ── Review queue (client provides patient_id as filter) ─────────────────────


@router.get("/review-queue", status_code=status.HTTP_200_OK)
async def get_review_queue(
    patient_id: str | None = None,
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    """List flagged extracted fields requiring human steward review."""
    if not patient_id:
        raise HTTPException(
            status_code=422, detail={"error_code": "PATIENT_ID_REQUIRED"}
        )
    capability = await authorize_document_processing(
        token=x_consent_token,
        patient_id=patient_id,
        provider=provider,
        operation=DocumentProcessingOperation.REVIEW_EXTRACTED_FIELDS,
    )
    pid = capability.patient_id
    pid_uuid = _parse_uuid(pid)

    stmt_q = select(ReviewQueueItem).where(
        ReviewQueueItem.status == "pending", ReviewQueueItem.patient_id == pid_uuid
    )
    res_q = await db.execute(stmt_q)
    q_items = res_q.scalars().all()
    authorized_items = []
    for queue_item in q_items:
        job = (
            await db.execute(
                select(ExtractionJob).where(ExtractionJob.id == queue_item.job_id)
            )
        ).scalar_one_or_none()
        if job is None:
            continue
        try:
            assert_job_authorization_binding(
                job=job, capability=capability, provider=provider
            )
        except HTTPException:
            continue
        authorized_items.append(queue_item)

    items = [
        {
            "review_item_id": str(qi.id),
            "job_id": str(qi.job_id),
            "patient_id": pid,
            "document_title": "Clinical document review",
            "flagged_fields_count": 1,
            "highest_risk_level": "MEDIUM_RISK",
            "queued_at": qi.queued_at.isoformat(),
        }
        for qi in authorized_items
    ]
    return {"items": items}


# ── Field review (server-derived patient_id from field → job chain) ─────────


@router.post("/fields/{field_id}/review", status_code=status.HTTP_200_OK)
async def review_extracted_field(
    field_id: str,
    payload: FieldReviewRequest,
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    """Adjudicate (approve/reject/edit) an extracted clinical observation.

    ALPHA: patient_id is derived server-side from the field's parent
    ExtractionJob, not from client-provided values.
    """
    f_uuid = _parse_uuid(field_id)
    stmt_f = (
        select(ExtractedFieldRecord)
        .where(ExtractedFieldRecord.id == f_uuid)
        .with_for_update()
    )
    res_f = await db.execute(stmt_f)
    field = res_f.scalar_one_or_none()

    # ALPHA: Derive patient_id server-side from the field's parent job
    server_patient_id: str | None = None
    if field:
        stmt_j = select(ExtractionJob).where(ExtractionJob.id == field.job_id)
        res_j = await db.execute(stmt_j)
        job = res_j.scalar_one_or_none()
        if job:
            server_patient_id = str(job.patient_id)
            if (
                job.tenant_id is not None
                and job.tenant_id != provider.hospital.hospital_id
            ):
                raise HTTPException(
                    status_code=403, detail={"error_code": "CROSS_TENANT_JOB_ACCESS"}
                )
    else:
        raise HTTPException(status_code=404, detail="Extracted field not found")

    # ALPHA: Consent validation raises HTTPException on failure
    capability = await authorize_document_processing(
        token=x_consent_token,
        patient_id=server_patient_id,
        provider=provider,
        operation=DocumentProcessingOperation.ADJUDICATE_EXTRACTED_FIELD,
        consent_request_id=job.consent_request_id,
    )
    assert_job_authorization_binding(job=job, capability=capability, provider=provider)

    if payload.action is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Adjudication action is required.",
        )

    status_map = {
        "approve": "approved",
        "reject": "rejected",
        "edit": "edited",
        "approved": "approved",
        "rejected": "rejected",
        "edited": "edited",
    }
    if payload.action not in status_map:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid adjudication action.",
        )

    new_st = status_map[payload.action]

    if not set(provider.affiliation.roles or []).intersection(
        {"clinician", "clinical_reviewer", "admin"}
    ):
        raise HTTPException(
            status_code=403, detail={"error_code": "REVIEW_ROLE_REQUIRED"}
        )
    if field.status != "needs_review":
        raise HTTPException(
            status_code=409, detail={"error_code": "STALE_REVIEW_DECISION"}
        )
    if payload.version is not None and payload.version != field.review_version:
        raise HTTPException(
            status_code=409, detail={"error_code": "STALE_REVIEW_VERSION"}
        )

    if field:
        field.status = new_st
        field.review_version += 1
        if payload.corrected_value:
            field.corrected_value = payload.corrected_value
        if payload.units is not None:
            field.units = payload.units.strip() or None

        if new_st == "edited" and payload.corrected_value:
            fc = FieldCorrection(
                id=uuid.uuid4(),
                field_id=f_uuid,
                job_id=field.job_id,
                field_name=field.field_name,
                original_value=field.raw_value,
                corrected_value=payload.corrected_value,
                confidence=field.confidence,
                corrected_by=provider.actor_uid,
                corrected_at=datetime.now(timezone.utc),
            )
            db.add(fc)

        stmt_qi = select(ReviewQueueItem).where(
            ReviewQueueItem.field_id == f_uuid, ReviewQueueItem.status == "pending"
        )
        res_qi = await db.execute(stmt_qi)
        qi = res_qi.scalar_one_or_none()
        if qi:
            qi.status = "adjudicated"
            qi.adjudicated_by = provider.actor_uid
            qi.adjudicated_at = datetime.now(timezone.utc)
            qi.notes = payload.review_notes

        await db.commit()

    ev_type = (
        "FIELD_APPROVED"
        if new_st == "approved"
        else ("FIELD_REJECTED" if new_st == "rejected" else "FIELD_EDITED")
    )
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PIPELINE),
        actor_uid=provider.actor_uid,
        event_type=ev_type,
        target_id=field_id,
        status="SUCCESS",
        metadata={"action": payload.action, "new_status": new_st},
    )

    return {
        "field_id": field_id,
        "job_id": str(field.job_id),
        "previous_status": "needs_review",
        "new_status": new_st,
        "final_value": payload.corrected_value
        or field.corrected_value
        or field.raw_value,
        "adjudicated_by": provider.actor_uid,
        "adjudicated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/fields/{field_id}/approve", status_code=status.HTTP_200_OK)
async def approve_extracted_field(
    field_id: str,
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    return await review_extracted_field(
        field_id, FieldReviewRequest(action="approve"), provider, x_consent_token, db
    )


@router.post("/fields/{field_id}/reject", status_code=status.HTTP_200_OK)
async def reject_extracted_field(
    field_id: str,
    payload: RejectFieldRequest | None = None,
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    return await review_extracted_field(
        field_id,
        FieldReviewRequest(
            action="reject", review_notes=payload.reason if payload else None
        ),
        provider,
        x_consent_token,
        db,
    )


@router.post("/fields/{field_id}/edit", status_code=status.HTTP_200_OK)
async def edit_extracted_field(
    field_id: str,
    payload: EditFieldRequest,
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    return await review_extracted_field(
        field_id,
        FieldReviewRequest(
            action="edit",
            corrected_value=payload.corrected_value,
            units=payload.units,
            version=payload.version,
        ),
        provider,
        x_consent_token,
        db,
    )


# ── Job commit (server-derived patient_id from job entity) ──────────────────


@router.post("/jobs/{job_id}/commit", status_code=status.HTTP_201_CREATED)
async def commit_extraction_job(
    job_id: str,
    payload: CommitJobRequest,
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    """Commit adjudicated extraction job to permanent storage and timeline.

    ALPHA: patient_id is derived server-side from the job's DB row.  If
    payload.patient_id doesn't match the job's actual patient_id, the request
    is rejected with 400.  This prevents writing records under the wrong patient.
    """
    job_uuid = _parse_uuid(job_id)

    if payload.fields is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "CLIENT_SUPPLIED_COMMIT_FIELDS_FORBIDDEN",
                "message": "Commit fields are loaded from the reviewed server-side staging records.",
            },
        )
    if payload.encounter_summary is not None:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "CLIENT_SUPPLIED_CLINICAL_SUMMARY_FORBIDDEN",
                "message": "Commit summaries are generated from reviewed server-side state.",
            },
        )

    # 1. Load the job first to derive patient_id server-side
    stmt_job = (
        select(ExtractionJob).where(ExtractionJob.id == job_uuid).with_for_update()
    )
    res_job = await db.execute(stmt_job)
    job = res_job.scalar_one_or_none()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Extraction job not found.",
        )
    if job.tenant_id is not None and job.tenant_id != provider.hospital.hospital_id:
        raise HTTPException(
            status_code=403, detail={"error_code": "CROSS_TENANT_JOB_ACCESS"}
        )
    if job.status == "committed":
        raise HTTPException(
            status_code=409, detail={"error_code": "JOB_ALREADY_COMMITTED"}
        )
    if job.status == "source_only":
        raise HTTPException(
            status_code=409, detail={"error_code": "SOURCE_ONLY_NOT_COMMITTABLE"}
        )
    if job.status == "quarantined":
        raise HTTPException(
            status_code=409, detail={"error_code": "QUARANTINED_JOB_NOT_COMMITTABLE"}
        )
    if job.status not in {"review_pending", "ready_for_commit"}:
        raise HTTPException(
            status_code=409, detail={"error_code": "JOB_NOT_READY_FOR_COMMIT"}
        )

    # 2. ALPHA: Validate consent using server-derived patient_id (raises on failure)
    capability = await authorize_document_processing(
        token=x_consent_token,
        patient_id=str(job.patient_id),
        provider=provider,
        operation=DocumentProcessingOperation.COMMIT_VERIFIED_FIELDS,
        consent_request_id=job.consent_request_id,
    )
    assert_job_authorization_binding(job=job, capability=capability, provider=provider)

    # 3. Verify payload.patient_id matches the job's actual patient_id
    server_pid = str(job.patient_id)
    if not payload.patient_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="patient_id is required for pipeline commit.",
        )
    if str(_parse_uuid(payload.patient_id)) != str(job.patient_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="patient_id in request body does not match the job's patient_id.",
        )

    # 4. Check for unresolved fields
    stmt_unres = select(ExtractedFieldRecord).where(
        ExtractedFieldRecord.job_id == job_uuid,
        ExtractedFieldRecord.status == "needs_review",
    )
    res_unres = await db.execute(stmt_unres)
    unres_rows = res_unres.scalars().all()
    if isinstance(unres_rows, list) and len(unres_rows) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review incomplete: job contains unresolved fields needing review.",
        )

    approved_models = []

    stmt_app = select(ExtractedFieldRecord).where(
        ExtractedFieldRecord.job_id == job_uuid,
        ExtractedFieldRecord.status.in_(["approved", "edited"]),
    )
    res_app = await db.execute(stmt_app)
    for rec in res_app.scalars().all():
        approved_models.append(
            ExtractedField(
                field_id=str(rec.id),
                job_id=str(rec.job_id),
                field_name=rec.field_name,
                raw_value=rec.raw_value,
                normalized_value=rec.normalized_value,
                units=rec.units,
                confidence=rec.confidence,
                risk_level=rec.risk_level,
                source_document_id=str(rec.source_document_id or job_id),
                status=rec.status,
                corrected_value=rec.corrected_value,
            )
        )

    audit_context = current_audit_context(AuditDomain.PIPELINE)
    audit_metadata = {
        "workflow_id": job.consent_request_id,
        "request_id": job.request_id,
        "document_id": str(job.document_id),
        "patient_id": server_pid,
        "tenant_id": str(job.tenant_id) if job.tenant_id is not None else None,
    }
    cnt = len(approved_models)
    committed_at = datetime.now(timezone.utc)
    tl = TimelineEvent(
        patient_id=job.patient_id,
        event_type="PIPELINE_COMMIT",
        event_ref_id=job_uuid,
        occurred_at=committed_at,
        source="ai_pipeline",
        summary="Reviewed document extraction committed",
    )

    try:
        # The route owns the only commit: clinical rows, timelines, job state,
        # commit marker, and every required audit event succeed or roll back together.
        if approved_models:
            await ingest_extracted_fields(
                patient_id=server_pid,
                job_id=job_id,
                approved_fields=approved_models,
                db=db,
                committed_by=provider.actor_uid,
                audit_context=audit_context,
                audit_metadata=audit_metadata,
            )

        job.status = "committed"
        db.add(tl)
        await enqueue_audit_event(
            db,
            audit_context=audit_context,
            idempotency_key=f"pipeline:{job_id}:committed",
            actor_id=provider.actor_uid,
            event_type="JOB_COMMITTED",
            target_id=job_id,
            patient_id=server_pid,
            metadata={"fields_committed": cnt, **audit_metadata},
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "CLINICAL_COMMIT_AUDIT_UNAVAILABLE"},
        ) from exc

    return {
        "job_id": job_id,
        "patient_id": server_pid,
        "status": "committed",
        "fields_committed": cnt,
        "committed_fields_count": cnt,
        "timeline_event_id": str(tl.id) if tl.id is not None else None,
        "ledger_tx_hash": None,
        "committed_at": committed_at.isoformat(),
    }


def _adjudication_failure(exc: AdjudicationError) -> HTTPException:
    not_found = {
        "ADJUDICATION_CASE_NOT_FOUND",
        "ADJUDICATION_JOB_NOT_FOUND",
        "ADJUDICATION_ROUTE_NOT_FOUND",
        "ADJUDICATION_SOURCE_NOT_FOUND",
        "ADJUDICATION_SUBMISSION_NOT_FOUND",
    }
    denied = {
        "ADJUDICATION_ACCESS_DENIED",
        "ADJUDICATION_ROLE_REQUIRED",
        "ADJUDICATION_CONSENT_INACTIVE",
        "ADJUDICATION_ERASURE_ACCESS_BLOCKED",
    }
    malformed = {
        "ADJUDICATION_SESSION_INVALID",
        "ADJUDICATION_IDEMPOTENCY_KEY_INVALID",
        "ADJUDICATION_PAYLOAD_INVALID",
    }
    return HTTPException(
        status_code=404
        if exc.code in not_found
        else 403
        if exc.code in denied
        else 422
        if exc.code in malformed
        else 503
        if exc.code == "ADJUDICATION_ERASURE_REGISTRY_UNAVAILABLE"
        else 409,
        detail={"error_code": exc.code},
    )


async def _finish_adjudication_failure(
    db: AsyncSession, exc: AdjudicationError
) -> None:
    if exc.code in {
        "ADJUDICATION_ERASURE_ACCESS_BLOCKED",
        "ADJUDICATION_ERASURE_REGISTRY_UNAVAILABLE",
    }:
        await db.commit()
    else:
        await db.rollback()


def _case_response(case: AdjudicationCaseRecord) -> dict[str, Any]:
    return {
        "case_id": str(case.id),
        "patient_id": str(case.patient_id),
        "tenant_id": str(case.tenant_id),
        "source_document_id": str(case.source_document_id),
        "job_id": str(case.job_id),
        "routing_id": str(case.routing_id) if case.routing_id else None,
        "decision_id": str(case.decision_id) if case.decision_id else None,
        "reviewer_id": case.reviewer_id,
        "reviewer_role": case.reviewer_role,
        "status": case.status,
        "version": case.version,
        "created_at": case.created_at,
        "resolved_at": case.resolved_at,
        "clinical_committed_at": case.clinical_committed_at,
    }


@router.post("/routing/{routing_id}/adjudication-cases", status_code=201)
async def create_routing_adjudication_case(
    routing_id: str,
    payload: CreateAdjudicationCaseRequest,
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        case = await create_adjudication_case(
            db,
            provider=provider,
            routing_id=_parse_uuid(routing_id),
            idempotency_key=payload.idempotency_key,
            review_session_id=payload.review_session_id,
        )
        await db.commit()
        return _case_response(case)
    except AdjudicationError as exc:
        await _finish_adjudication_failure(db, exc)
        raise _adjudication_failure(exc) from exc
    except Exception:
        await db.rollback()
        raise


@router.post("/jobs/{job_id}/document-adjudication-cases", status_code=201)
async def create_document_adjudication_case(
    job_id: str,
    payload: CreateAdjudicationCaseRequest,
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        case = await create_adjudication_case(
            db,
            provider=provider,
            job_id=_parse_uuid(job_id),
            idempotency_key=payload.idempotency_key,
            review_session_id=payload.review_session_id,
        )
        await db.commit()
        return _case_response(case)
    except AdjudicationError as exc:
        await _finish_adjudication_failure(db, exc)
        raise _adjudication_failure(exc) from exc
    except Exception:
        await db.rollback()
        raise


@router.get("/adjudication-cases")
async def list_adjudication_cases(
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
):
    if not {"clinician", "clinical_reviewer", "admin"}.intersection(
        provider.affiliation.roles
    ):
        raise HTTPException(403, detail={"error_code": "ADJUDICATION_ROLE_REQUIRED"})
    rows = (
        await db.execute(
            select(AdjudicationCaseRecord).where(
                AdjudicationCaseRecord.tenant_id == provider.hospital.hospital_id,
                AdjudicationCaseRecord.reviewer_id == provider.actor_uid,
            )
        )
    ).scalars()
    return [_case_response(row) for row in rows]


@router.get("/adjudication-cases/{case_id}")
async def get_adjudication_case(
    case_id: str,
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
):
    row = (
        await db.execute(
            select(AdjudicationCaseRecord).where(
                AdjudicationCaseRecord.id == _parse_uuid(case_id),
                AdjudicationCaseRecord.tenant_id == provider.hospital.hospital_id,
                AdjudicationCaseRecord.reviewer_id == provider.actor_uid,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, detail={"error_code": "ADJUDICATION_CASE_NOT_FOUND"})
    return _case_response(row)


@router.post("/adjudication-cases/{case_id}/recover-session")
async def recover_adjudication_session(
    case_id: str,
    payload: RecoverAdjudicationSessionRequest,
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        case = await rotate_adjudication_review_session(
            db,
            case_id=_parse_uuid(case_id),
            provider=provider,
            new_review_session_id=payload.review_session_id,
        )
        await db.commit()
        return _case_response(case)
    except AdjudicationError as exc:
        await _finish_adjudication_failure(db, exc)
        raise _adjudication_failure(exc) from exc
    except Exception:
        await db.rollback()
        raise


@router.post(
    "/adjudication-cases/{case_id}/submissions",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": AdjudicationSubmissionRequest.model_json_schema()
                }
            },
        }
    },
)
async def create_adjudication_submission(
    case_id: str,
    raw_payload: dict[str, Any] = Body(...),
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        try:
            payload = AdjudicationSubmissionRequest.model_validate(raw_payload)
        except ValidationError as exc:
            raise AdjudicationError("ADJUDICATION_PAYLOAD_INVALID") from exc
        row = await submit_adjudication_case(
            db,
            case_id=_parse_uuid(case_id),
            provider=provider,
            review_session_id=payload.review_session_id,
            outcome=payload.outcome,
            fields=[field.model_dump(mode="python") for field in payload.fields],
            reason_codes=payload.reason_codes,
            idempotency_key=payload.idempotency_key,
            supersedes_submission_id=payload.supersedes_submission_id,
        )
        await db.commit()
        return {
            "submission_id": str(row.id),
            "case_id": str(row.case_id),
            "outcome": row.outcome,
            "attempt_number": row.attempt_number,
            "content_hash": row.content_hash,
            "supersedes_submission_id": (
                str(row.supersedes_submission_id)
                if row.supersedes_submission_id
                else None
            ),
        }
    except AdjudicationError as exc:
        await _finish_adjudication_failure(db, exc)
        raise _adjudication_failure(exc) from exc
    except Exception:
        await db.rollback()
        raise


@router.post("/adjudication-submissions/{submission_id}/commit")
async def commit_human_adjudication(
    submission_id: str,
    x_review_session_id: str = Header(alias="X-Review-Session-ID"),
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        case = await commit_adjudication_submission(
            db,
            submission_id=_parse_uuid(submission_id),
            provider=provider,
            review_session_id=x_review_session_id,
        )
        await db.commit()
        return {
            "submission_id": submission_id,
            "case_id": str(case.id),
            "status": "committed",
            "committed_at": case.clinical_committed_at,
            "provenance": "human_adjudicated",
        }
    except AdjudicationError as exc:
        await _finish_adjudication_failure(db, exc)
        raise _adjudication_failure(exc) from exc
    except Exception:
        await db.rollback()
        raise


@router.get("/adjudication-cases/{case_id}/source")
async def get_adjudication_source(
    case_id: str,
    x_review_session_id: str = Header(alias="X-Review-Session-ID"),
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        content, content_type = await read_source_document(
            db,
            case_id=_parse_uuid(case_id),
            provider=provider,
            review_session_id=x_review_session_id,
        )
        await db.commit()
        return Response(
            content=content,
            media_type=content_type,
            headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"},
        )
    except AdjudicationError as exc:
        await _finish_adjudication_failure(db, exc)
        raise _adjudication_failure(exc) from exc
    except Exception:
        await db.rollback()
        raise

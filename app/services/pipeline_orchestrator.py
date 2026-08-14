"""Patient-bound, fail-closed extraction job orchestration."""

from __future__ import annotations

from app.security.audit_context import AuditContext, AuditDomain

import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.extractor import DocumentExtractionError, get_medical_document_extractor
from app.ai.identity_decision import (
    IdentityDecision,
    IdentityDecisionState,
    IdentityFieldStatus,
    decide_identity_state,
)
from app.ai.candidate_eligibility import (
    CANDIDATE_ELIGIBILITY_POLICY_VERSION,
    CandidateEligibility,
    classify_semantic_candidate,
)
from app.core.config import get_document_extraction_config
from app.models.pipeline import DocumentStorage as DocumentStorageRecord
from app.models.pipeline import (
    DocumentSourceRelationshipRecord,
    ExtractionCandidateRecord,
    ExtractionDecisionRecord,
    ExtractionJob,
)
from app.models.extraction_decision import ExtractionDecisionPolicy
from app.models.field_evidence import EvidenceIssue, SnapshotState
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
from app.services.clinical_evidence_integrity import (
    SourceRelationType,
    clinical_fact_key,
    persist_conflict_set,
)
from app.services.crypto_kms import (
    EncryptedField,
    EncryptionError,
    get_encryption_provider,
)

logger = logging.getLogger("nexa_logger")

_IDENTITY_FIELDS = ("patient_name", "phone", "aadhaar_abha_id")
_IDENTITY_ERROR_CODES = {
    IdentityDecisionState.IDENTITY_DISCREPANCY: "EXTRACTED_IDENTITY_MISMATCH",
    IdentityDecisionState.IDENTITY_CONFLICTING: "EXTRACTED_IDENTITY_MISMATCH",
    IdentityDecisionState.IDENTITY_INSUFFICIENT: "EXTRACTED_IDENTITY_UNAVAILABLE",
}
_IDENTITY_REASON_CODES = {
    IdentityDecisionState.IDENTITY_DISCREPANCY: "IDENTITY_MISMATCH",
    IdentityDecisionState.IDENTITY_CONFLICTING: "IDENTITY_MISMATCH",
    IdentityDecisionState.IDENTITY_INSUFFICIENT: "IDENTITY_UNAVAILABLE",
}
_SUPPORTED_PRIOR_EXTRACTION_STATUSES = frozenset(
    {"source_only", "quarantined", "review_pending", "ready_for_commit", "committed"}
)


def _is_postgresql_session(db: AsyncSession) -> bool:
    if not isinstance(db, AsyncSession):
        return False
    bind = db.get_bind()
    return getattr(getattr(bind, "dialect", None), "name", None) == "postgresql"


class ExtractionEvidenceInstanceCollision(RuntimeError):
    """A persisted evidence ID is bound to a different immutable instance."""


def _is_candidate_evidence_unique_violation(exc: BaseException) -> bool:
    """Recognize only the PostgreSQL evidence-ID uniqueness violation.

    SQLAlchemy may wrap the asyncpg exception, while asyncpg exposes the
    constraint directly (and some adapters expose it through ``diag``).
    Reconciliation is permitted only when both structured PostgreSQL markers
    are present; human-readable exception text is intentionally ignored.
    """
    candidates: list[BaseException] = []
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending and len(candidates) < 4:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        candidates.append(current)
        pending.extend(
            getattr(current, name, None)
            for name in ("orig", "__cause__", "__context__")
        )
    for candidate in candidates:
        sqlstate = next(
            (
                getattr(candidate, name, None)
                for name in ("sqlstate", "pgcode", "code")
                if getattr(candidate, name, None) is not None
            ),
            None,
        )
        diagnostic = getattr(candidate, "diag", None)
        constraint_name = getattr(candidate, "constraint_name", None) or getattr(
            diagnostic, "constraint_name", None
        )
        if (
            sqlstate == "23505"
            and constraint_name == "extraction_candidates_evidence_id_key"
        ):
            return True
    return False


_CANDIDATE_BINDING_FIELDS = (
    "evidence_id",
    "job_id",
    "source_document_id",
    "patient_id",
    "tenant_id",
    "authorization_provider_id",
    "field_name",
    "clinical_fact_key",
    "provider_name",
    "provider_version",
    "lane",
    "routing_eligible",
    "eligibility_reason_code",
    "eligibility_policy_version",
)


def _candidate_binding_matches(
    existing: ExtractionCandidateRecord,
    candidate: ExtractionCandidateRecord,
) -> bool:
    return all(
        getattr(existing, field, None) == getattr(candidate, field, None)
        for field in _CANDIDATE_BINDING_FIELDS
    )


async def _persist_candidate_idempotently(
    db: AsyncSession, candidate: ExtractionCandidateRecord
) -> bool:
    """Persist one candidate, accepting only an exact same-instance replay.

    The insert occurs inside a savepoint so PostgreSQL's unique constraint is
    the concurrency authority. Only its specific evidence-ID conflict is
    reloaded and compared; all other integrity failures remain failures.
    """
    # Minimal in-memory contract doubles used by pure orchestration tests do
    # not expose SQLAlchemy savepoints; retain their pre-existing append/flush
    # behavior while real AsyncSession instances use the guarded path below.
    if not hasattr(db, "begin_nested"):
        db.add(candidate)
        await db.flush()
        return False

    try:
        async with db.begin_nested():
            db.add(candidate)
            await db.flush()
    except IntegrityError as exc:
        if not _is_candidate_evidence_unique_violation(exc):
            raise
        existing = (
            await db.execute(
                select(ExtractionCandidateRecord)
                .where(ExtractionCandidateRecord.evidence_id == candidate.evidence_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existing is None:
            raise
        if not _candidate_binding_matches(existing, candidate):
            raise ExtractionEvidenceInstanceCollision("EVIDENCE_INSTANCE_ID_COLLISION")
        return True
    return False


async def _rollback_and_reload_job(
    db: AsyncSession, job_uuid: uuid.UUID
) -> ExtractionJob:
    await db.rollback()
    return (
        await db.execute(
            select(ExtractionJob).where(ExtractionJob.id == job_uuid).with_for_update()
        )
    ).scalar_one()


async def _assess_extracted_identity(
    extracted: Any, patient_id: uuid.UUID, db: AsyncSession
) -> IdentityDecision | None:
    """Compare authentic document assertions without retaining their values."""
    assertions: dict[str, list[str]] = {}
    field_evidence = getattr(extracted, "field_evidence", ()) or ()
    for field_name in _IDENTITY_FIELDS:
        authentic_values = [
            str(item.raw_value).strip()
            for item in field_evidence
            if item.canonical_field_name == field_name and str(item.raw_value).strip()
        ]
        if authentic_values:
            assertions[field_name] = authentic_values
            continue
        summary_value = str(getattr(extracted, field_name, None) or "").strip()
        if summary_value:
            assertions[field_name] = [summary_value]
    if not assertions:
        return None

    row = (
        await db.execute(
            select(NexaVault)
            .where(NexaVault.masked_internal_id == str(patient_id))
            .limit(1)
        )
    ).scalar_one_or_none()

    field_statuses: dict[str, IdentityFieldStatus] = {}
    kms = None
    for field_name, extracted_values in assertions.items():
        stored = getattr(row, field_name, None) if row is not None else None
        if not stored:
            field_statuses[field_name] = IdentityFieldStatus.MISSING
            continue
        if kms is None:
            kms = get_encryption_provider()
        canonical = await kms.decrypt_field(
            str(patient_id),
            field_name,
            EncryptedField.deserialize(stored, field_name),
            db,
        )
        normalized_canonical = re.sub(r"[^a-z0-9]", "", canonical.casefold())
        if not normalized_canonical:
            field_statuses[field_name] = IdentityFieldStatus.MISSING
            continue
        comparisons = [
            secrets.compare_digest(
                normalized_canonical,
                re.sub(r"[^a-z0-9]", "", value.casefold()),
            )
            for value in extracted_values
        ]
        if all(comparisons):
            field_statuses[field_name] = IdentityFieldStatus.EXACT
        elif any(comparisons):
            field_statuses[field_name] = IdentityFieldStatus.CONFLICTING
        else:
            field_statuses[field_name] = IdentityFieldStatus.NONMATCHING

    return decide_identity_state(
        authoritative_context_present=True,
        field_statuses=field_statuses,
    )


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
                    "clinical_fact_key": clinical_fact_key(
                        candidate.representative.canonical_field_name,
                        candidate.representative.trusted_clinical_fact_id,
                    ),
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


def _candidate_fact_value(item: dict[str, Any]) -> tuple[str, str | None]:
    """Compare only exact provider assertions; never infer clinical equivalence."""
    evidence = item.get("provider_evidence")
    if evidence is None:
        return (str(item["raw_value"]), None)
    value = evidence.normalized_value or evidence.raw_value
    unit = evidence.normalized_unit or evidence.raw_unit
    return (value, unit)


def _mark_conflicting_evidence(
    candidates: list[dict[str, Any]], evidence_records: list[Any]
) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = {}
    for index, item in enumerate(candidates):
        key = item.get("clinical_fact_key")
        if key:
            groups.setdefault(key, []).append(index)
    conflicts: dict[str, list[int]] = {}
    for key, indexes in groups.items():
        if len({_candidate_fact_value(candidates[index]) for index in indexes}) < 2:
            continue
        conflicts[key] = indexes
        for index in indexes:
            evidence = evidence_records[index]
            issues = set(evidence.clinical_value.issues)
            issues.add(EvidenceIssue.CLINICAL_VALUE_AMBIGUOUS)
            evidence_records[index] = evidence.model_copy(
                update={
                    "clinical_value": evidence.clinical_value.model_copy(
                        update={"issues": frozenset(issues)}
                    )
                }
            )
    # Different same-field assertions without one shared explicit fact key are
    # review ambiguity, not a proven durable conflict. Mark every observation
    # without guessing that repeated measurements represent the same fact.
    by_field: dict[str, list[int]] = {}
    for index, item in enumerate(candidates):
        by_field.setdefault(str(item["field_name"]), []).append(index)
    conflict_member_indexes = {
        index for indexes in conflicts.values() for index in indexes
    }
    for indexes in by_field.values():
        if len({_candidate_fact_value(candidates[index]) for index in indexes}) < 2:
            continue
        keys = {candidates[index].get("clinical_fact_key") for index in indexes}
        if len(keys) == 1 and None not in keys:
            continue
        for index in indexes:
            if index in conflict_member_indexes:
                continue
            evidence = evidence_records[index]
            issues = set(evidence.clinical_value.issues)
            issues.add(EvidenceIssue.CLINICAL_VALUE_AMBIGUOUS)
            evidence_records[index] = evidence.model_copy(
                update={
                    "clinical_value": evidence.clinical_value.model_copy(
                        update={"issues": frozenset(issues)}
                    )
                }
            )
    return conflicts


async def _resolve_source_predecessors(
    db: AsyncSession,
    *,
    job: ExtractionJob,
    candidates: list[dict[str, Any]],
) -> list[
    tuple[
        str | None,
        str | None,
        str | None,
        frozenset[EvidenceIssue],
        ExtractionCandidateRecord | None,
    ]
]:
    # Pure orchestration doubles do not implement relational source graphs.
    if not hasattr(db, "begin_nested"):
        return [(None, None, None, frozenset(), None) for _ in candidates]
    relation = (
        await db.execute(
            select(DocumentSourceRelationshipRecord).where(
                DocumentSourceRelationshipRecord.source_document_id == job.document_id
            )
        )
    ).scalar_one_or_none()
    empty = [(None, None, None, frozenset(), None) for _ in candidates]
    if relation is None:
        return empty
    if relation.tenant_id != job.tenant_id or relation.patient_id != job.patient_id:
        return [
            (None, None, None, frozenset({EvidenceIssue.SUPERSESSION_UNRESOLVED}), None)
            for _ in candidates
        ]

    resolved = []
    for item in candidates:
        key = item.get("clinical_fact_key")
        prior_graph = []
        if key:
            prior_graph = (
                await db.execute(
                    select(ExtractionCandidateRecord, ExtractionDecisionRecord)
                    .join(
                        ExtractionJob,
                        ExtractionJob.id == ExtractionCandidateRecord.job_id,
                    )
                    .join(
                        ExtractionDecisionRecord,
                        and_(
                            ExtractionDecisionRecord.evidence_id
                            == ExtractionCandidateRecord.evidence_id,
                            ExtractionDecisionRecord.job_id
                            == ExtractionCandidateRecord.job_id,
                            ExtractionDecisionRecord.source_document_id
                            == ExtractionCandidateRecord.source_document_id,
                            ExtractionDecisionRecord.tenant_id
                            == ExtractionCandidateRecord.tenant_id,
                            ExtractionDecisionRecord.patient_id
                            == ExtractionCandidateRecord.patient_id,
                        ),
                    )
                    .where(
                        ExtractionCandidateRecord.source_document_id
                        == relation.related_document_id,
                        ExtractionCandidateRecord.tenant_id == job.tenant_id,
                        ExtractionCandidateRecord.patient_id == job.patient_id,
                        ExtractionCandidateRecord.clinical_fact_key == key,
                        ExtractionJob.document_id == relation.related_document_id,
                        ExtractionJob.tenant_id == job.tenant_id,
                        ExtractionJob.patient_id == job.patient_id,
                        ExtractionJob.status.in_(_SUPPORTED_PRIOR_EXTRACTION_STATUSES),
                        ExtractionDecisionRecord.organization_id == job.tenant_id,
                        ExtractionDecisionRecord.lane == ExtractionCandidateRecord.lane,
                        ExtractionDecisionRecord.auto_commit_feature_enabled.is_(False),
                    )
                )
            ).all()
        if len(prior_graph) != 1:
            resolved.append(
                (
                    None,
                    None,
                    None,
                    frozenset({EvidenceIssue.SUPERSESSION_UNRESOLVED}),
                    None,
                )
            )
            continue
        prior_candidate, prior_decision = prior_graph[0]
        earlier_decision_id = None
        supersedes_id = None
        addendum_id = None
        if relation.relation_type == SourceRelationType.SUPERSEDES.value:
            supersedes_id = str(prior_candidate.evidence_id)
            earlier_decision_id = str(prior_decision.id)
        elif relation.relation_type == SourceRelationType.ADDENDUM_TO.value:
            addendum_id = str(prior_candidate.evidence_id)
        else:
            resolved.append(
                (
                    None,
                    None,
                    None,
                    frozenset({EvidenceIssue.SUPERSESSION_UNRESOLVED}),
                    None,
                )
            )
            continue
        resolved.append(
            (
                supersedes_id,
                addendum_id,
                earlier_decision_id,
                frozenset(),
                prior_candidate,
            )
        )
    if _is_postgresql_session(db):
        await enqueue_audit_event(
            db,
            audit_context=AuditContext.for_tenant(
                tenant_id=str(job.tenant_id), domain=AuditDomain.PIPELINE
            ),
            idempotency_key=f"source-relation-processing:{job.id}:{job.attempt_count}",
            actor_id=str(job.authorization_provider_id),
            event_type="SOURCE_RELATION_PROCESSING_DECISION",
            target_id=str(job.id),
            patient_id=str(job.patient_id),
            metadata={
                "relation_type": relation.relation_type,
                "candidate_count": len(candidates),
                "linked_count": sum(not item[3] for item in resolved),
                "unresolved_count": sum(bool(item[3]) for item in resolved),
            },
        )
    return resolved


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
        identity_decision = await _assess_extracted_identity(
            extracted, job.patient_id, db
        )
        identity_state = identity_decision.state if identity_decision else None
        identity_quarantine = (
            identity_state is not None
            and identity_state is not IdentityDecisionState.IDENTITY_CONFIRMED
        )
        identity_error_code = (
            _IDENTITY_ERROR_CODES.get(identity_state) if identity_state else None
        )
        identity_reason_code = (
            _IDENTITY_REASON_CODES.get(identity_state) if identity_state else None
        )

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
        source_predecessors = await _resolve_source_predecessors(
            db, job=job, candidates=candidates
        )
        evidence_records = []
        earlier_decision_ids: list[str | None] = []
        prior_candidates: list[ExtractionCandidateRecord | None] = []
        for item, predecessor in zip(candidates, source_predecessors, strict=True):
            (
                supersedes_id,
                addendum_id,
                earlier_decision_id,
                lifecycle_issues,
                prior_candidate,
            ) = predecessor
            earlier_decision_ids.append(earlier_decision_id)
            prior_candidates.append(prior_candidate)
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
                        document_identity_state=identity_state,
                        supersedes_evidence_id=supersedes_id,
                        addendum_to_evidence_id=addendum_id,
                        lifecycle_issues=lifecycle_issues,
                    ),
                )
            )

        conflict_groups = _mark_conflicting_evidence(candidates, evidence_records)
        kms = get_encryption_provider()
        cross_source_conflicts: dict[str, tuple[int, ExtractionCandidateRecord]] = {}
        for index, prior_candidate in enumerate(prior_candidates):
            if prior_candidate is None:
                continue
            prior_raw = await kms.decrypt_field(
                str(job.patient_id),
                f"extraction_candidate_value:{prior_candidate.evidence_id}",
                EncryptedField.deserialize(
                    prior_candidate.encrypted_raw_value,
                    f"extraction_candidate_value:{prior_candidate.evidence_id}",
                ),
                db,
            )
            if prior_raw == evidence_records[index].clinical_value.raw_value:
                continue
            key = candidates[index].get("clinical_fact_key")
            if key:
                cross_source_conflicts[key] = (index, prior_candidate)
                evidence = evidence_records[index]
                issues = set(evidence.clinical_value.issues)
                issues.add(EvidenceIssue.CLINICAL_VALUE_AMBIGUOUS)
                evidence_records[index] = evidence.model_copy(
                    update={
                        "clinical_value": evidence.clinical_value.model_copy(
                            update={"issues": frozenset(issues)}
                        )
                    }
                )

        if not evidence_records:
            job.status = (
                "quarantined"
                if identity_quarantine
                else (
                    "source_only" if consent_active and erasure_clear else "quarantined"
                )
            )
            if job.status == "quarantined":
                job.error_code = (
                    identity_error_code or "LIVE_PROCESSING_AUTHORIZATION_BLOCKED"
                )
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
                    "reason_code": identity_reason_code,
                    "identity_state": identity_state.value if identity_state else None,
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
        for item, evidence, earlier_decision_id in zip(
            candidates, evidence_records, earlier_decision_ids, strict=True
        ):
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
                        routed_at
                        if identity_quarantine
                        or not consent_active
                        or not erasure_clear
                        else None
                    ),
                    earlier_decision_id=earlier_decision_id,
                )
            )

        persisted_candidates: dict[int, ExtractionCandidateRecord] = {}
        for index, (item, evidence, result) in enumerate(
            zip(candidates, evidence_records, results, strict=True)
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
            candidate_row = ExtractionCandidateRecord(
                id=uuid.uuid4(),
                evidence_id=evidence_uuid,
                job_id=job.id,
                source_document_id=job.document_id,
                patient_id=job.patient_id,
                tenant_id=job.tenant_id,
                authorization_provider_id=str(job.authorization_provider_id),
                field_name=evidence.clinical_value.field_name,
                clinical_fact_key=item.get("clinical_fact_key"),
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
            idempotent_candidate = await _persist_candidate_idempotently(
                db,
                candidate_row,
            )
            if idempotent_candidate:
                candidate_row = (
                    await db.execute(
                        select(ExtractionCandidateRecord).where(
                            ExtractionCandidateRecord.evidence_id == evidence_uuid
                        )
                    )
                ).scalar_one()
            persisted_candidates[index] = candidate_row

        for fact_key, indexes in conflict_groups.items():
            member_rows = [persisted_candidates[index] for index in indexes]
            await persist_conflict_set(
                db,
                tenant_id=job.tenant_id,
                patient_id=job.patient_id,
                job_id=job.id,
                source_document_id=job.document_id,
                field_name=member_rows[0].field_name,
                fact_key=fact_key,
                candidates=member_rows,
                created_at=routed_at,
            )
        for fact_key, (index, prior_candidate) in cross_source_conflicts.items():
            current_candidate = persisted_candidates[index]
            await persist_conflict_set(
                db,
                tenant_id=job.tenant_id,
                patient_id=job.patient_id,
                job_id=job.id,
                source_document_id=job.document_id,
                field_name=current_candidate.field_name,
                fact_key=fact_key,
                candidates=[prior_candidate, current_candidate],
                created_at=routed_at,
                related_document_id=prior_candidate.source_document_id,
            )

        quarantine_count = sum(
            result.routing.lane == "QUARANTINE" for result in results
        )
        source_only_count = sum(
            result.routing.lane == "SOURCE_ONLY" for result in results
        )
        job.status = "quarantined" if quarantine_count else "source_only"
        job.error_code = (
            identity_error_code
            if identity_quarantine
            else "EXTRACTION_EVIDENCE_QUARANTINED"
            if quarantine_count
            else None
        )
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
                "lane": "QUARANTINE" if quarantine_count else "SOURCE_ONLY",
                "candidate_count": len(candidates),
                "source_only_count": source_only_count,
                "quarantine_count": quarantine_count,
                "identity_state": identity_state.value if identity_state else None,
                "reason_code": identity_reason_code,
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
    except ExtractionEvidenceInstanceCollision:
        job = await _rollback_and_reload_job(db, job_uuid)
        job.status = "extraction_failed_terminal"
        job.error_code = "EVIDENCE_INSTANCE_ID_COLLISION"
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

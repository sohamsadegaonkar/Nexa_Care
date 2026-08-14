"""Durable clinical-conflict and source-provenance integrity boundaries."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import (
    DocumentSourceRelationshipRecord,
    DocumentStorage,
    ExtractionCandidateRecord,
    ExtractionConflictMemberRecord,
    ExtractionConflictRecord,
    ExtractionJob,
)
from app.security.audit_context import AuditContext, AuditDomain
from app.security.document_processing_policy import DocumentProcessingOperation
from app.security.erasure_registry import (
    ErasureRegistryUnavailable,
    _PatientErasedSignal,
    check_erasure_registry,
)
from app.services.approved_access_capability import (
    ApprovedAccessStoreUnavailable,
    validate_live_document_processing_request,
)
from app.services.audit_outbox import enqueue_audit_event

CLINICAL_FACT_KEY_VERSION = "clinical-fact-key/1.0"
MAX_SOURCE_RELATION_DEPTH = 64
SOURCE_RELATION_LOCK_VERSION = "nexa-source-relation-graph:v1"
CLINICAL_CONFLICT_LOCK_VERSION = "nexa-clinical-conflict-graph:v1"


class ClinicalEvidenceIntegrityError(RuntimeError):
    """Stable, value-free integrity failure."""


class SourceRelationType(StrEnum):
    SUPERSEDES = "SUPERSEDES"
    ADDENDUM_TO = "ADDENDUM_TO"


def _advisory_lock_key(namespace: str, *parts: object) -> int:
    canonical = json.dumps(
        [namespace, *(str(part) for part in parts)],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    return int.from_bytes(hashlib.sha256(canonical).digest()[:8], "big", signed=True)


def _is_postgresql(db: AsyncSession) -> bool:
    if not isinstance(db, AsyncSession):
        return False
    bind = db.get_bind()
    return getattr(getattr(bind, "dialect", None), "name", None) == "postgresql"


async def _acquire_graph_lock(db: AsyncSession, namespace: str, *parts: object) -> None:
    if not _is_postgresql(db):
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _advisory_lock_key(namespace, *parts)},
    )


def _audit_context(tenant_id: uuid.UUID) -> AuditContext:
    return AuditContext.for_tenant(
        tenant_id=str(tenant_id),
        domain=AuditDomain.PIPELINE,
    )


def clinical_fact_key(field_name: str, clinical_fact_id: str | None) -> str | None:
    """Return a value-free key only for a Nexa-owned exact fact identity.

    Field name alone is intentionally insufficient. The opaque fact identifier
    must have crossed a trusted in-process parser boundary and is never accepted
    from provider JSON or inferred from source text, page order, confidence, or
    a clinical value.
    """
    canonical_field = field_name.strip().casefold()
    explicit_id = (clinical_fact_id or "").strip()
    if not canonical_field or not explicit_id:
        return None
    payload = json.dumps(
        [CLINICAL_FACT_KEY_VERSION, canonical_field, explicit_id],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


async def create_source_relationship(
    db: AsyncSession,
    *,
    source_document_id: uuid.UUID,
    related_document_id: uuid.UUID,
    tenant_id: uuid.UUID,
    patient_id: uuid.UUID,
    relation_type: SourceRelationType | str,
    workflow_id: str,
    created_by: str,
    authorization_provider_id: str,
    authorization_hospital_id: uuid.UUID,
    consent_request_id: str,
    created_at: datetime,
) -> DocumentSourceRelationshipRecord:
    """Stage one authorized append-only relationship, rejecting graph drift."""
    try:
        closed_type = SourceRelationType(relation_type)
    except ValueError as exc:
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_TYPE_INVALID") from exc
    if source_document_id == related_document_id:
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_SELF_REFERENCE")
    if not all(
        (
            workflow_id,
            created_by,
            authorization_provider_id,
            consent_request_id,
        )
    ):
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_WORKFLOW_REQUIRED")
    if (
        created_by != authorization_provider_id
        or workflow_id != consent_request_id
        or authorization_hospital_id != tenant_id
    ):
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_AUTHORIZATION_MISMATCH")

    await _acquire_graph_lock(
        db,
        SOURCE_RELATION_LOCK_VERSION,
        tenant_id,
        patient_id,
    )

    documents = (
        (
            await db.execute(
                select(DocumentStorage)
                .where(
                    DocumentStorage.id.in_([source_document_id, related_document_id])
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    by_id = {row.id: row for row in documents}
    if set(by_id) != {source_document_id, related_document_id}:
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_DOCUMENT_NOT_FOUND")
    if any(row.tenant_id != tenant_id for row in documents):
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_TENANT_MISMATCH")
    if any(row.patient_id != patient_id for row in documents):
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_PATIENT_MISMATCH")
    if any(row.uploader_id != authorization_provider_id for row in documents):
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_PROVIDER_MISMATCH")

    current_job = (
        await db.execute(
            select(ExtractionJob).where(
                ExtractionJob.document_id == source_document_id,
                ExtractionJob.tenant_id == tenant_id,
                ExtractionJob.patient_id == patient_id,
                ExtractionJob.authorization_provider_id == authorization_provider_id,
                ExtractionJob.consent_request_id == consent_request_id,
            )
        )
    ).scalar_one_or_none()
    related_jobs = (
        (
            await db.execute(
                select(ExtractionJob).where(
                    ExtractionJob.document_id == related_document_id,
                    ExtractionJob.tenant_id == tenant_id,
                    ExtractionJob.patient_id == patient_id,
                    ExtractionJob.authorization_provider_id
                    == authorization_provider_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if current_job is None or not related_jobs:
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_AUTHORIZATION_MISMATCH")

    try:
        capability = await validate_live_document_processing_request(
            request_id=consent_request_id,
            patient_id=str(patient_id),
            provider_id=authorization_provider_id,
            hospital_id=str(authorization_hospital_id),
        )
    except ApprovedAccessStoreUnavailable as exc:
        raise ClinicalEvidenceIntegrityError(
            "SOURCE_RELATION_AUTHORIZATION_UNAVAILABLE"
        ) from exc
    if (
        capability is None
        or DocumentProcessingOperation.UPLOAD_DOCUMENT.value
        not in capability.allowed_operations
    ):
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_CONSENT_INACTIVE")

    try:
        await check_erasure_registry(str(patient_id), db)
    except _PatientErasedSignal as exc:
        raise ClinicalEvidenceIntegrityError(
            "SOURCE_RELATION_ERASURE_ACCESS_BLOCKED"
        ) from exc
    except ErasureRegistryUnavailable as exc:
        raise ClinicalEvidenceIntegrityError(
            "SOURCE_RELATION_ERASURE_REGISTRY_UNAVAILABLE"
        ) from exc

    existing = (
        await db.execute(
            select(DocumentSourceRelationshipRecord)
            .where(
                DocumentSourceRelationshipRecord.source_document_id
                == source_document_id
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.related_document_id == related_document_id
            and existing.relation_type == closed_type.value
            and existing.tenant_id == tenant_id
            and existing.patient_id == patient_id
            and existing.workflow_id == workflow_id
            and existing.created_by == created_by
        ):
            return existing
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_CONFLICT")

    cursor = related_document_id
    visited: set[uuid.UUID] = set()
    for _ in range(MAX_SOURCE_RELATION_DEPTH):
        if cursor == source_document_id:
            raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_CYCLE")
        if cursor in visited:
            raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_GRAPH_INVALID")
        visited.add(cursor)
        parent = (
            await db.execute(
                select(DocumentSourceRelationshipRecord).where(
                    DocumentSourceRelationshipRecord.source_document_id == cursor
                )
            )
        ).scalar_one_or_none()
        if parent is None:
            break
        if parent.tenant_id != tenant_id or parent.patient_id != patient_id:
            raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_GRAPH_INVALID")
        cursor = parent.related_document_id
    else:
        raise ClinicalEvidenceIntegrityError("SOURCE_RELATION_GRAPH_TOO_DEEP")

    row = DocumentSourceRelationshipRecord(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        patient_id=patient_id,
        source_document_id=source_document_id,
        related_document_id=related_document_id,
        relation_type=closed_type.value,
        workflow_id=workflow_id,
        created_by=created_by,
        created_at=created_at,
    )
    db.add(row)
    await db.flush()
    if _is_postgresql(db):
        await enqueue_audit_event(
            db,
            audit_context=_audit_context(tenant_id),
            idempotency_key=f"source-relationship-created:{row.id}",
            actor_id=authorization_provider_id,
            event_type="DOCUMENT_SOURCE_RELATIONSHIP_CREATED",
            target_id=str(row.id),
            patient_id=str(patient_id),
            metadata={"relation_type": closed_type.value},
        )
    return row


async def persist_conflict_set(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    patient_id: uuid.UUID,
    job_id: uuid.UUID,
    source_document_id: uuid.UUID,
    field_name: str,
    fact_key: str,
    candidates: Iterable[ExtractionCandidateRecord],
    created_at: datetime,
    related_document_id: uuid.UUID | None = None,
) -> ExtractionConflictRecord:
    """Persist an idempotent conflict and immutable, graph-checked membership."""
    members = list(candidates)
    if len(members) < 2:
        raise ClinicalEvidenceIntegrityError("CLINICAL_CONFLICT_MEMBERS_REQUIRED")
    if len({row.evidence_id for row in members}) != len(members):
        raise ClinicalEvidenceIntegrityError("CLINICAL_CONFLICT_DUPLICATE_MEMBER")
    for row in members:
        if (
            row.tenant_id != tenant_id
            or row.patient_id != patient_id
            or row.field_name != field_name
            or row.clinical_fact_key != fact_key
            or (
                (row.job_id != job_id or row.source_document_id != source_document_id)
                and row.source_document_id != related_document_id
            )
        ):
            raise ClinicalEvidenceIntegrityError("CLINICAL_CONFLICT_BINDING_MISMATCH")

    actor_ids = {row.authorization_provider_id for row in members}
    if len(actor_ids) != 1:
        raise ClinicalEvidenceIntegrityError("CLINICAL_CONFLICT_BINDING_MISMATCH")
    actor_id = actor_ids.pop()

    await _acquire_graph_lock(
        db,
        CLINICAL_CONFLICT_LOCK_VERSION,
        tenant_id,
        patient_id,
        job_id,
        fact_key,
    )

    conflict = (
        await db.execute(
            select(ExtractionConflictRecord)
            .where(
                ExtractionConflictRecord.job_id == job_id,
                ExtractionConflictRecord.clinical_fact_key == fact_key,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    conflict_created = conflict is None
    if conflict_created:
        conflict = ExtractionConflictRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            patient_id=patient_id,
            job_id=job_id,
            source_document_id=source_document_id,
            field_name=field_name,
            clinical_fact_key=fact_key,
            created_at=created_at,
        )
        db.add(conflict)
        await db.flush()
    elif (
        conflict.tenant_id != tenant_id
        or conflict.patient_id != patient_id
        or conflict.source_document_id != source_document_id
        or conflict.field_name != field_name
    ):
        raise ClinicalEvidenceIntegrityError("CLINICAL_CONFLICT_BINDING_MISMATCH")

    existing_ids = set(
        (
            await db.execute(
                select(ExtractionConflictMemberRecord.evidence_id).where(
                    ExtractionConflictMemberRecord.conflict_id == conflict.id
                )
            )
        ).scalars()
    )
    added_count = 0
    for candidate in members:
        if candidate.evidence_id in existing_ids:
            continue
        db.add(
            ExtractionConflictMemberRecord(
                id=uuid.uuid4(),
                conflict_id=conflict.id,
                candidate_id=candidate.id,
                evidence_id=candidate.evidence_id,
                created_at=created_at,
            )
        )
        added_count += 1
    await db.flush()
    if _is_postgresql(db):
        if conflict_created:
            await enqueue_audit_event(
                db,
                audit_context=_audit_context(tenant_id),
                idempotency_key=f"clinical-conflict-created:{conflict.id}",
                actor_id=actor_id,
                event_type="CLINICAL_CONFLICT_CREATED",
                target_id=str(conflict.id),
                patient_id=str(patient_id),
                metadata={"member_count": len(members)},
            )
        if added_count:
            await enqueue_audit_event(
                db,
                audit_context=_audit_context(tenant_id),
                idempotency_key=(
                    f"clinical-conflict-members:{conflict.id}:"
                    f"{len(existing_ids) + added_count}"
                ),
                actor_id=actor_id,
                event_type="CLINICAL_CONFLICT_MEMBERS_ADDED",
                target_id=str(conflict.id),
                patient_id=str(patient_id),
                metadata={"added_count": added_count},
            )
    return conflict

"""Structured Patient Records and Timeline API Routes (Workstream 3).

Implements consent-gated read endpoints (summary, timeline, full record),
patient self-view endpoints, and provider-authed write endpoints with
audit-before-write guarantee.
"""

from __future__ import annotations

from app.security.audit_context import AuditContext, AuditDomain, current_audit_context

import logging
import base64
import binascii
import json
import uuid
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.consent_gate import require_consent, require_self_patient_access
from app.core.database import get_db_session
from app.core.dependencies import (
    get_current_provider,
    require_clinical_capability,
    require_role,
)
from app.security.provider_capabilities import ClinicalCapability
from app.models.patient_records import (
    Allergy,
    DocumentReference,
    LabResult,
    Medication,
    TimelineEvent,
    Vitals,
)
from app.models.provider import HospitalRegistry, ProviderIdentity
from app.models.provider_context import ProviderContext
from app.models.shards import NexaVault
from app.services.crypto_kms import (
    EncryptedField,
    EncryptionError,
    get_encryption_provider,
)
from app.observability.audit_ledger import (
    append_audit_log_or_503,
    read_audit_events,
    read_patient_access_history_events,
)
from app.services.audit_outbox import enqueue_audit_event

logger = logging.getLogger("nexa_logger")

router = APIRouter(tags=["records"])

_ACCESS_HISTORY_SUCCESS_STATUSES = {
    "PATIENT_RECORD_READ_SUCCESS": {"SUCCESS"},
    "BREAK_GLASS_EMERGENCY_SUMMARY_ACCESSED": {"SUCCESS"},
    "SNAPSHOT_ACCESSED": {"SUCCESS"},
    "PATIENT_RECORD_VIEW_COMPLETED": {"COMPLETED", "SUCCESS"},
}
_FORMER_PROVIDER_LABEL = "Former or unavailable provider"
_UNKNOWN_FACILITY_LABEL = "Unknown facility"
_UNKNOWN_PURPOSE_LABEL = "Purpose not recorded"


async def _stage_patient_record_success_audit(
    db: AsyncSession,
    *,
    actor_uid: str,
    patient_id: str,
    record_type: str,
    record_id: str,
) -> None:
    """Stage the success audit intent in the clinical-write transaction.

    The outbox insert deliberately does not commit.  If it cannot be staged,
    the clinical row and timeline row must not be allowed to commit either.
    """
    try:
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
            idempotency_key=f"patient-record-append:{record_type}:{record_id}",
            actor_id=actor_uid,
            event_type="PATIENT_RECORD_APPEND_SUCCESS",
            target_id=patient_id,
            patient_id=patient_id,
            metadata={"type": record_type, "record_id": record_id},
        )
    except Exception as exc:  # noqa: BLE001 - fail closed at the API boundary
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "AUDIT_DURABILITY_UNAVAILABLE"},
        ) from exc


async def _commit_patient_record_transaction(db: AsyncSession) -> None:
    """Commit one clinical/timeline/outbox transaction or fail closed."""
    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - do not expose database details
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "PATIENT_RECORD_WRITE_UNAVAILABLE"},
        ) from exc


def _encode_access_history_cursor(row: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "created_at": str(row["created_at"]),
            "audit_id": str(row["audit_id"]),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_access_history_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    if cursor is None:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        created_at = datetime.fromisoformat(
            str(payload["created_at"]).replace("Z", "+00:00")
        )
        audit_id = uuid.UUID(str(payload["audit_id"]))
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "INVALID_ACCESS_HISTORY_CURSOR"},
        ) from None
    return created_at.isoformat(), str(audit_id)


def _encode_keyset_cursor(occurred_at: datetime | str, item_id: uuid.UUID | str) -> str:
    dt_str = (
        occurred_at.isoformat()
        if isinstance(occurred_at, datetime)
        else str(occurred_at)
    )
    payload = json.dumps(
        {"occurred_at": dt_str, "id": str(item_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_keyset_cursor(cursor: str | None) -> tuple[datetime | None, str | None]:
    if cursor is None or not str(cursor).strip():
        return None, None
    try:
        padded = str(cursor) + "=" * (-len(str(cursor)) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        raw_dt = str(payload["occurred_at"]).replace("Z", "+00:00")
        occurred_at = datetime.fromisoformat(raw_dt)
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        item_id = str(payload["id"]).strip()
        if not item_id:
            raise ValueError("Empty ID in cursor")
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        binascii.Error,
        json.JSONDecodeError,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "INVALID_CURSOR",
                "message": "Pagination cursor is malformed or invalid",
            },
        ) from None
    return occurred_at, item_id


def _apply_no_store_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"


# ── Pydantic Request Models ──────────────────────────────────────────────────


class AppendVitalsRequest(BaseModel):
    encounter_id: str | None = None
    systolic_bp: int | str
    diastolic_bp: int | str
    heart_rate: int | str
    temperature_celsius: float | str
    sp_o2_percentage: int | str
    recorded_at: datetime
    source: str = "manual"
    confidence: float | None = None
    risk_level: str = "LOW_RISK"
    source_document_id: uuid.UUID | None = None


class AppendMedicationRequest(BaseModel):
    name: str
    strength: str
    frequency: str
    prescribed_at: datetime
    source: str = "manual"
    confidence: float | None = None
    risk_level: str = "MEDIUM_RISK"
    source_document_id: uuid.UUID | None = None


class AppendLabResultRequest(BaseModel):
    test_name: str
    value: str
    unit: str
    reference_range: str
    is_abnormal: bool = False
    recorded_at: datetime
    source: str = "manual"
    confidence: float | None = None
    risk_level: str = "MEDIUM_RISK"
    source_document_id: uuid.UUID | None = None


class AppendAllergyRequest(BaseModel):
    allergen: str
    severity: str
    source: str = "manual"
    confidence: float | None = None
    risk_level: str = "HIGH_RISK"
    source_document_id: uuid.UUID | None = None


class AppendDocumentRequest(BaseModel):
    document_type: str
    storage_ref: str
    extraction_job_id: uuid.UUID | None = None
    source: str = "manual"
    confidence: float | None = None
    risk_level: str = "LOW_RISK"
    source_document_id: uuid.UUID | None = None


def _validate_provenance(
    source: str, confidence: float | None, risk_level: str, source_doc: uuid.UUID | None
) -> None:
    if source == "ai_extracted":
        if (
            confidence is None
            or not (0.0 <= confidence <= 1.0)
            or not risk_level
            or not source_doc
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="AI-extracted field must have numeric confidence, risk_level, and source_document_id",
            )


def _parse_uuid(id_str: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(id_str))
    except (AttributeError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "INVALID_PATIENT_ID",
                "message": "patient id must be a valid UUID",
            },
        ) from exc


async def _read_vault_identity(
    patient_id: uuid.UUID, db: AsyncSession
) -> dict[str, str | None]:
    """Read and decrypt canonical vault identity for exactly one patient."""

    result = await db.execute(
        select(NexaVault)
        .where(NexaVault.masked_internal_id == str(patient_id))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    identity = {"patient_name": None, "phone": None, "aadhaar_abha_id": None}
    if row is None:
        return identity

    kms = get_encryption_provider()
    for field_name in identity:
        value = getattr(row, field_name, None)
        if value is None:
            continue
        try:
            encrypted = EncryptedField.deserialize(value, field_name)
            identity[field_name] = await kms.decrypt_field(
                str(patient_id), field_name, encrypted, db
            )
        except EncryptionError:
            # Vault identity is required to be encrypted. Never expose legacy
            # plaintext or substitute a fabricated identity.
            logger.error(
                "Vault identity could not be decrypted",
                extra={
                    "event": "vault_identity_decrypt_failed",
                    "patient_id": str(patient_id),
                    "field": field_name,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "IDENTITY_UNAVAILABLE",
                    "message": "patient identity is temporarily unavailable",
                },
            )
    return identity


# ── Patient Self-View Endpoints ──────────────────────────────────────────────


@router.get("/api/v2/patient/me/summary", status_code=status.HTTP_200_OK)
async def get_my_summary(
    response: Response,
    patient_id: str = Depends(require_self_patient_access()),
    db: AsyncSession = Depends(get_db_session),
):
    """Patient views personal health summary (allergies, medications, vitals, labs, reports)."""
    _apply_no_store_headers(response)
    pid_uuid = _parse_uuid(patient_id)

    # 1. Allergies
    stmt_a = select(Allergy).where(Allergy.patient_id == pid_uuid).limit(20)
    res_a = await db.execute(stmt_a)
    alg_rows = res_a.scalars().all()
    allergy_highlights = [
        {
            "id": str(a.id),
            "allergen": a.allergen,
            "severity": a.severity,
            "risk_level": a.risk_level,
            "source": a.source,
            "source_display": "Clinician recorded" if a.source == "manual" else "Document extracted",
        }
        for a in alg_rows
    ]

    # 2. Active medications
    stmt_m = (
        select(Medication)
        .where(Medication.patient_id == pid_uuid)
        .order_by(Medication.prescribed_at.desc())
        .limit(10)
    )
    res_m = await db.execute(stmt_m)
    med_rows = res_m.scalars().all()
    active_medications = [
        {
            "id": str(m.id),
            "name": m.name,
            "strength": m.strength,
            "frequency": m.frequency,
            "prescribed_at": m.prescribed_at.isoformat() if m.prescribed_at else None,
            "source": m.source,
            "risk_level": m.risk_level,
        }
        for m in med_rows
    ]

    # 3. Latest vitals (latest observation per vital type)
    stmt_v = (
        select(Vitals)
        .where(Vitals.patient_id == pid_uuid)
        .order_by(Vitals.recorded_at.desc())
        .limit(20)
    )
    res_v = await db.execute(stmt_v)
    v_rows = res_v.scalars().all()
    seen_vital_types = set()
    latest_vitals = []
    for v in v_rows:
        if v.type not in seen_vital_types:
            seen_vital_types.add(v.type)
            latest_vitals.append({
                "id": str(v.id),
                "type": v.type,
                "value": v.value,
                "unit": v.unit,
                "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None,
                "source": v.source,
                "risk_level": v.risk_level,
            })

    # 4. Recent labs
    stmt_l = (
        select(LabResult)
        .where(LabResult.patient_id == pid_uuid)
        .order_by(LabResult.recorded_at.desc())
        .limit(10)
    )
    res_l = await db.execute(stmt_l)
    lab_rows = res_l.scalars().all()
    recent_labs = [
        {
            "id": str(lab.id),
            "test_name": lab.test_name,
            "value": lab.value,
            "unit": lab.unit,
            "reference_range": lab.reference_range,
            "is_abnormal": lab.is_abnormal,
            "recorded_at": lab.recorded_at.isoformat() if lab.recorded_at else None,
            "source": lab.source,
            "risk_level": lab.risk_level,
        }
        for lab in lab_rows
    ]

    # 5. Recent reports / documents
    stmt_d = (
        select(DocumentReference)
        .where(DocumentReference.patient_id == pid_uuid)
        .order_by(DocumentReference.uploaded_at.desc())
        .limit(10)
    )
    res_d = await db.execute(stmt_d)
    doc_rows = res_d.scalars().all()
    recent_reports = [
        {
            "id": str(d.id),
            "document_type": d.document_type,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            "has_source_document": True,
        }
        for d in doc_rows
    ]

    # 6. Recent timeline events (latest 5)
    recent_events, _ = await _fetch_patient_longitudinal_timeline(patient_id, db, limit=5)

    return {
        "patient_id": patient_id,
        "allergy_highlights": allergy_highlights,
        "active_medications": active_medications,
        "latest_vitals": latest_vitals,
        "recent_labs": recent_labs,
        "recent_reports": recent_reports,
        "recent_timeline_events": recent_events,
        "counts": {
            "allergies": len(alg_rows),
            "medications": len(med_rows),
            "vitals": len(latest_vitals),
            "labs": len(lab_rows),
            "reports": len(doc_rows),
        },
    }


@router.get("/api/v2/patient/me/timeline", status_code=status.HTTP_200_OK)
async def get_my_timeline(
    response: Response,
    limit: int = 20,
    cursor: str | None = None,
    category: str | None = None,
    patient_id: str = Depends(require_self_patient_access()),
    db: AsyncSession = Depends(get_db_session),
):
    """Patient views their own longitudinal timeline with keyset pagination and category filters."""
    _apply_no_store_headers(response)
    events, next_cursor = await _fetch_patient_longitudinal_timeline(
        patient_id, db, limit=limit, cursor=cursor, category=category
    )
    return {
        "patient_id": patient_id,
        "events": events,
        "next_cursor": next_cursor,
    }


@router.get("/api/v2/patient/me/records", status_code=status.HTTP_200_OK)
async def get_my_records_overview(
    response: Response,
    patient_id: str = Depends(require_self_patient_access()),
    db: AsyncSession = Depends(get_db_session),
):
    """Overview of patient medical records grouped by category with counts and last updated times."""
    _apply_no_store_headers(response)
    pid_uuid = _parse_uuid(patient_id)

    res_a = await db.execute(select(Allergy.id).where(Allergy.patient_id == pid_uuid))
    allergy_count = len(res_a.scalars().all())

    res_m = await db.execute(
        select(Medication.prescribed_at)
        .where(Medication.patient_id == pid_uuid)
        .order_by(Medication.prescribed_at.desc())
    )
    med_dates = res_m.scalars().all()
    med_count = len(med_dates)
    med_latest = med_dates[0].isoformat() if med_dates and med_dates[0] else None

    res_v = await db.execute(
        select(Vitals.recorded_at)
        .where(Vitals.patient_id == pid_uuid)
        .order_by(Vitals.recorded_at.desc())
    )
    v_dates = res_v.scalars().all()
    v_count = len(v_dates)
    v_latest = v_dates[0].isoformat() if v_dates and v_dates[0] else None

    res_l = await db.execute(
        select(LabResult.recorded_at)
        .where(LabResult.patient_id == pid_uuid)
        .order_by(LabResult.recorded_at.desc())
    )
    l_dates = res_l.scalars().all()
    l_count = len(l_dates)
    l_latest = l_dates[0].isoformat() if l_dates and l_dates[0] else None

    res_d = await db.execute(
        select(DocumentReference.uploaded_at)
        .where(DocumentReference.patient_id == pid_uuid)
        .order_by(DocumentReference.uploaded_at.desc())
    )
    d_dates = res_d.scalars().all()
    d_count = len(d_dates)
    d_latest = d_dates[0].isoformat() if d_dates and d_dates[0] else None

    categories = [
        {
            "category": "allergies",
            "label": "Allergies",
            "description": "Recorded allergies, sensitivities, and clinical risk levels",
            "count": allergy_count,
            "last_updated": None,
            "icon": "alert-triangle",
        },
        {
            "category": "medications",
            "label": "Medications",
            "description": "Active and past pharmaceutical prescriptions and doses",
            "count": med_count,
            "last_updated": med_latest,
            "icon": "pill",
        },
        {
            "category": "vitals",
            "label": "Vitals",
            "description": "Blood pressure, heart rate, temperature, SpO2, and blood glucose",
            "count": v_count,
            "last_updated": v_latest,
            "icon": "heart",
        },
        {
            "category": "labs",
            "label": "Laboratory Results",
            "description": "Blood panels, metabolic tests, and diagnostic values with reference ranges",
            "count": l_count,
            "last_updated": l_latest,
            "icon": "test-tube",
        },
        {
            "category": "documents",
            "label": "Reports & Documents",
            "description": "Lab reports, imaging scans, discharge summaries, and external records",
            "count": d_count,
            "last_updated": d_latest,
            "icon": "file-text",
        },
    ]

    return {
        "patient_id": patient_id,
        "categories": categories,
    }


@router.get("/api/v2/patient/me/records/{category}", status_code=status.HTTP_200_OK)
async def get_my_records_by_category(
    category: str,
    response: Response,
    limit: int = 20,
    cursor: str | None = None,
    patient_id: str = Depends(require_self_patient_access()),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve paginated records for a specific category."""
    _apply_no_store_headers(response)
    pid_uuid = _parse_uuid(patient_id)
    bounded_limit = max(1, min(int(limit), 50))
    cursor_dt, cursor_id = _decode_keyset_cursor(cursor)

    cat_norm = category.strip().lower()
    if cat_norm not in {"allergies", "medications", "vitals", "labs", "documents"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "INVALID_RECORD_CATEGORY",
                "message": f"Unknown category '{category}'. Must be one of: allergies, medications, vitals, labs, documents.",
            },
        )

    records: list[dict[str, Any]] = []
    next_cursor: str | None = None

    if cat_norm == "allergies":
        stmt = select(Allergy).where(Allergy.patient_id == pid_uuid)
        res = await db.execute(stmt)
        all_algs = res.scalars().all()
        all_algs.sort(key=lambda a: str(a.id), reverse=True)
        if cursor_id:
            all_algs = [a for a in all_algs if str(a.id) < cursor_id]
        page = all_algs[:bounded_limit]
        if len(all_algs) > bounded_limit and page:
            next_cursor = _encode_keyset_cursor(datetime.now(timezone.utc), page[-1].id)
        for a in page:
            records.append({
                "record_id": str(a.id),
                "category": "allergies",
                "allergen": a.allergen,
                "severity": a.severity,
                "risk_level": a.risk_level,
                "source": a.source,
                "confidence": a.confidence,
                "source_document_id": str(a.source_document_id) if a.source_document_id else None,
                "has_source_document": a.source_document_id is not None,
            })

    elif cat_norm == "medications":
        stmt = select(Medication).where(Medication.patient_id == pid_uuid)
        if cursor_dt:
            stmt = stmt.where(Medication.prescribed_at <= cursor_dt)
        stmt = stmt.order_by(Medication.prescribed_at.desc(), Medication.id.desc()).limit(bounded_limit + 1)
        res = await db.execute(stmt)
        med_rows = res.scalars().all()
        if cursor_dt and cursor_id:
            med_rows = [
                m for m in med_rows
                if (m.prescribed_at < cursor_dt or (m.prescribed_at == cursor_dt and str(m.id) < cursor_id))
            ]
        page = med_rows[:bounded_limit]
        if len(med_rows) > bounded_limit and page:
            next_cursor = _encode_keyset_cursor(page[-1].prescribed_at, page[-1].id)
        for m in page:
            records.append({
                "record_id": str(m.id),
                "category": "medications",
                "name": m.name,
                "strength": m.strength,
                "frequency": m.frequency,
                "prescribed_at": m.prescribed_at.isoformat() if m.prescribed_at else None,
                "source": m.source,
                "risk_level": m.risk_level,
                "confidence": m.confidence,
                "source_document_id": str(m.source_document_id) if m.source_document_id else None,
                "has_source_document": m.source_document_id is not None,
            })

    elif cat_norm == "vitals":
        stmt = select(Vitals).where(Vitals.patient_id == pid_uuid)
        if cursor_dt:
            stmt = stmt.where(Vitals.recorded_at <= cursor_dt)
        stmt = stmt.order_by(Vitals.recorded_at.desc(), Vitals.id.desc()).limit(bounded_limit + 1)
        res = await db.execute(stmt)
        v_rows = res.scalars().all()
        if cursor_dt and cursor_id:
            v_rows = [
                v for v in v_rows
                if (v.recorded_at < cursor_dt or (v.recorded_at == cursor_dt and str(v.id) < cursor_id))
            ]
        page = v_rows[:bounded_limit]
        if len(v_rows) > bounded_limit and page:
            next_cursor = _encode_keyset_cursor(page[-1].recorded_at, page[-1].id)
        for v in page:
            records.append({
                "record_id": str(v.id),
                "category": "vitals",
                "type": v.type,
                "value": v.value,
                "unit": v.unit,
                "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None,
                "source": v.source,
                "risk_level": v.risk_level,
                "confidence": v.confidence,
                "source_document_id": str(v.source_document_id) if v.source_document_id else None,
                "has_source_document": v.source_document_id is not None,
            })

    elif cat_norm == "labs":
        stmt = select(LabResult).where(LabResult.patient_id == pid_uuid)
        if cursor_dt:
            stmt = stmt.where(LabResult.recorded_at <= cursor_dt)
        stmt = stmt.order_by(LabResult.recorded_at.desc(), LabResult.id.desc()).limit(bounded_limit + 1)
        res = await db.execute(stmt)
        lab_rows = res.scalars().all()
        if cursor_dt and cursor_id:
            lab_rows = [
                lab_item for lab_item in lab_rows
                if (lab_item.recorded_at < cursor_dt or (lab_item.recorded_at == cursor_dt and str(lab_item.id) < cursor_id))
            ]
        page = lab_rows[:bounded_limit]
        if len(lab_rows) > bounded_limit and page:
            next_cursor = _encode_keyset_cursor(page[-1].recorded_at, page[-1].id)
        for lab_item in page:
            records.append({
                "record_id": str(lab_item.id),
                "category": "labs",
                "test_name": lab_item.test_name,
                "value": lab_item.value,
                "unit": lab_item.unit,
                "reference_range": lab_item.reference_range,
                "is_abnormal": lab_item.is_abnormal,
                "recorded_at": lab_item.recorded_at.isoformat() if lab_item.recorded_at else None,
                "source": lab_item.source,
                "risk_level": lab_item.risk_level,
                "confidence": lab_item.confidence,
                "source_document_id": str(lab_item.source_document_id) if lab_item.source_document_id else None,
                "has_source_document": lab_item.source_document_id is not None,
            })

    elif cat_norm == "documents":
        stmt = select(DocumentReference).where(DocumentReference.patient_id == pid_uuid)
        if cursor_dt:
            stmt = stmt.where(DocumentReference.uploaded_at <= cursor_dt)
        stmt = stmt.order_by(DocumentReference.uploaded_at.desc(), DocumentReference.id.desc()).limit(bounded_limit + 1)
        res = await db.execute(stmt)
        doc_rows = res.scalars().all()
        if cursor_dt and cursor_id:
            doc_rows = [
                d for d in doc_rows
                if (d.uploaded_at < cursor_dt or (d.uploaded_at == cursor_dt and str(d.id) < cursor_id))
            ]
        page = doc_rows[:bounded_limit]
        if len(doc_rows) > bounded_limit and page:
            next_cursor = _encode_keyset_cursor(page[-1].uploaded_at, page[-1].id)
        for d in page:
            records.append({
                "record_id": str(d.id),
                "category": "documents",
                "document_type": d.document_type,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                "has_source_document": True,
            })

    return {
        "patient_id": patient_id,
        "category": cat_norm,
        "records": records,
        "next_cursor": next_cursor,
    }


@router.get("/api/v2/patient/me/records/{category}/{record_id}", status_code=status.HTTP_200_OK)
async def get_my_record_detail(
    category: str,
    record_id: str,
    response: Response,
    patient_id: str = Depends(require_self_patient_access()),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve detailed structured fields and provenance for a single record."""
    _apply_no_store_headers(response)
    pid_uuid = _parse_uuid(patient_id)
    rec_uuid = _parse_uuid(record_id)
    cat_norm = category.strip().lower()

    if cat_norm == "allergies":
        stmt = select(Allergy).where(Allergy.id == rec_uuid, Allergy.patient_id == pid_uuid).limit(1)
        res = await db.execute(stmt)
        a = res.scalar_one_or_none()
        if not a:
            raise HTTPException(status_code=404, detail={"error_code": "RECORD_NOT_FOUND", "message": "Allergy record not found"})
        return {
            "record_id": str(a.id),
            "patient_id": patient_id,
            "category": "allergies",
            "title": f"Allergy: {a.allergen}",
            "fields": {
                "allergen": a.allergen,
                "severity": a.severity,
                "risk_level": a.risk_level,
            },
            "recorded_at": None,
            "provenance": {
                "source": a.source,
                "source_display": "Clinician Recorded" if a.source == "manual" else "Document Extracted",
                "confidence": a.confidence,
                "risk_level": a.risk_level,
                "source_document_id": str(a.source_document_id) if a.source_document_id else None,
                "has_source_document": a.source_document_id is not None,
            },
        }

    elif cat_norm == "medications":
        stmt = select(Medication).where(Medication.id == rec_uuid, Medication.patient_id == pid_uuid).limit(1)
        res = await db.execute(stmt)
        m = res.scalar_one_or_none()
        if not m:
            raise HTTPException(status_code=404, detail={"error_code": "RECORD_NOT_FOUND", "message": "Medication record not found"})
        return {
            "record_id": str(m.id),
            "patient_id": patient_id,
            "category": "medications",
            "title": f"Medication: {m.name} {m.strength}",
            "fields": {
                "name": m.name,
                "strength": m.strength,
                "frequency": m.frequency,
                "prescribed_at": m.prescribed_at.isoformat() if m.prescribed_at else None,
            },
            "recorded_at": m.prescribed_at.isoformat() if m.prescribed_at else None,
            "provenance": {
                "source": m.source,
                "source_display": "Clinician Prescribed" if m.source == "manual" else "Document Extracted",
                "confidence": m.confidence,
                "risk_level": m.risk_level,
                "source_document_id": str(m.source_document_id) if m.source_document_id else None,
                "has_source_document": m.source_document_id is not None,
            },
        }

    elif cat_norm == "vitals":
        stmt = select(Vitals).where(Vitals.id == rec_uuid, Vitals.patient_id == pid_uuid).limit(1)
        res = await db.execute(stmt)
        v = res.scalar_one_or_none()
        if not v:
            raise HTTPException(status_code=404, detail={"error_code": "RECORD_NOT_FOUND", "message": "Vitals record not found"})
        return {
            "record_id": str(v.id),
            "patient_id": patient_id,
            "category": "vitals",
            "title": f"Vitals Observation: {v.type}",
            "fields": {
                "type": v.type,
                "value": v.value,
                "unit": v.unit,
                "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None,
            },
            "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None,
            "provenance": {
                "source": v.source,
                "source_display": "Clinician Recorded" if v.source == "manual" else "Document Extracted",
                "confidence": v.confidence,
                "risk_level": v.risk_level,
                "source_document_id": str(v.source_document_id) if v.source_document_id else None,
                "has_source_document": v.source_document_id is not None,
            },
        }

    elif cat_norm == "labs":
        stmt = select(LabResult).where(LabResult.id == rec_uuid, LabResult.patient_id == pid_uuid).limit(1)
        res = await db.execute(stmt)
        lab = res.scalar_one_or_none()
        if not lab:
            raise HTTPException(status_code=404, detail={"error_code": "RECORD_NOT_FOUND", "message": "Lab result not found"})
        return {
            "record_id": str(lab.id),
            "patient_id": patient_id,
            "category": "labs",
            "title": f"Lab Result: {lab.test_name}",
            "fields": {
                "test_name": lab.test_name,
                "value": lab.value,
                "unit": lab.unit,
                "reference_range": lab.reference_range,
                "is_abnormal": lab.is_abnormal,
                "recorded_at": lab.recorded_at.isoformat() if lab.recorded_at else None,
            },
            "recorded_at": lab.recorded_at.isoformat() if lab.recorded_at else None,
            "provenance": {
                "source": lab.source,
                "source_display": "Laboratory Integration" if lab.source == "manual" else "Document Extracted",
                "confidence": lab.confidence,
                "risk_level": lab.risk_level,
                "source_document_id": str(lab.source_document_id) if lab.source_document_id else None,
                "has_source_document": lab.source_document_id is not None,
            },
        }

    elif cat_norm == "documents":
        stmt = select(DocumentReference).where(DocumentReference.id == rec_uuid, DocumentReference.patient_id == pid_uuid).limit(1)
        res = await db.execute(stmt)
        d = res.scalar_one_or_none()
        if not d:
            raise HTTPException(status_code=404, detail={"error_code": "RECORD_NOT_FOUND", "message": "Document record not found"})
        return {
            "record_id": str(d.id),
            "patient_id": patient_id,
            "category": "documents",
            "title": f"Document: {d.document_type}",
            "fields": {
                "document_type": d.document_type,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            },
            "recorded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            "provenance": {
                "source": "patient_uploaded",
                "source_display": "Patient Uploaded / External Document",
                "source_document_id": str(d.id),
                "has_source_document": True,
            },
        }

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error_code": "INVALID_RECORD_CATEGORY", "message": f"Unknown category '{category}'"},
    )


@router.get("/api/v2/patient/me/prescriptions", status_code=status.HTTP_200_OK)
async def get_my_prescriptions(
    response: Response,
    limit: int = 20,
    cursor: str | None = None,
    patient_id: str = Depends(require_self_patient_access()),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve patient prescriptions and medication treatments for review."""
    _apply_no_store_headers(response)
    pid_uuid = _parse_uuid(patient_id)
    bounded_limit = max(1, min(int(limit), 50))
    cursor_dt, cursor_id = _decode_keyset_cursor(cursor)

    stmt = select(Medication).where(Medication.patient_id == pid_uuid)
    if cursor_dt:
        stmt = stmt.where(Medication.prescribed_at <= cursor_dt)
    stmt = stmt.order_by(Medication.prescribed_at.desc(), Medication.id.desc()).limit(bounded_limit + 1)
    res = await db.execute(stmt)
    med_rows = res.scalars().all()
    if cursor_dt and cursor_id:
        med_rows = [
            m for m in med_rows
            if (m.prescribed_at < cursor_dt or (m.prescribed_at == cursor_dt and str(m.id) < cursor_id))
        ]
    page = med_rows[:bounded_limit]
    next_cursor = (
        _encode_keyset_cursor(page[-1].prescribed_at, page[-1].id)
        if len(med_rows) > bounded_limit and page
        else None
    )

    prescriptions = [
        {
            "prescription_id": str(m.id),
            "medication_name": m.name,
            "strength": m.strength,
            "frequency": m.frequency,
            "prescribed_at": m.prescribed_at.isoformat() if m.prescribed_at else None,
            "source": m.source,
            "source_display": "Clinician Prescribed" if m.source == "manual" else "Document Extracted",
            "risk_level": m.risk_level,
            "confidence": m.confidence,
            "has_source_document": m.source_document_id is not None,
            "source_document_id": str(m.source_document_id) if m.source_document_id else None,
        }
        for m in page
    ]

    return {
        "patient_id": patient_id,
        "prescriptions": prescriptions,
        "next_cursor": next_cursor,
    }


@router.get("/api/v2/patient/me/reports", status_code=status.HTTP_200_OK)
async def get_my_reports(
    response: Response,
    limit: int = 20,
    cursor: str | None = None,
    patient_id: str = Depends(require_self_patient_access()),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve diagnostic reports, imaging records, and clinical documents."""
    _apply_no_store_headers(response)
    pid_uuid = _parse_uuid(patient_id)
    bounded_limit = max(1, min(int(limit), 50))
    cursor_dt, cursor_id = _decode_keyset_cursor(cursor)

    stmt = select(DocumentReference).where(DocumentReference.patient_id == pid_uuid)
    if cursor_dt:
        stmt = stmt.where(DocumentReference.uploaded_at <= cursor_dt)
    stmt = stmt.order_by(DocumentReference.uploaded_at.desc(), DocumentReference.id.desc()).limit(bounded_limit + 1)
    res = await db.execute(stmt)
    doc_rows = res.scalars().all()
    if cursor_dt and cursor_id:
        doc_rows = [
            d for d in doc_rows
            if (d.uploaded_at < cursor_dt or (d.uploaded_at == cursor_dt and str(d.id) < cursor_id))
        ]
    page = doc_rows[:bounded_limit]
    next_cursor = (
        _encode_keyset_cursor(page[-1].uploaded_at, page[-1].id)
        if len(doc_rows) > bounded_limit and page
        else None
    )

    reports = [
        {
            "report_id": str(d.id),
            "report_title": f"Medical Report ({d.document_type})",
            "document_type": d.document_type,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            "source": "patient_uploaded",
            "source_display": "Patient Uploaded / External Source",
            "can_view_source": True,
        }
        for d in page
    ]

    return {
        "patient_id": patient_id,
        "reports": reports,
        "next_cursor": next_cursor,
    }


@router.get("/api/v2/patient/me/documents/{document_id}", status_code=status.HTTP_200_OK)
async def get_my_document_detail(
    document_id: str,
    response: Response,
    patient_id: str = Depends(require_self_patient_access()),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve safe metadata for a patient-owned source document without exposing internal storage keys."""
    _apply_no_store_headers(response)
    pid_uuid = _parse_uuid(patient_id)
    doc_uuid = _parse_uuid(document_id)

    stmt = select(DocumentReference).where(
        DocumentReference.id == doc_uuid,
        DocumentReference.patient_id == pid_uuid,
    ).limit(1)
    res = await db.execute(stmt)
    doc = res.scalar_one_or_none()

    if not doc:
        from app.models.pipeline import DocumentStorage
        stmt_ds = select(DocumentStorage).where(
            DocumentStorage.id == doc_uuid,
            DocumentStorage.patient_id == pid_uuid,
        ).limit(1)
        res_ds = await db.execute(stmt_ds)
        ds = res_ds.scalar_one_or_none()
        if not ds:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": "DOCUMENT_NOT_FOUND", "message": "Document not found"},
            )
        doc_type = "source_document"
        uploaded_at = ds.uploaded_at.isoformat() if hasattr(ds, "uploaded_at") and ds.uploaded_at else None
    else:
        doc_type = doc.document_type
        uploaded_at = doc.uploaded_at.isoformat() if doc.uploaded_at else None

    return {
        "document_id": str(doc_uuid),
        "patient_id": patient_id,
        "document_type": doc_type,
        "uploaded_at": uploaded_at,
        "is_owner": True,
        "view_authorized": True,
    }


@router.get("/api/v2/patient/me/access-history", status_code=status.HTTP_200_OK)
async def get_my_access_history(
    limit: int = 20,
    cursor: str | None = None,
    patient_id: str = Depends(require_self_patient_access()),
    db: AsyncSession = Depends(get_db_session),
):
    """Patient views audit ledger history of who accessed their data."""
    endpoint_started = perf_counter()
    bounded_limit = max(1, min(int(limit), 100))
    cursor_created_at, cursor_audit_id = _decode_access_history_cursor(cursor)
    try:
        connection_started = perf_counter()
        await db.connection()
        logger.info(
            "Patient access history timing",
            extra={
                "operation": "db_connection",
                "duration_ms": round((perf_counter() - connection_started) * 1000, 2),
                "row_count": 0,
            },
        )
        query_started = perf_counter()
        rows = await read_patient_access_history_events(
            db,
            str(patient_id),
            limit=bounded_limit + 1,
            cursor_created_at=cursor_created_at,
            cursor_audit_id=cursor_audit_id,
        )
        logger.info(
            "Patient access history timing",
            extra={
                "operation": "audit_query",
                "duration_ms": round((perf_counter() - query_started) * 1000, 2),
                "row_count": len(rows),
            },
        )
    except Exception as exc:
        try:
            await db.rollback()
        except Exception as rollback_exc:
            logger.warning(
                "Patient access history rollback failed",
                extra={"error_type": type(rollback_exc).__name__},
            )
        logger.error(
            "Patient access history store unavailable",
            extra={
                "operation": "audit_query",
                "duration_ms": round((perf_counter() - endpoint_started) * 1000, 2),
                "row_count": 0,
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "AUDIT_HISTORY_UNAVAILABLE"},
        ) from exc

    page_rows = rows[:bounded_limit]
    next_cursor = (
        _encode_access_history_cursor(page_rows[-1])
        if len(rows) > bounded_limit and page_rows
        else None
    )

    candidates: list[dict[str, Any]] = []
    for r in page_rows:
        payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        metadata = (
            payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        )
        if not metadata and isinstance(r.get("metadata"), dict):
            metadata = r.get("metadata")

        event_type = str(
            r.get("event_type") or payload.get("event") or r.get("event") or ""
        ).upper()
        allowed_statuses = _ACCESS_HISTORY_SUCCESS_STATUSES.get(event_type)
        event_status = str(r.get("status") or payload.get("status") or "").upper()
        if allowed_statuses is None or event_status not in allowed_statuses:
            continue

        actor_value = r.get("actor_uid") or payload.get("actor_uid")
        actor_uid = str(actor_value) if actor_value is not None else ""
        if metadata.get("access_type") == "self_access":
            continue
        is_break_glass = event_type == "BREAK_GLASS_EMERGENCY_SUMMARY_ACCESSED"
        if not is_break_glass and actor_uid == str(patient_id):
            continue

        candidates.append(
            {
                "row": r,
                "payload": payload,
                "metadata": metadata,
                "event_type": event_type,
                "actor_uid": actor_uid,
                "hospital_id": str(metadata.get("hospital_id") or ""),
                "is_break_glass": is_break_glass,
            }
        )

    provider_ids = {
        provider_id
        for candidate in candidates
        if (provider_id := _as_uuid(candidate["actor_uid"])) is not None
    }
    provider_rows = []
    if provider_ids:
        provider_lookup_started = perf_counter()
        provider_result = await db.execute(
            select(
                ProviderIdentity.id,
                ProviderIdentity.display_name,
                ProviderIdentity.hospital_id,
            ).where(ProviderIdentity.id.in_(provider_ids))
        )
        provider_rows = provider_result.all()
        logger.info(
            "Patient access history timing",
            extra={
                "operation": "provider_lookup",
                "duration_ms": round(
                    (perf_counter() - provider_lookup_started) * 1000, 2
                ),
                "row_count": len(provider_rows),
            },
        )

    provider_names: dict[str, str] = {}
    provider_hospital_ids: dict[str, str] = {}
    for provider_id, display_name, hospital_id in provider_rows:
        provider_key = str(provider_id)
        if display_name and str(display_name).strip():
            provider_names[provider_key] = str(display_name).strip()
        if hospital_id is not None:
            provider_hospital_ids[provider_key] = str(hospital_id)

    hospital_ids = {
        hospital_id
        for candidate in candidates
        if (
            hospital_id := _as_uuid(
                candidate["hospital_id"]
                or provider_hospital_ids.get(candidate["actor_uid"], "")
            )
        )
        is not None
    }
    hospital_rows = []
    if hospital_ids:
        hospital_lookup_started = perf_counter()
        hospital_result = await db.execute(
            select(HospitalRegistry.id, HospitalRegistry.display_name).where(
                HospitalRegistry.id.in_(hospital_ids)
            )
        )
        hospital_rows = hospital_result.all()
        logger.info(
            "Patient access history timing",
            extra={
                "operation": "hospital_lookup",
                "duration_ms": round(
                    (perf_counter() - hospital_lookup_started) * 1000, 2
                ),
                "row_count": len(hospital_rows),
            },
        )
    hospital_names = {
        str(hospital_id): str(display_name).strip()
        for hospital_id, display_name in hospital_rows
        if display_name and str(display_name).strip()
    }

    history = []
    seen_operations: set[str] = set()
    for candidate in candidates:
        r = candidate["row"]
        payload = candidate["payload"]
        metadata = candidate["metadata"]
        operation_id = metadata.get("audit_transaction_id") or metadata.get(
            "consent_request_id"
        )
        if operation_id:
            operation_key = str(operation_id)
            if operation_key in seen_operations:
                continue
            seen_operations.add(operation_key)

        actor_uid = candidate["actor_uid"]
        hospital_id = candidate["hospital_id"] or provider_hospital_ids.get(
            actor_uid, ""
        )
        doctor_name = provider_names.get(actor_uid, _FORMER_PROVIDER_LABEL)
        hospital_name = hospital_names.get(hospital_id, _UNKNOWN_FACILITY_LABEL)
        purpose = metadata.get("purpose") or r.get("purpose")
        if not purpose or not str(purpose).strip():
            purpose = _UNKNOWN_PURPOSE_LABEL
        else:
            purpose = str(purpose).strip()
        accessed_at_value = r.get("created_at") or payload.get("timestamp")
        accessed_at = str(accessed_at_value) if accessed_at_value is not None else None
        raw_scope = (
            metadata.get("scope")
            or metadata.get("data_categories")
            or metadata.get("categories")
            or []
        )
        data_categories = raw_scope if isinstance(raw_scope, list) else [str(raw_scope)]
        is_break_glass = candidate["is_break_glass"]

        history.append(
            {
                "audit_id": str(r.get("audit_id") or r.get("record_hash"))
                if (r.get("audit_id") or r.get("record_hash"))
                else None,
                "accessed_by": f"{doctor_name} ({hospital_name})",
                "doctor_name": doctor_name,
                "hospital_name": hospital_name,
                "purpose": purpose,
                "accessed_at": accessed_at,
                "data_categories": data_categories,
                "is_break_glass": is_break_glass,
                "flag": "BREAK_GLASS_ACCESS" if is_break_glass else "ROUTINE_ACCESS",
                "event_type": candidate["event_type"],
            }
        )

    logger.info(
        "Patient access history timing",
        extra={
            "operation": "total",
            "duration_ms": round((perf_counter() - endpoint_started) * 1000, 2),
            "row_count": len(history),
        },
    )
    return {
        "patient_id": patient_id,
        "access_history": history,
        "next_cursor": next_cursor,
    }


def _as_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


# ── Read Endpoints (Consent-Gated) ───────────────────────────────────────────


@router.get("/api/v2/patient/{id}/summary", status_code=status.HTTP_200_OK)
async def get_patient_summary(
    id: str,
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.RECORD_READ)
    ),
    capability=Depends(require_consent("clinical_summary")),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve de-identified or full clinical summary."""
    pid_uuid = _parse_uuid(id)

    stmt_v = (
        select(Vitals)
        .where(Vitals.patient_id == pid_uuid)
        .order_by(Vitals.recorded_at.desc())
        .limit(10)
    )
    res_v = await db.execute(stmt_v)
    vitals_rows = res_v.scalars().all()

    stmt_m = (
        select(Medication)
        .where(Medication.patient_id == pid_uuid)
        .order_by(Medication.prescribed_at.desc())
        .limit(10)
    )
    res_m = await db.execute(stmt_m)
    meds_rows = res_m.scalars().all()

    stmt_a = select(Allergy).where(Allergy.patient_id == pid_uuid).limit(10)
    res_a = await db.execute(stmt_a)
    alg_rows = res_a.scalars().all()

    stmt_l = (
        select(LabResult)
        .where(LabResult.patient_id == pid_uuid)
        .order_by(LabResult.recorded_at.desc())
        .limit(10)
    )
    res_l = await db.execute(stmt_l)
    lab_rows = res_l.scalars().all()

    vitals_list = [
        {
            "type": v.type,
            "value": v.value,
            "unit": v.unit,
            "recorded_at": v.recorded_at.isoformat(),
        }
        for v in vitals_rows
    ]
    meds_list = [
        {"name": m.name, "dosage": m.strength, "frequency": m.frequency}
        for m in meds_rows
    ]
    allergies_list = [f"{a.allergen} ({a.severity})" for a in alg_rows]

    labs_list = [
        {
            "test_name": lab.test_name,
            "value": lab.value,
            "unit": lab.unit,
            "reference_range": lab.reference_range,
            "is_abnormal": lab.is_abnormal,
            "recorded_at": lab.recorded_at.isoformat(),
            "source": lab.source,
        }
        for lab in lab_rows
    ]

    has_full = (
        capability is not None
        and hasattr(capability, "scope")
        and any(s in capability.scope for s in ("full", "pii", "pii.*"))
    )

    identity = await _read_vault_identity(pid_uuid, db) if has_full else {}

    return {
        "patient_id": id,
        "pii": {
            "patient_name": identity.get("patient_name") if has_full else "[REDACTED]",
            "phone": identity.get("phone") if has_full else "[REDACTED]",
            "aadhaar_abha_id": identity.get("aadhaar_abha_id")
            if has_full
            else "[REDACTED]",
        },
        "clinical_summary": {
            "blood_group": None,
            "blood_group_verification": "unknown",
            "blood_group_provenance": None,
            "allergies": allergies_list,
            "chronic_conditions": [],
            "active_conditions": [],
            "active_medications": meds_list,
            "current_medications": meds_list,
            "latest_vitals": vitals_list,
            "recent_labs": labs_list,
        },
        "shard_scope": "full" if has_full else "clinical",
    }


def _enrich_timeline_provenance(
    item_id: str,
    event_type: str,
    title: str,
    summary: str,
    dt_str: str,
    raw_source: str,
    confidence: float | None = None,
    risk_level: str | None = None,
    provider_name: str | None = None,
    review_status: str | None = None,
    document_type: str | None = None,
    source_page: int | None = None,
    record_id: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    if raw_source == "ai_extracted" or "ai_" in str(raw_source).lower():
        conf_val = float(confidence) if confidence is not None else None
        conf_pct = int(round(conf_val * 100)) if conf_val is not None else None
        risk_val = str(risk_level) if risk_level else None
        rev_val = (
            str(review_status).replace("_", " ").title() if review_status else None
        )
        source_parts = [str(document_type)] if document_type else []
        if source_page is not None:
            source_parts.append(f"Page {source_page}")
        source_detail = ", ".join(source_parts) or None
        source_display = "AI-extracted from document"
        if conf_pct is not None:
            source_display += f", {conf_pct}% confidence"
        badges = [
            badge
            for badge in [
                f"AI Extracted ({conf_pct}%)"
                if conf_pct is not None
                else "AI Extracted",
                f"Risk: {risk_val}" if risk_val else None,
                f"Reviewed: {rev_val}" if rev_val else None,
                f"Source: {source_detail}" if source_detail else None,
            ]
            if badge is not None
        ]
    else:
        conf_val = None
        risk_val = str(risk_level or "LOW_RISK") if risk_level else None
        pname = provider_name
        source_display = f"Manual entry by {pname}" if pname else "Manual entry"
        source_detail = (
            f"Manual provider entry ({pname})" if pname else "Manual provider entry"
        )
        rev_val = "N/A"
        badges = ["Manual Entry"] + ([f"By: {pname}"] if pname else [])

    return {
        "event_id": item_id,
        "record_id": record_id,
        "category": category,
        "event_type": event_type,
        "title": title,
        "summary": summary,
        "description": summary,
        "event_date": dt_str,
        "occurred_at": dt_str,
        "source": raw_source,
        "source_display": source_display,
        "provenance": source_display,
        "confidence": conf_val,
        "risk_level": risk_val,
        "review_status": rev_val,
        "source_detail": source_detail,
        "badges": badges,
    }


async def _fetch_and_merge_timeline(
    id_str: str, db: AsyncSession, limit: int = 50
) -> list[dict[str, Any]]:
    pid_uuid = _parse_uuid(id_str)
    events: list[dict[str, Any]] = []

    stmt_te = (
        select(TimelineEvent)
        .where(TimelineEvent.patient_id == pid_uuid)
        .order_by(TimelineEvent.occurred_at.desc())
        .limit(limit)
    )
    res_te = await db.execute(stmt_te)
    for te in res_te.scalars().all():
        if te.occurred_at is None:
            continue
        dt_str = te.occurred_at.isoformat()
        events.append(
            _enrich_timeline_provenance(
                str(te.id), te.event_type, te.event_type, te.summary, dt_str, te.source
            )
        )

    stmt_v = (
        select(Vitals)
        .where(Vitals.patient_id == pid_uuid)
        .order_by(Vitals.recorded_at.desc())
        .limit(limit)
    )
    res_v = await db.execute(stmt_v)
    for v in res_v.scalars().all():
        if v.recorded_at is None:
            continue
        dt_str = v.recorded_at.isoformat()
        events.append(
            _enrich_timeline_provenance(
                str(v.id),
                "VITALS",
                f"Vitals Recorded ({v.type})",
                f"{v.type}: {v.value} {v.unit}",
                dt_str,
                v.source,
                v.confidence,
                v.risk_level,
            )
        )

    stmt_m = (
        select(Medication)
        .where(Medication.patient_id == pid_uuid)
        .order_by(Medication.prescribed_at.desc())
        .limit(limit)
    )
    res_m = await db.execute(stmt_m)
    for m in res_m.scalars().all():
        if m.prescribed_at is None:
            continue
        dt_str = m.prescribed_at.isoformat()
        events.append(
            _enrich_timeline_provenance(
                str(m.id),
                "MEDICATION",
                f"Medication Prescribed ({m.name})",
                f"{m.name} {m.strength} ({m.frequency})",
                dt_str,
                m.source,
                m.confidence,
                m.risk_level,
            )
        )

    stmt_l = (
        select(LabResult)
        .where(LabResult.patient_id == pid_uuid)
        .order_by(LabResult.recorded_at.desc())
        .limit(limit)
    )
    res_l = await db.execute(stmt_l)
    for lab in res_l.scalars().all():
        if lab.recorded_at is None:
            continue
        dt_str = lab.recorded_at.isoformat()
        events.append(
            _enrich_timeline_provenance(
                str(lab.id),
                "LAB_RESULT",
                f"Lab Result ({lab.test_name})",
                f"{lab.test_name}: {lab.value} {lab.unit}",
                dt_str,
                lab.source,
                lab.confidence,
                lab.risk_level,
            )
        )

    stmt_d = (
        select(DocumentReference)
        .where(DocumentReference.patient_id == pid_uuid)
        .order_by(DocumentReference.uploaded_at.desc())
        .limit(limit)
    )
    res_d = await db.execute(stmt_d)
    for d in res_d.scalars().all():
        if d.uploaded_at is None:
            continue
        dt_str = d.uploaded_at.isoformat()
        events.append(
            _enrich_timeline_provenance(
                str(d.id),
                "DOCUMENT",
                f"Document Uploaded ({d.document_type})",
                f"Uploaded clinical document: {d.document_type}",
                dt_str,
                "manual",
            )
        )

    seen = set()
    deduped = []
    for e in events:
        if e["event_id"] not in seen:
            seen.add(e["event_id"])
            deduped.append(e)

    deduped.sort(
        key=lambda x: str(x.get("occurred_at", x.get("event_date", ""))), reverse=True
    )

    return deduped[:limit]


async def _fetch_patient_longitudinal_timeline(
    id_str: str,
    db: AsyncSession,
    limit: int = 20,
    cursor: str | None = None,
    category: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    pid_uuid = _parse_uuid(id_str)
    bounded_limit = max(1, min(int(limit), 50))
    cursor_dt, cursor_id = _decode_keyset_cursor(cursor)

    cat_norm = category.strip().lower() if category else None
    valid_categories = {
        "all",
        "vitals",
        "medication",
        "medications",
        "lab_result",
        "labs",
        "document",
        "documents",
        "allergy",
        "allergies",
        "encounter",
        "encounters",
    }
    if cat_norm and cat_norm not in valid_categories:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "INVALID_TIMELINE_CATEGORY",
                "message": f"Unsupported category filter: {category}",
            },
        )

    include_all = cat_norm is None or cat_norm == "all"
    include_vitals = include_all or cat_norm == "vitals"
    include_meds = include_all or cat_norm in {"medication", "medications"}
    include_labs = include_all or cat_norm in {"lab_result", "labs"}
    include_docs = include_all or cat_norm in {"document", "documents"}
    include_allergies = include_all or cat_norm in {"allergy", "allergies"}

    fetch_limit = bounded_limit + 15
    candidates: list[dict[str, Any]] = []
    seen_entity_keys: set[str] = set()

    # 1. Vitals
    if include_vitals:
        stmt_v = select(Vitals).where(Vitals.patient_id == pid_uuid)
        if cursor_dt:
            stmt_v = stmt_v.where(Vitals.recorded_at <= cursor_dt)
        stmt_v = stmt_v.order_by(Vitals.recorded_at.desc(), Vitals.id.desc()).limit(fetch_limit)
        res_v = await db.execute(stmt_v)
        for v in res_v.scalars().all():
            if v.recorded_at is None:
                continue
            dt_str = v.recorded_at.isoformat()
            seen_entity_keys.add(f"vitals:{str(v.id)}")
            seen_entity_keys.add(f"vitals_dt:{dt_str}")
            candidates.append(
                _enrich_timeline_provenance(
                    str(v.id),
                    "VITALS",
                    f"Vitals Recorded ({v.type})",
                    f"{v.type}: {v.value} {v.unit}",
                    dt_str,
                    v.source,
                    v.confidence,
                    v.risk_level,
                    record_id=str(v.id),
                    category="vitals",
                )
            )

    # 2. Medications
    if include_meds:
        stmt_m = select(Medication).where(Medication.patient_id == pid_uuid)
        if cursor_dt:
            stmt_m = stmt_m.where(Medication.prescribed_at <= cursor_dt)
        stmt_m = stmt_m.order_by(Medication.prescribed_at.desc(), Medication.id.desc()).limit(fetch_limit)
        res_m = await db.execute(stmt_m)
        for m in res_m.scalars().all():
            if m.prescribed_at is None:
                continue
            dt_str = m.prescribed_at.isoformat()
            seen_entity_keys.add(f"medication:{str(m.id)}")
            seen_entity_keys.add(f"medication_dt:{dt_str}")
            candidates.append(
                _enrich_timeline_provenance(
                    str(m.id),
                    "MEDICATION",
                    f"Medication Prescribed ({m.name})",
                    f"{m.name} {m.strength} ({m.frequency})",
                    dt_str,
                    m.source,
                    m.confidence,
                    m.risk_level,
                    record_id=str(m.id),
                    category="medications",
                )
            )

    # 3. Lab Results
    if include_labs:
        stmt_l = select(LabResult).where(LabResult.patient_id == pid_uuid)
        if cursor_dt:
            stmt_l = stmt_l.where(LabResult.recorded_at <= cursor_dt)
        stmt_l = stmt_l.order_by(LabResult.recorded_at.desc(), LabResult.id.desc()).limit(fetch_limit)
        res_l = await db.execute(stmt_l)
        for lab in res_l.scalars().all():
            if lab.recorded_at is None:
                continue
            dt_str = lab.recorded_at.isoformat()
            seen_entity_keys.add(f"lab:{str(lab.id)}")
            seen_entity_keys.add(f"lab_dt:{dt_str}")
            summary_text = f"{lab.test_name}: {lab.value} {lab.unit}"
            if lab.is_abnormal:
                summary_text += " [ABNORMAL]"
            candidates.append(
                _enrich_timeline_provenance(
                    str(lab.id),
                    "LAB_RESULT",
                    f"Lab Result ({lab.test_name})",
                    summary_text,
                    dt_str,
                    lab.source,
                    lab.confidence,
                    lab.risk_level,
                    record_id=str(lab.id),
                    category="labs",
                )
            )

    # 4. Documents
    if include_docs:
        stmt_d = select(DocumentReference).where(DocumentReference.patient_id == pid_uuid)
        if cursor_dt:
            stmt_d = stmt_d.where(DocumentReference.uploaded_at <= cursor_dt)
        stmt_d = stmt_d.order_by(DocumentReference.uploaded_at.desc(), DocumentReference.id.desc()).limit(fetch_limit)
        res_d = await db.execute(stmt_d)
        for d in res_d.scalars().all():
            if d.uploaded_at is None:
                continue
            dt_str = d.uploaded_at.isoformat()
            seen_entity_keys.add(f"document:{str(d.id)}")
            seen_entity_keys.add(f"document_dt:{dt_str}")
            candidates.append(
                _enrich_timeline_provenance(
                    str(d.id),
                    "DOCUMENT",
                    f"Document Uploaded ({d.document_type})",
                    f"Uploaded clinical document: {d.document_type}",
                    dt_str,
                    "manual",
                    record_id=str(d.id),
                    category="documents",
                )
            )

    # 5. Timeline Events
    stmt_te = select(TimelineEvent).where(TimelineEvent.patient_id == pid_uuid)
    if cursor_dt:
        stmt_te = stmt_te.where(TimelineEvent.occurred_at <= cursor_dt)
    stmt_te = stmt_te.order_by(TimelineEvent.occurred_at.desc(), TimelineEvent.id.desc()).limit(fetch_limit)
    res_te = await db.execute(stmt_te)
    for te in res_te.scalars().all():
        if te.occurred_at is None:
            continue
        dt_str = te.occurred_at.isoformat()

        # Check deduplication against existing typed records
        ref_id_str = str(te.event_ref_id) if te.event_ref_id else None
        if ref_id_str:
            if (
                f"vitals:{ref_id_str}" in seen_entity_keys
                or f"medication:{ref_id_str}" in seen_entity_keys
                or f"lab:{ref_id_str}" in seen_entity_keys
                or f"document:{ref_id_str}" in seen_entity_keys
            ):
                continue

        te_type_upper = te.event_type.upper()
        if not include_all:
            if include_vitals and te_type_upper != "VITALS":
                continue
            if include_meds and te_type_upper != "MEDICATION":
                continue
            if include_labs and te_type_upper not in {"LAB_RESULT", "LAB"}:
                continue
            if include_docs and te_type_upper not in {"DOCUMENT", "EXTRACTED_DATA_INGESTED"}:
                continue
            if include_allergies and te_type_upper != "ALLERGY":
                continue

        type_prefix = te_type_upper.lower()
        if f"{type_prefix}_dt:{dt_str}" in seen_entity_keys:
            continue

        category_label = "general"
        if te_type_upper == "VITALS":
            category_label = "vitals"
        elif te_type_upper == "MEDICATION":
            category_label = "medications"
        elif te_type_upper in {"LAB_RESULT", "LAB"}:
            category_label = "labs"
        elif te_type_upper in {"DOCUMENT", "EXTRACTED_DATA_INGESTED"}:
            category_label = "documents"
        elif te_type_upper == "ALLERGY":
            category_label = "allergies"

        title = te.event_type.replace("_", " ").title()
        if te.event_type == "EXTRACTED_DATA_INGESTED":
            title = "Clinical Document Ingested"

        candidates.append(
            _enrich_timeline_provenance(
                str(te.id),
                te.event_type,
                title,
                te.summary,
                dt_str,
                te.source,
                record_id=ref_id_str,
                category=category_label,
            )
        )

    # Sort deterministically
    candidates.sort(
        key=lambda x: (str(x.get("occurred_at", "")), str(x.get("event_id", ""))),
        reverse=True,
    )

    # Keyset filter by cursor
    if cursor_dt and cursor_id:
        cursor_dt_str = cursor_dt.isoformat()
        candidates = [
            c
            for c in candidates
            if (str(c.get("occurred_at", "")), str(c.get("event_id", "")))
            < (cursor_dt_str, cursor_id)
        ]

    if len(candidates) > bounded_limit:
        page = candidates[:bounded_limit]
        next_cursor = _encode_keyset_cursor(
            page[-1]["occurred_at"], page[-1]["event_id"]
        )
    else:
        page = candidates
        next_cursor = None

    return page, next_cursor


@router.get("/api/v2/patient/{id}/timeline", status_code=status.HTTP_200_OK)
async def get_patient_timeline(
    id: str,
    limit: int = 20,
    cursor: str | None = None,
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.RECORD_READ)
    ),
    capability=Depends(require_consent("timeline_view")),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve chronological clinical timeline events."""
    events = await _fetch_and_merge_timeline(id, db, limit=limit)
    return {
        "patient_id": id,
        "events": events,
        "next_cursor": None,
    }


@router.get("/api/v2/patient/{id}/audit-trail", status_code=status.HTTP_200_OK)
async def get_patient_audit_trail(
    id: str,
    limit: int = 50,
    provider: ProviderContext = Depends(require_role("admin")),
):
    """Admin & Auditor Console view: returns complete, unfiltered audit ledger trail for a patient."""
    _parse_uuid(id)
    audit_context = AuditContext.for_hospital(
        hospital_id=str(provider.hospital.hospital_id),
        domain=AuditDomain.PATIENT_RECORD,
    )
    try:
        rows = await read_audit_events(
            str(id), audit_context=audit_context, limit=limit
        )
    except Exception as exc:
        logger.error(
            "Admin audit trail store unavailable",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "AUDIT_HISTORY_UNAVAILABLE"},
        ) from exc

    trail = [
        {
            "audit_id": str(r.get("audit_id") or r.get("record_hash"))
            if (r.get("audit_id") or r.get("record_hash"))
            else None,
            "actor_uid": r.get("actor_uid"),
            "event_type": r.get("event_type"),
            "timestamp": r.get("created_at"),
            "status": r.get("status"),
            "payload": r.get("payload", {}),
        }
        for r in rows
    ]
    return {
        "patient_id": id,
        "audit_trail": trail,
    }


# ── Structured Full Record Endpoints ─────────────────────────────────────────


@router.get("/api/v2/patient/{id}/records", status_code=status.HTTP_200_OK)
@router.get("/api/v2/patient/{id}/structured-record", status_code=status.HTTP_200_OK)
async def get_patient_structured_record(
    id: str,
    provider: ProviderContext = Depends(
        require_clinical_capability(ClinicalCapability.RECORD_READ)
    ),
    capability=Depends(require_consent("full")),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve full structured record (all sub-models)."""
    pid_uuid = _parse_uuid(id)

    res_v = await db.execute(select(Vitals).where(Vitals.patient_id == pid_uuid))
    vitals = [
        {
            "id": str(v.id),
            "type": v.type,
            "value": v.value,
            "unit": v.unit,
            "recorded_at": v.recorded_at.isoformat(),
            "source": v.source,
            "risk_level": v.risk_level,
        }
        for v in res_v.scalars().all()
    ]

    res_m = await db.execute(
        select(Medication).where(Medication.patient_id == pid_uuid)
    )
    meds = [
        {
            "id": str(m.id),
            "name": m.name,
            "strength": m.strength,
            "frequency": m.frequency,
            "prescribed_at": m.prescribed_at.isoformat(),
            "source": m.source,
            "risk_level": m.risk_level,
        }
        for m in res_m.scalars().all()
    ]

    res_l = await db.execute(select(LabResult).where(LabResult.patient_id == pid_uuid))
    labs = [
        {
            "id": str(lab.id),
            "test_name": lab.test_name,
            "value": lab.value,
            "unit": lab.unit,
            "reference_range": lab.reference_range,
            "is_abnormal": lab.is_abnormal,
            "recorded_at": lab.recorded_at.isoformat(),
            "source": lab.source,
            "risk_level": lab.risk_level,
        }
        for lab in res_l.scalars().all()
    ]

    res_a = await db.execute(select(Allergy).where(Allergy.patient_id == pid_uuid))
    allergies = [
        {
            "id": str(a.id),
            "allergen": a.allergen,
            "severity": a.severity,
            "source": a.source,
            "risk_level": a.risk_level,
        }
        for a in res_a.scalars().all()
    ]

    res_d = await db.execute(
        select(DocumentReference).where(DocumentReference.patient_id == pid_uuid)
    )
    docs = [
        {
            "id": str(d.id),
            "document_type": d.document_type,
            "storage_ref": d.storage_ref,
            "uploaded_at": d.uploaded_at.isoformat(),
        }
        for d in res_d.scalars().all()
    ]

    return {
        "patient_id": id,
        "vitals": vitals,
        "medications": meds,
        "lab_results": labs,
        "allergies": allergies,
        "documents": docs,
    }


# ── Write Endpoints (Provider-Authed, Audit-Before-Write) ────────────────────


@router.post("/api/v2/patient/{id}/records/vitals", status_code=status.HTTP_201_CREATED)
@router.post("/api/v2/patient/{id}/record/vitals", status_code=status.HTTP_201_CREATED)
async def append_vitals(
    id: str,
    payload: AppendVitalsRequest,
    provider: ProviderContext = Depends(get_current_provider),
    capability=Depends(require_consent("clinical_append")),
    db: AsyncSession = Depends(get_db_session),
):
    """Append structured vitals observation with audit-before-write guarantee."""
    _validate_provenance(
        payload.source,
        payload.confidence,
        payload.risk_level,
        payload.source_document_id,
    )
    actor_uid = provider.actor_uid if provider else "UNKNOWN"
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
        actor_uid=actor_uid,
        event_type="PATIENT_RECORD_APPEND_ATTEMPT",
        target_id=id,
        status="STARTED",
        metadata={"type": "vitals"},
    )

    pid_uuid = _parse_uuid(id)
    rec_dt = payload.recorded_at
    doc_uuid = payload.source_document_id

    v_bp = Vitals(
        id=uuid.uuid4(),
        patient_id=pid_uuid,
        type="BP",
        value=f"{payload.systolic_bp}/{payload.diastolic_bp}",
        unit="mmHg",
        recorded_at=rec_dt,
        source=payload.source,
        confidence=payload.confidence,
        risk_level=payload.risk_level,
        source_document_id=doc_uuid,
    )
    db.add(v_bp)

    tl = TimelineEvent(
        patient_id=pid_uuid,
        event_type="VITALS",
        occurred_at=rec_dt,
        source=payload.source,
        summary=f"Vitals recorded: {payload.systolic_bp}/{payload.diastolic_bp} mmHg, HR {payload.heart_rate}",
    )
    db.add(tl)
    await db.flush()
    if v_bp.id is None:
        raise RuntimeError("Vitals record identifier was not assigned during flush")
    record_id = str(v_bp.id)
    await _stage_patient_record_success_audit(
        db,
        actor_uid=actor_uid,
        patient_id=id,
        record_type="vitals",
        record_id=record_id,
    )
    await _commit_patient_record_transaction(db)

    return {
        "record_id": record_id,
        "patient_id": id,
        "status": "committed",
        "audit_ledger_hash": None,
    }


@router.post(
    "/api/v2/patient/{id}/records/medications", status_code=status.HTTP_201_CREATED
)
@router.post(
    "/api/v2/patient/{id}/record/medications", status_code=status.HTTP_201_CREATED
)
async def append_medications(
    id: str,
    payload: AppendMedicationRequest,
    provider: ProviderContext = Depends(get_current_provider),
    capability=Depends(require_consent("clinical_append")),
    db: AsyncSession = Depends(get_db_session),
):
    """Append structured medication prescription with audit-before-write guarantee."""
    _validate_provenance(
        payload.source,
        payload.confidence,
        payload.risk_level,
        payload.source_document_id,
    )
    actor_uid = provider.actor_uid if provider else "UNKNOWN"
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
        actor_uid=actor_uid,
        event_type="PATIENT_RECORD_APPEND_ATTEMPT",
        target_id=id,
        status="STARTED",
        metadata={"type": "medications"},
    )

    pid_uuid = _parse_uuid(id)
    rec_dt = payload.prescribed_at
    doc_uuid = payload.source_document_id

    med = Medication(
        id=uuid.uuid4(),
        patient_id=pid_uuid,
        name=payload.name,
        strength=payload.strength,
        frequency=payload.frequency,
        prescribed_at=rec_dt,
        source=payload.source,
        confidence=payload.confidence,
        risk_level=payload.risk_level,
        source_document_id=doc_uuid,
    )
    db.add(med)

    tl = TimelineEvent(
        patient_id=pid_uuid,
        event_type="MEDICATION",
        occurred_at=rec_dt,
        source=payload.source,
        summary=f"Medication prescribed: {payload.name} {payload.strength} ({payload.frequency})",
    )
    db.add(tl)
    await db.flush()
    if med.id is None:
        raise RuntimeError("Medication record identifier was not assigned during flush")
    record_id = str(med.id)
    await _stage_patient_record_success_audit(
        db,
        actor_uid=actor_uid,
        patient_id=id,
        record_type="medications",
        record_id=record_id,
    )
    await _commit_patient_record_transaction(db)

    return {
        "record_id": record_id,
        "patient_id": id,
        "status": "committed",
        "audit_ledger_hash": None,
    }


@router.post("/api/v2/patient/{id}/records/labs", status_code=status.HTTP_201_CREATED)
@router.post("/api/v2/patient/{id}/record/labs", status_code=status.HTTP_201_CREATED)
async def append_labs(
    id: str,
    payload: AppendLabResultRequest,
    provider: ProviderContext = Depends(get_current_provider),
    capability=Depends(require_consent("clinical_append")),
    db: AsyncSession = Depends(get_db_session),
):
    """Append structured lab result observation with audit-before-write guarantee."""
    _validate_provenance(
        payload.source,
        payload.confidence,
        payload.risk_level,
        payload.source_document_id,
    )
    actor_uid = provider.actor_uid if provider else "UNKNOWN"
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
        actor_uid=actor_uid,
        event_type="PATIENT_RECORD_APPEND_ATTEMPT",
        target_id=id,
        status="STARTED",
        metadata={"type": "labs"},
    )

    pid_uuid = _parse_uuid(id)
    rec_dt = payload.recorded_at
    doc_uuid = payload.source_document_id

    lab = LabResult(
        id=uuid.uuid4(),
        patient_id=pid_uuid,
        test_name=payload.test_name,
        value=payload.value,
        unit=payload.unit,
        reference_range=payload.reference_range,
        is_abnormal=payload.is_abnormal,
        recorded_at=rec_dt,
        source=payload.source,
        confidence=payload.confidence,
        risk_level=payload.risk_level,
        source_document_id=doc_uuid,
    )
    db.add(lab)

    tl = TimelineEvent(
        patient_id=pid_uuid,
        event_type="LAB_RESULT",
        occurred_at=rec_dt,
        source=payload.source,
        summary=f"Lab result committed: {payload.test_name} ({payload.value} {payload.unit})",
    )
    db.add(tl)
    await db.flush()
    if lab.id is None:
        raise RuntimeError("Lab record identifier was not assigned during flush")
    record_id = str(lab.id)
    await _stage_patient_record_success_audit(
        db,
        actor_uid=actor_uid,
        patient_id=id,
        record_type="labs",
        record_id=record_id,
    )
    await _commit_patient_record_transaction(db)

    return {
        "record_id": record_id,
        "patient_id": id,
        "status": "committed",
        "audit_ledger_hash": None,
    }


@router.post(
    "/api/v2/patient/{id}/records/allergies", status_code=status.HTTP_201_CREATED
)
@router.post(
    "/api/v2/patient/{id}/record/allergies", status_code=status.HTTP_201_CREATED
)
async def append_allergies(
    id: str,
    payload: AppendAllergyRequest,
    provider: ProviderContext = Depends(get_current_provider),
    capability=Depends(require_consent("clinical_append")),
    db: AsyncSession = Depends(get_db_session),
):
    """Append structured allergy sensitivity observation with audit-before-write guarantee."""
    _validate_provenance(
        payload.source,
        payload.confidence,
        payload.risk_level,
        payload.source_document_id,
    )
    if payload.risk_level != "HIGH_RISK":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Allergy risk_level strictly defaults to and requires HIGH_RISK",
        )

    actor_uid = provider.actor_uid if provider else "UNKNOWN"
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
        actor_uid=actor_uid,
        event_type="PATIENT_RECORD_APPEND_ATTEMPT",
        target_id=id,
        status="STARTED",
        metadata={"type": "allergies"},
    )

    pid_uuid = _parse_uuid(id)
    doc_uuid = payload.source_document_id

    alg = Allergy(
        id=uuid.uuid4(),
        patient_id=pid_uuid,
        allergen=payload.allergen,
        severity=payload.severity,
        source=payload.source,
        confidence=payload.confidence,
        risk_level=payload.risk_level,
        source_document_id=doc_uuid,
    )
    db.add(alg)

    now = datetime.now(timezone.utc)
    tl = TimelineEvent(
        patient_id=pid_uuid,
        event_type="ALLERGY",
        occurred_at=now,
        source=payload.source,
        summary=f"Allergy recorded: {payload.allergen} ({payload.severity})",
    )
    db.add(tl)
    await db.flush()
    if alg.id is None:
        raise RuntimeError("Allergy record identifier was not assigned during flush")
    record_id = str(alg.id)
    await _stage_patient_record_success_audit(
        db,
        actor_uid=actor_uid,
        patient_id=id,
        record_type="allergies",
        record_id=record_id,
    )
    await _commit_patient_record_transaction(db)

    return {
        "record_id": record_id,
        "patient_id": id,
        "status": "committed",
        "audit_ledger_hash": None,
    }


@router.post(
    "/api/v2/patient/{id}/records/documents", status_code=status.HTTP_201_CREATED
)
@router.post(
    "/api/v2/patient/{id}/record/documents", status_code=status.HTTP_201_CREATED
)
async def append_documents(
    id: str,
    payload: AppendDocumentRequest,
    provider: ProviderContext = Depends(get_current_provider),
    capability=Depends(require_consent("clinical_append")),
    db: AsyncSession = Depends(get_db_session),
):
    """Append structured clinical document reference with audit-before-write guarantee."""
    _validate_provenance(
        payload.source,
        payload.confidence,
        payload.risk_level,
        payload.source_document_id,
    )
    actor_uid = provider.actor_uid if provider else "UNKNOWN"
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
        actor_uid=actor_uid,
        event_type="PATIENT_RECORD_APPEND_ATTEMPT",
        target_id=id,
        status="STARTED",
        metadata={"type": "documents"},
    )

    pid_uuid = _parse_uuid(id)
    now = datetime.now(timezone.utc)

    job_uuid = payload.extraction_job_id

    doc = DocumentReference(
        id=uuid.uuid4(),
        patient_id=pid_uuid,
        document_type=payload.document_type,
        uploaded_at=now,
        storage_ref=payload.storage_ref,
        extraction_job_id=job_uuid,
    )
    db.add(doc)

    tl = TimelineEvent(
        patient_id=pid_uuid,
        event_type="DOCUMENT",
        occurred_at=now,
        source=payload.source,
        summary=f"Document uploaded: {payload.document_type}",
    )
    db.add(tl)
    await db.flush()
    if doc.id is None:
        raise RuntimeError("Document record identifier was not assigned during flush")
    record_id = str(doc.id)
    await _stage_patient_record_success_audit(
        db,
        actor_uid=actor_uid,
        patient_id=id,
        record_type="documents",
        record_id=record_id,
    )
    await _commit_patient_record_transaction(db)

    return {
        "record_id": record_id,
        "patient_id": id,
        "status": "committed",
        "audit_ledger_hash": None,
    }

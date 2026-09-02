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

from fastapi import APIRouter, Depends, HTTPException, status
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


@router.get("/api/v2/patient/me/timeline", status_code=status.HTTP_200_OK)
async def get_my_timeline(
    limit: int = 20,
    cursor: str | None = None,
    patient_id: str = Depends(require_self_patient_access()),
    db: AsyncSession = Depends(get_db_session),
):
    """Patient views their own timeline using patient session (no doctor consent needed)."""
    events = await _fetch_and_merge_timeline(patient_id, db, limit=limit)
    return {
        "patient_id": patient_id,
        "events": events,
        "next_cursor": None,
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

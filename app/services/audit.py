"""Audit-before-read helpers for reconstruction workflows."""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import uuid4

from app.observability.audit_ledger import append_audit_log_or_503


@asynccontextmanager
async def audit_read(clinician_id: str, patient_id: str, purpose: str) -> AsyncIterator[str]:
    """Write immutable view evidence before yielding permission to read shards."""

    audit_transaction_id = str(uuid4())
    started_at = datetime.now(timezone.utc).isoformat()

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PLATFORM),
        actor_uid=clinician_id,
        event_type="PATIENT_RECORD_VIEW_STARTED",
        target_id=patient_id,
        status="STARTED",
        metadata={
            "audit_transaction_id": audit_transaction_id,
            "patient_id": patient_id,
            "clinician_id": clinician_id,
            "purpose": purpose,
            "started_at": started_at,
        },
        event_timestamp=started_at,
    )

    try:
        yield audit_transaction_id
    except Exception:
        failed_at = datetime.now(timezone.utc).isoformat()
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.PLATFORM),
            actor_uid=clinician_id,
            event_type="PATIENT_RECORD_VIEW_FAILED",
            target_id=patient_id,
            status="FAILED",
            metadata={
                "audit_transaction_id": audit_transaction_id,
                "patient_id": patient_id,
                "clinician_id": clinician_id,
                "purpose": purpose,
                "failed_at": failed_at,
            },
            event_timestamp=failed_at,
        )
        raise
    else:
        completed_at = datetime.now(timezone.utc).isoformat()
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.PLATFORM),
            actor_uid=clinician_id,
            event_type="PATIENT_RECORD_VIEW_COMPLETED",
            target_id=patient_id,
            status="COMPLETED",
            metadata={
                "audit_transaction_id": audit_transaction_id,
                "patient_id": patient_id,
                "clinician_id": clinician_id,
                "purpose": purpose,
                "completed_at": completed_at,
            },
            event_timestamp=completed_at,
        )

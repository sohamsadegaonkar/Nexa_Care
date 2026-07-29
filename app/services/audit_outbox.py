"""Transactional persistence boundary for durable audit events."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.security.audit_context import AuditContext, derive_audit_partition

_OUTBOX_INSERT_SQL = text(
    """
    INSERT INTO public.audit_outbox
        (id, event_id, idempotency_key, chain_partition, event_type, actor_id,
         tenant_id, patient_id, payload, status, attempt_count, available_at, created_at)
    VALUES
        (gen_random_uuid(), gen_random_uuid(), :idempotency_key, :chain_partition,
         :event_type, :actor_id, :tenant_id, :patient_id, CAST(:payload AS JSONB),
         'pending', 0, now(), now())
    """
)


async def enqueue_audit_event(
    db: AsyncSession,
    *,
    audit_context: AuditContext,
    idempotency_key: str,
    actor_id: str,
    event_type: str,
    target_id: str,
    patient_id: str | None,
    status: str = "SUCCESS",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Stage an audit event on ``db`` without committing its transaction."""
    payload = {
        "target_id": str(target_id),
        "status": status,
        "metadata": metadata or {},
        "tenant_id": audit_context.tenant_id,
        "hospital_id": audit_context.hospital_id,
        "audit_domain": audit_context.domain.value,
    }
    await db.execute(
        _OUTBOX_INSERT_SQL,
        {
            "idempotency_key": idempotency_key,
            "chain_partition": derive_audit_partition(audit_context),
            "event_type": event_type,
            "actor_id": str(actor_id),
            "tenant_id": audit_context.tenant_id,
            "patient_id": str(patient_id) if patient_id is not None else None,
            "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
        },
    )

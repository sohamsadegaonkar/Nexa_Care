"""Emergency snapshot retrieval routes for Nexa Care V2."""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_provider_context
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log
from app.services.card_resolution_service import CardResolutionService
from app.services.emergency_snapshot_service import get_emergency_snapshot

router = APIRouter(prefix="/api/v2/emergency", tags=["emergency"])


class NFCReadRequest(BaseModel):
    """Strict request body for a physical NFC card tap."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    card_uid: str = Field(..., min_length=1, max_length=128)


class EmergencySnapshotResponse(BaseModel):
    """Typed emergency snapshot response returned to authenticated providers."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    patient_id: UUID
    snapshot_status: Literal[
        "available",
        "no_known_medical_data",
        "records_exist_but_snapshot_unavailable",
        "error",
    ]
    message: str | None = None
    snapshot: dict[str, JsonValue] = Field(default_factory=dict)
    retrieved_at: datetime


@router.post("/read-card", response_model=EmergencySnapshotResponse)
async def read_emergency_card(
    payload: NFCReadRequest,
    provider: ProviderContext = Depends(get_provider_context),
    db_session: AsyncSession = Depends(get_db_session),
) -> EmergencySnapshotResponse:
    """Read a patient's emergency snapshot from a tapped NFC card.

    Fail-closed chain of trust:
    1. Auth: ``get_provider_context`` rejects unauthenticated providers.
    2. Resolution: ``CardResolutionService`` rejects unknown or inactive cards.
    3. Audit: ``SNAPSHOT_ACCESSED`` is durably recorded before retrieval.
    4. Retrieval: the read-only projection service returns emergency data or a
       structured ``No Known Medical Data`` response.

    The audit event records provider, hospital, patient, and timestamp context
    only. It never stores the returned medical snapshot payload.
    """

    resolver = CardResolutionService(db_session)
    patient_id = await resolver.resolve_card(payload.card_uid)

    access_timestamp = datetime.now(timezone.utc).isoformat()
    audit_success = await append_audit_log(
        audit_context=current_audit_context(AuditDomain.EMERGENCY),
        actor_uid=provider.actor_uid,
        event_type="SNAPSHOT_ACCESSED",
        target_id=str(patient_id),
        status="SUCCESS",
        metadata={
            "hospital_id": str(provider.hospital.hospital_id),
            "patient_id": str(patient_id),
            "access_timestamp": access_timestamp,
        },
        event_timestamp=access_timestamp,
    )
    if not audit_success:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Audit ledger write failed; emergency snapshot access aborted.",
        )

    snapshot_payload = await get_emergency_snapshot(patient_id, db_session)
    return EmergencySnapshotResponse.model_validate(snapshot_payload)

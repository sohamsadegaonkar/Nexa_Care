"""Provider Clinical Workspace Routes.

Provides server-derived clinical workspace state for the authenticated provider:
- Active Treatment Sessions (indexed on provider_id, hospital_id, status, expires_at)
- Recent Clinical Encounters
- Safe patient display descriptors (public_patient_id, decrypted name where permitted)
- High-level workspace counts

Strictly enforces:
- Server-side derivation of provider_id and hospital_id from ProviderContext
- No exposure of token_hash, provider_session_binding_hash, or raw capabilities
- No exposure of raw patient PII (Aadhaar, ABHA, phone)
- Strict tenant and hospital isolation
"""

from __future__ import annotations

import logging
from typing import Any
import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_provider_context
from app.models.clinical_access_session import ClinicalAccessSessionRecord
from app.models.clinical_encounter import ClinicalEncounter
from app.models.consent_grant import ConsentGrantLog
from app.models.patient import Patient
from app.models.patient_profile import PatientProfile
from app.models.provider_context import ProviderContext
from app.services.crypto_kms import (
    EncryptedField,
    get_encryption_provider,
)

logger = logging.getLogger("nexa_logger")

router = APIRouter(prefix="/api/v2/provider", tags=["provider-workspace"])


class ActiveTreatmentSessionItem(BaseModel):
    session_id: str
    encounter_id: str | None
    patient_id: str
    patient_display_identifier: str
    patient_name: str | None
    status: str
    purpose: str
    scope: str
    allowed_operations: list[str]
    issued_at: str
    expires_at: str


class RecentEncounterItem(BaseModel):
    encounter_id: str
    clinical_session_id: str
    patient_id: str
    patient_display_identifier: str
    patient_name: str | None
    created_at: str


class WorkspaceSummaryCounts(BaseModel):
    active_sessions_count: int
    recent_encounters_count: int
    total_patients: int


class ProviderWorkspaceResponse(BaseModel):
    active_sessions: list[ActiveTreatmentSessionItem]
    recent_encounters: list[RecentEncounterItem]
    pending_access: list[dict[str, Any]] = Field(default_factory=list)
    summary_counts: WorkspaceSummaryCounts


@router.get(
    "/workspace",
    response_model=ProviderWorkspaceResponse,
    status_code=status.HTTP_200_OK,
)
async def get_provider_workspace(
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
) -> ProviderWorkspaceResponse:
    """Return the authenticated provider's clinical workspace data.

    Derived strictly from the authenticated provider and hospital context:
    - Active Treatment Sessions for this provider + facility
    - Recent Clinical Encounters for this provider + facility
    - Resolved safe patient display descriptors (no sensitive PII)
    """
    provider_id: uuid.UUID = provider.provider.provider_id
    hospital_id: uuid.UUID = provider.hospital.hospital_id

    # 1. Query Active Treatment Sessions (indexed, bounded to 20)
    sessions_stmt = (
        select(ClinicalAccessSessionRecord)
        .where(
            ClinicalAccessSessionRecord.provider_id == provider_id,
            ClinicalAccessSessionRecord.hospital_id == hospital_id,
            ClinicalAccessSessionRecord.status == "ACTIVE",
            ClinicalAccessSessionRecord.revoked_at.is_(None),
            ClinicalAccessSessionRecord.expires_at > func.now(),
        )
        .order_by(
            ClinicalAccessSessionRecord.expires_at.asc(),
            ClinicalAccessSessionRecord.session_id.asc(),
        )
        .limit(20)
    )
    sessions_res = await db.execute(sessions_stmt)
    sessions = sessions_res.scalars().all()

    # 2. Query Recent Encounters (bounded to 10)
    encounters_stmt = (
        select(ClinicalEncounter)
        .where(
            ClinicalEncounter.provider_id == provider_id,
            ClinicalEncounter.hospital_id == hospital_id,
        )
        .order_by(
            ClinicalEncounter.created_at.desc(),
            ClinicalEncounter.encounter_id.desc(),
        )
        .limit(10)
    )
    encounters_res = await db.execute(encounters_stmt)
    encounters = encounters_res.scalars().all()

    # 3. Batch resolve patient display descriptors to avoid N+1
    referenced_patient_ids = {s.patient_id for s in sessions} | {
        e.patient_id for e in encounters
    }

    patient_map: dict[uuid.UUID, Patient] = {}
    name_map: dict[uuid.UUID, str] = {}

    if referenced_patient_ids:
        patient_stmt = select(Patient).where(
            Patient.patient_uuid.in_(referenced_patient_ids)
        )
        patient_res = await db.execute(patient_stmt)
        for p in patient_res.scalars().all():
            patient_map[p.patient_uuid] = p

        profile_stmt = select(PatientProfile).where(
            PatientProfile.patient_id.in_(referenced_patient_ids)
        )
        profile_res = await db.execute(profile_stmt)
        profile_rows = profile_res.scalars().all()

        if profile_rows:
            try:
                kms = get_encryption_provider()
                for prof in profile_rows:
                    try:
                        decrypted = await kms.decrypt_field(
                            str(prof.patient_id),
                            "full_name",
                            EncryptedField.deserialize(
                                prof.full_name_encrypted, "full_name"
                            ),
                            db,
                        )
                        if decrypted and decrypted.strip():
                            name_map[prof.patient_id] = decrypted.strip()
                    except Exception as dec_exc:
                        logger.warning(
                            "Unable to decrypt patient profile display name: %s",
                            type(dec_exc).__name__,
                        )
            except Exception as kms_exc:
                logger.warning(
                    "Encryption provider unavailable for workspace patient names: %s",
                    type(kms_exc).__name__,
                )

    # 4. Summary counts
    total_pts_stmt = select(func.count(distinct(ConsentGrantLog.patient_id))).where(
        ConsentGrantLog.hospital_id == hospital_id
    )
    total_pts_res = await db.scalar(total_pts_stmt)
    total_patients_count = int(total_pts_res or 0)

    # 5. Build responses with strict redaction of secrets/tokens
    active_session_items: list[ActiveTreatmentSessionItem] = []
    for s in sessions:
        pid = s.patient_id
        patient_row = patient_map.get(pid)
        display_id = (
            patient_row.public_patient_id
            if patient_row
            else f"NC-{str(pid)[:8].upper()}"
        )
        active_session_items.append(
            ActiveTreatmentSessionItem(
                session_id=str(s.session_id),
                encounter_id=str(s.encounter_id) if s.encounter_id else None,
                patient_id=str(s.patient_id),
                patient_display_identifier=display_id,
                patient_name=name_map.get(pid),
                status=s.status,
                purpose=s.purpose,
                scope=s.scope,
                allowed_operations=list(s.allowed_operations),
                issued_at=s.issued_at.isoformat(),
                expires_at=s.expires_at.isoformat(),
            )
        )

    recent_encounter_items: list[RecentEncounterItem] = []
    for e in encounters:
        pid = e.patient_id
        patient_row = patient_map.get(pid)
        display_id = (
            patient_row.public_patient_id
            if patient_row
            else f"NC-{str(pid)[:8].upper()}"
        )
        recent_encounter_items.append(
            RecentEncounterItem(
                encounter_id=str(e.encounter_id),
                clinical_session_id=str(e.clinical_session_id),
                patient_id=str(e.patient_id),
                patient_display_identifier=display_id,
                patient_name=name_map.get(pid),
                created_at=e.created_at.isoformat(),
            )
        )

    return ProviderWorkspaceResponse(
        active_sessions=active_session_items,
        recent_encounters=recent_encounter_items,
        pending_access=[],
        summary_counts=WorkspaceSummaryCounts(
            active_sessions_count=len(active_session_items),
            recent_encounters_count=len(recent_encounter_items),
            total_patients=total_patients_count,
        ),
    )

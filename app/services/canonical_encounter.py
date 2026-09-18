"""Canonical Encounter materialization for Treatment Session V1.

The service accepts only a server-validated TreatmentSessionV1Authority. It
reuses the server-generated encounter correlation reserved on the durable
ClinicalAccessSession and never accepts caller-selected patient/provider/
hospital/encounter authority.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clinical_session_gate import (
    TreatmentSessionV1Authority,
    TreatmentSessionV1GateDenied,
    TreatmentSessionV1GateUnavailable,
    stage_server_encounter_binding,
)
from app.models.clinical_encounter import ClinicalEncounter
from app.security.clinical_access_policy import ClinicalAccessOperation


def _matches_authority(
    row: ClinicalEncounter,
    *,
    encounter_id: uuid.UUID,
    authority: TreatmentSessionV1Authority,
) -> bool:
    return (
        row.encounter_id == encounter_id
        and row.clinical_session_id == authority.session_id
        and row.patient_id == authority.patient_id
        and row.provider_id == authority.provider_id
        and row.hospital_id == authority.hospital_id
    )


async def stage_canonical_encounter(
    *,
    db: AsyncSession,
    authority: TreatmentSessionV1Authority,
) -> tuple[ClinicalEncounter, bool]:
    """Stage one canonical Encounter in the caller-owned transaction.

    The ClinicalAccessSession and ConsentGrant rows remain locked by the
    encounter-reservation function until the caller commits or rolls back.
    Returning created=False is the idempotent retry path.
    """

    if authority.required_operation is not ClinicalAccessOperation.CREATE_ENCOUNTER:
        raise TreatmentSessionV1GateDenied("TREATMENT_ENCOUNTER_OPERATION_REQUIRED")

    encounter_id = await stage_server_encounter_binding(
        db=db,
        authority=authority,
    )

    try:
        existing = (
            await db.execute(
                select(ClinicalEncounter)
                .where(ClinicalEncounter.clinical_session_id == authority.session_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
    except Exception as exc:
        raise TreatmentSessionV1GateUnavailable(
            "TREATMENT_ENCOUNTER_DURABLE_STORE_UNAVAILABLE"
        ) from exc

    if existing is not None:
        if not _matches_authority(
            existing,
            encounter_id=encounter_id,
            authority=authority,
        ):
            raise TreatmentSessionV1GateUnavailable(
                "TREATMENT_ENCOUNTER_INTEGRITY_FAILURE"
            )
        return existing, False

    encounter = ClinicalEncounter(
        encounter_id=encounter_id,
        clinical_session_id=authority.session_id,
        patient_id=authority.patient_id,
        provider_id=authority.provider_id,
        hospital_id=authority.hospital_id,
    )
    db.add(encounter)
    try:
        await db.flush()
    except Exception as exc:
        raise TreatmentSessionV1GateUnavailable(
            "TREATMENT_ENCOUNTER_DURABLE_STORE_UNAVAILABLE"
        ) from exc
    return encounter, True

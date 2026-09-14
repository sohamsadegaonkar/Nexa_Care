"""Authoritative lifecycle adapter from verified patient phone proof to search authority.

Only a freshly provider-verified Supabase phone may call this boundary. The
adapter never accepts a client-selected patient mapping and never persists or
returns the phone value. A collision is quarantined rather than reassigned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_auth_identity import PatientAuthIdentity
from app.services.patient_search_identifier_service import (
    PatientSearchIdentifierConflict,
    PatientSearchIdentifierUnavailable,
    quarantine_verified_phone_conflict,
    synchronize_verified_phone_identifier,
)

SUPABASE_PROVIDER = "supabase"


@dataclass(frozen=True, slots=True)
class VerifiedPhoneAuthorityResult:
    disposition: Literal["BOUND", "CONFLICT_QUARANTINED"]
    patient_id: UUID
    identity_id: UUID


async def reconcile_verified_supabase_phone_authority(
    db: AsyncSession,
    *,
    provider_subject: str,
    expected_patient_id: UUID,
    verified_phone: str,
) -> VerifiedPhoneAuthorityResult:
    """Reconcile one fresh Supabase phone proof with durable search authority.

    The caller owns the outer transaction. An unavailable keyring, stale source
    graph, audit failure, or incomplete conflict quarantine propagates as a
    failure so the caller can withhold the enclosing authentication transition.
    A genuine cross-patient phone collision is different: every implicated
    active search row is revoked atomically and the patient authentication
    identity is left unchanged.
    """

    subject = provider_subject.strip()
    if not subject or len(subject) > 255:
        raise PatientSearchIdentifierUnavailable()

    identity = await db.scalar(
        select(PatientAuthIdentity)
        .where(
            PatientAuthIdentity.provider == SUPABASE_PROVIDER,
            PatientAuthIdentity.provider_subject == subject,
            PatientAuthIdentity.patient_id == expected_patient_id,
            PatientAuthIdentity.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if identity is None:
        raise PatientSearchIdentifierUnavailable()

    try:
        await synchronize_verified_phone_identifier(
            db,
            patient_id=expected_patient_id,
            identity_id=identity.identity_id,
            verified_phone=verified_phone,
        )
    except PatientSearchIdentifierConflict:
        await quarantine_verified_phone_conflict(
            db,
            identity_id=identity.identity_id,
            verified_phone=verified_phone,
        )
        return VerifiedPhoneAuthorityResult(
            disposition="CONFLICT_QUARANTINED",
            patient_id=expected_patient_id,
            identity_id=identity.identity_id,
        )

    return VerifiedPhoneAuthorityResult(
        disposition="BOUND",
        patient_id=expected_patient_id,
        identity_id=identity.identity_id,
    )

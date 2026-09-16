"""Patient-controlled phone discoverability authority.

Phone-based discovery is deliberately opt-in.  Patient authentication and a
fresh provider-verified Supabase phone prove who may create the searchable
binding; the searchable binding itself never stores or returns the phone.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_search_identifier import PatientSearchIdentifier
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event
from app.services.patient_search_identifier_service import (
    IDENTIFIER_PHONE,
    PatientSearchIdentifierUnavailable,
    revoke_active_identifiers_for_identity,
)
from app.services.patient_verified_phone_authority import (
    reconcile_verified_supabase_phone_authority,
)


class PatientPhoneDiscoverabilityError(RuntimeError):
    """Stable, value-free patient discoverability failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PhoneDiscoverabilityState:
    enabled: bool


async def _active_identity(
    db: AsyncSession, *, patient_id: UUID, provider_subject: str, lock: bool = False
) -> PatientAuthIdentity:
    statement = select(PatientAuthIdentity).where(
        PatientAuthIdentity.patient_id == patient_id,
        PatientAuthIdentity.provider == "supabase",
        PatientAuthIdentity.provider_subject == provider_subject,
        PatientAuthIdentity.revoked_at.is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    identity = await db.scalar(statement)
    if identity is None:
        raise PatientPhoneDiscoverabilityError("PHONE_DISCOVERABILITY_IDENTITY_INVALID")
    return identity


async def get_phone_discoverability_state(
    db: AsyncSession, *, patient_id: UUID, provider_subject: str
) -> PhoneDiscoverabilityState:
    identity = await _active_identity(
        db, patient_id=patient_id, provider_subject=provider_subject
    )
    active = await db.scalar(
        select(PatientSearchIdentifier.identifier_id)
        .where(
            PatientSearchIdentifier.patient_id == patient_id,
            PatientSearchIdentifier.identity_id == identity.identity_id,
            PatientSearchIdentifier.identifier_type == IDENTIFIER_PHONE,
            PatientSearchIdentifier.revoked_at.is_(None),
        )
        .limit(1)
    )
    return PhoneDiscoverabilityState(enabled=active is not None)


async def enable_phone_discoverability(
    db: AsyncSession,
    *,
    patient_id: UUID,
    provider_subject: str,
    verified_phone: str,
) -> PhoneDiscoverabilityState:
    """Bind one freshly verified phone or quarantine a collision.

    The caller owns the transaction.  A collision is committed only as a
    quarantine; callers receive a generic conflict after that commit and no
    patient mapping is chosen.
    """

    try:
        result = await reconcile_verified_supabase_phone_authority(
            db,
            provider_subject=provider_subject,
            expected_patient_id=patient_id,
            verified_phone=verified_phone,
        )
    except PatientSearchIdentifierUnavailable as exc:
        raise PatientPhoneDiscoverabilityError(
            "PHONE_DISCOVERABILITY_UNAVAILABLE"
        ) from exc
    if result.disposition == "CONFLICT_QUARANTINED":
        raise PatientPhoneDiscoverabilityError("PHONE_DISCOVERABILITY_CONFLICT")
    return PhoneDiscoverabilityState(enabled=True)


async def disable_phone_discoverability(
    db: AsyncSession, *, patient_id: UUID, provider_subject: str
) -> PhoneDiscoverabilityState:
    """Idempotently revoke the patient's active phone-search authority."""

    identity = await _active_identity(
        db, patient_id=patient_id, provider_subject=provider_subject, lock=True
    )
    try:
        revoked = await revoke_active_identifiers_for_identity(
            db,
            identity_id=identity.identity_id,
            reason="ADMINISTRATIVE",
        )
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.AUTH),
            idempotency_key=f"patient-phone-discoverability:disable:{uuid.uuid4()}",
            actor_id=str(patient_id),
            event_type="PATIENT_PHONE_DISCOVERABILITY_DISABLED",
            target_id=str(identity.identity_id),
            patient_id=str(patient_id),
            status="SUCCESS",
            metadata={"identifier_type": IDENTIFIER_PHONE, "revoked_count": revoked},
        )
    except Exception as exc:
        raise PatientPhoneDiscoverabilityError(
            "PHONE_DISCOVERABILITY_UNAVAILABLE"
        ) from exc
    return PhoneDiscoverabilityState(enabled=False)

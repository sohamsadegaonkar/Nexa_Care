"""Transactional first-time patient-account registration.

This module deliberately creates only the durable account graph needed for
phone-OTP authentication.  It neither stores profile PII nor changes the
existing login flow.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.services.patient_discovery_service import generate_public_patient_id
from app.models.patient_records import PatientRecord
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event


_SUPABASE_PROVIDER = "supabase"


class PatientRegistrationError(RuntimeError):
    """A stable, non-sensitive registration-finalization failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PatientRegistrationAccount:
    patient_id: str
    created: bool
    provider_subject: str


def _registration_lock_key(provider_subject: str) -> int:
    """Return a stable signed 64-bit advisory-lock key without persisting PII."""
    digest = hashlib.sha256(
        f"{_SUPABASE_PROVIDER}:{provider_subject}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _is_postgresql(db: AsyncSession) -> bool:
    bind = db.get_bind()
    return getattr(getattr(bind, "dialect", None), "name", None) == "postgresql"


async def _find_identity(
    db: AsyncSession, provider_subject: str
) -> PatientAuthIdentity | None:
    return await db.scalar(
        select(PatientAuthIdentity).where(
            PatientAuthIdentity.provider == _SUPABASE_PROVIDER,
            PatientAuthIdentity.provider_subject == provider_subject,
        )
    )


async def _existing_account_or_error(
    db: AsyncSession, identity: PatientAuthIdentity
) -> PatientRegistrationAccount:
    if identity.revoked_at is not None:
        raise PatientRegistrationError("REGISTRATION_IDENTITY_UNAVAILABLE")

    patient = await db.scalar(
        select(Patient).where(
            Patient.patient_uuid == identity.patient_id,
            Patient.is_deleted.is_(False),
        )
    )
    if patient is None:
        raise PatientRegistrationError("REGISTRATION_IDENTITY_UNAVAILABLE")

    record = await db.scalar(
        select(PatientRecord).where(PatientRecord.patient_id == patient.patient_uuid)
    )
    if record is None:
        raise PatientRegistrationError("REGISTRATION_IDENTITY_UNAVAILABLE")
    return PatientRegistrationAccount(
        patient_id=str(patient.patient_uuid),
        created=False,
        provider_subject=identity.provider_subject,
    )


def registration_audit_idempotency_key(attempt_id: str) -> str:
    """Return a non-secret durable proof for one bounded attempt only."""
    return (
        "patient-registration:" + hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()
    )


async def _attempt_has_success_audit(
    db: AsyncSession, *, attempt_id: str, patient_id: str
) -> bool:
    """Check the transactionally durable proof used only for same-attempt recovery."""
    row = await db.scalar(
        text(
            """SELECT patient_id FROM public.audit_outbox
               WHERE idempotency_key = :idempotency_key
                 AND event_type = 'PATIENT_REGISTRATION_SUCCESS'
                 AND patient_id = :patient_id
               LIMIT 1"""
        ),
        {
            "idempotency_key": registration_audit_idempotency_key(attempt_id),
            "patient_id": str(patient_id),
        },
    )
    return row is not None


async def recover_patient_registration_for_attempt(
    db: AsyncSession, *, attempt_id: str, patient_id: str | None = None
) -> PatientRegistrationAccount | None:
    """Recover only a graph durably proven to belong to this same attempt.

    This handles the narrow crash window after the database commit but before
    Redis receives ``finalized``.  It never uses a provider subject supplied by
    the client and it cannot adopt an account created by another attempt.
    """
    durable_patient_id = patient_id or await db.scalar(
        text(
            """SELECT patient_id FROM public.audit_outbox
               WHERE idempotency_key = :idempotency_key
                 AND event_type = 'PATIENT_REGISTRATION_SUCCESS'
               LIMIT 1"""
        ),
        {"idempotency_key": registration_audit_idempotency_key(attempt_id)},
    )
    if durable_patient_id is None:
        return None
    identity = await db.scalar(
        select(PatientAuthIdentity).where(
            PatientAuthIdentity.patient_id == durable_patient_id,
            PatientAuthIdentity.provider == _SUPABASE_PROVIDER,
        )
    )
    if identity is None:
        raise PatientRegistrationError("REGISTRATION_IDENTITY_UNAVAILABLE")
    account = await _existing_account_or_error(db, identity)
    if not await _attempt_has_success_audit(
        db, attempt_id=attempt_id, patient_id=account.patient_id
    ):
        raise PatientRegistrationError("REGISTRATION_IDENTITY_UNAVAILABLE")
    return account


async def finalize_patient_registration(
    db: AsyncSession, *, provider_subject: str, attempt_id: str
) -> PatientRegistrationAccount:
    """Create or recover one complete patient account for a verified subject.

    PostgreSQL obtains a transaction-scoped lock before inspecting or creating
    the graph.  The database unique constraint remains the authoritative
    collision backstop for writers that do not use this service.
    """
    subject = provider_subject.strip()
    if not subject or len(subject) > 255:
        raise PatientRegistrationError("REGISTRATION_PROVIDER_RESPONSE_INVALID")

    async with db.begin():
        if _is_postgresql(db):
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": _registration_lock_key(subject)},
            )

        existing = await _find_identity(db, subject)
        if existing is not None:
            account = await _existing_account_or_error(db, existing)
            if await _attempt_has_success_audit(
                db, attempt_id=attempt_id, patient_id=account.patient_id
            ):
                return account
            raise PatientRegistrationError("ACCOUNT_ALREADY_REGISTERED")

        try:
            # A savepoint lets a concurrent non-cooperating writer's unique
            # constraint collision be recovered without leaving even an
            # orphan Patient row in the outer transaction.
            async with db.begin_nested():
                patient = Patient(
                    is_deleted=False, public_patient_id=generate_public_patient_id()
                )
                db.add(patient)
                await db.flush()
                identity = PatientAuthIdentity(
                    patient_id=patient.patient_uuid,
                    provider=_SUPABASE_PROVIDER,
                    provider_subject=subject,
                )
                record = PatientRecord(patient_id=patient.patient_uuid)
                db.add_all([identity, record])
                await db.flush()
        except IntegrityError:
            concurrent = await _find_identity(db, subject)
            if concurrent is not None:
                account = await _existing_account_or_error(db, concurrent)
                if await _attempt_has_success_audit(
                    db, attempt_id=attempt_id, patient_id=account.patient_id
                ):
                    return account
                raise PatientRegistrationError("ACCOUNT_ALREADY_REGISTERED")
            raise PatientRegistrationError(
                "REGISTRATION_FINALIZATION_RETRYABLE"
            ) from None

        account = PatientRegistrationAccount(
            patient_id=str(patient.patient_uuid), created=True, provider_subject=subject
        )
        # This insert is deliberately part of the surrounding account-graph
        # transaction.  An outbox write failure aborts Patient, identity, and
        # record together; later asynchronous ledger delivery cannot undo them.
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.AUTH),
            idempotency_key=registration_audit_idempotency_key(attempt_id),
            actor_id="PATIENT_REGISTRATION",
            event_type="PATIENT_REGISTRATION_SUCCESS",
            target_id=account.patient_id,
            patient_id=account.patient_id,
            metadata={"provider": _SUPABASE_PROVIDER},
        )
        return account

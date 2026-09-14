"""Privacy-preserving exact-match patient-search identifier authority.

This module is intentionally not wired to the public discovery route yet.
Slice 10A first qualifies the durable identifier/index lifecycle before any
low-entropy lookup is exposed to provider clients.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.patient_discovery_index_config import (
    PatientDiscoveryIndexConfigError,
    get_patient_discovery_index_config,
)
from app.models.patient import Patient
from app.models.patient_auth_identity import PatientAuthIdentity
from app.models.patient_search_identifier import PatientSearchIdentifier
from app.services.patient_auth_service import normalize_indian_phone
from app.services.patient_discovery_service import (
    DiscoveryNoMatch,
    DiscoveryUnavailable,
    PatientDiscoveryService,
)

IDENTIFIER_PHONE = "PHONE"
PHONE_NORMALIZATION_VERSION = 1
_INDEX_DOMAIN = "nexa-care:patient-discovery-index:v1"


class PatientSearchIdentifierError(RuntimeError):
    """Stable, non-sensitive search-identifier authority failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PatientSearchIdentifierNoMatch(PatientSearchIdentifierError):
    def __init__(self) -> None:
        super().__init__("PATIENT_SEARCH_IDENTIFIER_NO_MATCH")


class PatientSearchIdentifierConflict(PatientSearchIdentifierError):
    def __init__(self) -> None:
        super().__init__("PATIENT_SEARCH_IDENTIFIER_CONFLICT")


class PatientSearchIdentifierUnavailable(PatientSearchIdentifierError):
    def __init__(self) -> None:
        super().__init__("PATIENT_SEARCH_IDENTIFIER_UNAVAILABLE")


def _keyring() -> tuple[int, dict[int, str]]:
    try:
        config = get_patient_discovery_index_config()
    except PatientDiscoveryIndexConfigError as exc:
        raise PatientSearchIdentifierUnavailable() from exc
    return config.active_key_version, dict(config.hmac_keys)


def _phone_fingerprints(normalized_phone: str) -> tuple[int, dict[int, str]]:
    """Return versioned HMAC fingerprints without persisting or logging PII."""

    active_version, keyring = _keyring()
    message = (
        f"{_INDEX_DOMAIN}:{IDENTIFIER_PHONE}:"
        f"{PHONE_NORMALIZATION_VERSION}:{normalized_phone}"
    ).encode("utf-8")
    fingerprints = {
        version: hmac.new(
            secret.encode("utf-8"), message, hashlib.sha256
        ).hexdigest()
        for version, secret in keyring.items()
    }
    return active_version, fingerprints


def phone_index_fingerprints(phone: str) -> tuple[int, dict[int, str]]:
    """Normalize a phone and derive only its non-reversible keyed indexes."""

    try:
        normalized = normalize_indian_phone(phone)
    except ValueError as exc:
        raise PatientSearchIdentifierError("PATIENT_SEARCH_IDENTIFIER_INVALID") from exc
    return _phone_fingerprints(normalized)


def _candidate_predicate(fingerprints: dict[int, str]):
    return or_(
        *(
            and_(
                PatientSearchIdentifier.key_version == version,
                PatientSearchIdentifier.value_hmac == fingerprint,
            )
            for version, fingerprint in fingerprints.items()
        )
    )


async def _assert_live_source_authority(
    db: AsyncSession, *, patient_id: UUID, identity_id: UUID
) -> None:
    """Require one active Supabase identity bound to one active canonical patient."""

    identity = await db.scalar(
        select(PatientAuthIdentity)
        .where(
            PatientAuthIdentity.identity_id == identity_id,
            PatientAuthIdentity.patient_id == patient_id,
            PatientAuthIdentity.provider == "supabase",
            PatientAuthIdentity.revoked_at.is_(None),
        )
        .with_for_update()
    )
    if identity is None:
        raise PatientSearchIdentifierUnavailable()

    try:
        patient, redirected = await PatientDiscoveryService(
            db, redis=None
        ).resolve_patient_id(patient_id)
    except (DiscoveryNoMatch, DiscoveryUnavailable) as exc:
        raise PatientSearchIdentifierUnavailable() from exc
    if redirected or patient.patient_uuid != patient_id:
        # Do not silently bind searchable PII to a stale pre-merge identity.
        raise PatientSearchIdentifierUnavailable()


async def synchronize_verified_phone_identifier(
    db: AsyncSession,
    *,
    patient_id: UUID,
    identity_id: UUID,
    verified_phone: str,
    verified_at: datetime | None = None,
) -> PatientSearchIdentifier:
    """Synchronize one server-verified phone into the exact-match authority.

    The caller must supply a phone that has already been verified by the
    authoritative patient authentication flow.  This function never accepts a
    client-provided patient mapping and never stores the raw/normalized phone.

    The caller owns the outer transaction.  This function uses a savepoint so
    uniqueness races fail closed without leaving a partially superseded row.
    """

    try:
        normalized = normalize_indian_phone(verified_phone)
    except ValueError as exc:
        raise PatientSearchIdentifierError("PATIENT_SEARCH_IDENTIFIER_INVALID") from exc

    await _assert_live_source_authority(
        db, patient_id=patient_id, identity_id=identity_id
    )
    active_version, fingerprints = _phone_fingerprints(normalized)
    active_fingerprint = fingerprints[active_version]
    now = verified_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise PatientSearchIdentifierError("PATIENT_SEARCH_IDENTIFIER_INVALID")

    existing = await db.scalar(
        select(PatientSearchIdentifier)
        .where(
            PatientSearchIdentifier.patient_id == patient_id,
            PatientSearchIdentifier.identifier_type == IDENTIFIER_PHONE,
            PatientSearchIdentifier.revoked_at.is_(None),
        )
        .with_for_update()
    )

    matching_rows = list(
        (
            await db.scalars(
                select(PatientSearchIdentifier)
                .where(
                    PatientSearchIdentifier.identifier_type == IDENTIFIER_PHONE,
                    PatientSearchIdentifier.normalization_version
                    == PHONE_NORMALIZATION_VERSION,
                    PatientSearchIdentifier.revoked_at.is_(None),
                    _candidate_predicate(fingerprints),
                )
                .with_for_update()
            )
        ).all()
    )
    if any(row.patient_id != patient_id for row in matching_rows):
        # A verified phone cannot silently move between patient identities.
        raise PatientSearchIdentifierConflict()

    if (
        existing is not None
        and existing.identity_id == identity_id
        and existing.normalization_version == PHONE_NORMALIZATION_VERSION
        and existing.key_version == active_version
        and hmac.compare_digest(existing.value_hmac, active_fingerprint)
    ):
        if now > existing.verified_at:
            existing.verified_at = now
            await db.flush()
        return existing

    try:
        async with db.begin_nested():
            if existing is not None:
                existing.revoked_at = now
                existing.revocation_reason = "SUPERSEDED"
            replacement = PatientSearchIdentifier(
                patient_id=patient_id,
                identity_id=identity_id,
                identifier_type=IDENTIFIER_PHONE,
                normalization_version=PHONE_NORMALIZATION_VERSION,
                key_version=active_version,
                value_hmac=active_fingerprint,
                verified_at=now,
            )
            db.add(replacement)
            await db.flush()
    except IntegrityError as exc:
        # Covers concurrent claims of the same phone and concurrent active-row
        # creation for one patient.  Do not expose which invariant collided.
        raise PatientSearchIdentifierConflict() from exc
    return replacement


async def revoke_active_identifiers_for_identity(
    db: AsyncSession,
    *,
    identity_id: UUID,
    reason: str = "IDENTITY_REVOKED",
    revoked_at: datetime | None = None,
) -> int:
    """Revoke search authority when its authentication source is revoked."""

    if reason not in {"IDENTITY_REVOKED", "PATIENT_ERASED", "ADMINISTRATIVE"}:
        raise PatientSearchIdentifierError("PATIENT_SEARCH_IDENTIFIER_INVALID")
    now = revoked_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise PatientSearchIdentifierError("PATIENT_SEARCH_IDENTIFIER_INVALID")

    rows = list(
        (
            await db.scalars(
                select(PatientSearchIdentifier)
                .where(
                    PatientSearchIdentifier.identity_id == identity_id,
                    PatientSearchIdentifier.revoked_at.is_(None),
                )
                .with_for_update()
            )
        ).all()
    )
    for row in rows:
        row.revoked_at = now
        row.revocation_reason = reason
    if rows:
        await db.flush()
    return len(rows)


async def resolve_verified_phone_patient(
    db: AsyncSession, *, phone: str
) -> tuple[Patient, bool]:
    """Resolve an indexed verified phone internally; not a public API contract.

    This function is deliberately separate from ``POST /patient-discovery``.
    Route-level anti-enumeration policy must be qualified before it can be
    exposed.  A matched index still passes through canonical merge, deletion,
    and erasure checks before a patient object is returned.
    """

    try:
        normalized = normalize_indian_phone(phone)
    except ValueError as exc:
        raise PatientSearchIdentifierNoMatch() from exc
    _, fingerprints = _phone_fingerprints(normalized)
    rows = list(
        (
            await db.scalars(
                select(PatientSearchIdentifier).where(
                    PatientSearchIdentifier.identifier_type == IDENTIFIER_PHONE,
                    PatientSearchIdentifier.normalization_version
                    == PHONE_NORMALIZATION_VERSION,
                    PatientSearchIdentifier.revoked_at.is_(None),
                    _candidate_predicate(fingerprints),
                )
            )
        ).all()
    )
    if not rows:
        raise PatientSearchIdentifierNoMatch()

    patient_ids = {row.patient_id for row in rows}
    if len(patient_ids) != 1:
        # Cross-version ambiguity is an integrity failure, never a ranking task.
        raise PatientSearchIdentifierUnavailable()
    patient_id = next(iter(patient_ids))

    valid_sources = list(
        (
            await db.scalars(
                select(PatientAuthIdentity).where(
                    PatientAuthIdentity.identity_id.in_([row.identity_id for row in rows]),
                    PatientAuthIdentity.patient_id == patient_id,
                    PatientAuthIdentity.provider == "supabase",
                    PatientAuthIdentity.revoked_at.is_(None),
                )
            )
        ).all()
    )
    if not valid_sources:
        raise PatientSearchIdentifierUnavailable()

    try:
        return await PatientDiscoveryService(db, redis=None).resolve_patient_id(patient_id)
    except DiscoveryNoMatch as exc:
        raise PatientSearchIdentifierNoMatch() from exc
    except DiscoveryUnavailable as exc:
        raise PatientSearchIdentifierUnavailable() from exc

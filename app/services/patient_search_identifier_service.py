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
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event
from app.services.patient_auth_service import normalize_indian_phone
from app.services.patient_discovery_service import (
    DiscoveryNoMatch,
    DiscoveryUnavailable,
    PatientDiscoveryService,
)

IDENTIFIER_PHONE = "PHONE"
PHONE_NORMALIZATION_VERSION = 1
_INDEX_DOMAIN = "nexa-care:patient-discovery-index:v1"
_AUDIT_ACTOR = "PATIENT_IDENTITY_AUTHORITY"
_ALLOWED_REVOCATION_REASONS = frozenset(
    {
        "IDENTITY_REVOKED",
        "IDENTITY_REBOUND",
        "PATIENT_ERASED",
        "AUTHORITY_CONFLICT",
        "SOURCE_REVERIFICATION_FAILED",
        "ADMINISTRATIVE",
    }
)


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


async def _assert_active_rows_covered_by_keyring(
    db: AsyncSession, *, configured_versions: set[int]
) -> None:
    """Fail closed if active rows cannot participate in collision detection.

    A low-entropy identifier key must never be retired while active rows still
    depend on it. Otherwise the same phone could be HMACed under a new key and
    appear unrelated to an old active row. Normalization-version drift has the
    same ambiguity risk. The operator must retain old key material until those
    rows have been reverified/reindexed through an authoritative source event.
    """

    stale = await db.scalar(
        select(PatientSearchIdentifier.identifier_id)
        .where(
            PatientSearchIdentifier.identifier_type == IDENTIFIER_PHONE,
            PatientSearchIdentifier.revoked_at.is_(None),
            or_(
                PatientSearchIdentifier.normalization_version
                != PHONE_NORMALIZATION_VERSION,
                PatientSearchIdentifier.key_version.not_in(configured_versions),
            ),
        )
        .limit(1)
    )
    if stale is not None:
        raise PatientSearchIdentifierUnavailable()


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


async def _audit_bound(
    db: AsyncSession,
    *,
    row: PatientSearchIdentifier,
    replaced_identifier_id: UUID | None,
) -> None:
    await enqueue_audit_event(
        db,
        audit_context=current_audit_context(AuditDomain.AUTH),
        idempotency_key=f"patient-search-id:{row.identifier_id}:bound",
        actor_id=_AUDIT_ACTOR,
        event_type="PATIENT_SEARCH_IDENTIFIER_BOUND",
        target_id=str(row.identifier_id),
        patient_id=str(row.patient_id),
        metadata={
            "identifier_type": row.identifier_type,
            "normalization_version": row.normalization_version,
            "key_version": row.key_version,
            "replaced_existing": replaced_identifier_id is not None,
        },
    )


async def _audit_superseded(
    db: AsyncSession,
    *,
    previous: PatientSearchIdentifier,
    replacement: PatientSearchIdentifier,
) -> None:
    await enqueue_audit_event(
        db,
        audit_context=current_audit_context(AuditDomain.AUTH),
        idempotency_key=(
            f"patient-search-id:{previous.identifier_id}:"
            f"superseded:{replacement.identifier_id}"
        ),
        actor_id=_AUDIT_ACTOR,
        event_type="PATIENT_SEARCH_IDENTIFIER_SUPERSEDED",
        target_id=str(previous.identifier_id),
        patient_id=str(previous.patient_id),
        metadata={
            "identifier_type": previous.identifier_type,
            "replacement_identifier_id": str(replacement.identifier_id),
        },
    )


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
    authoritative patient authentication flow. This function never accepts a
    client-provided patient mapping and never stores the raw/normalized phone.

    The caller owns the outer transaction. A savepoint couples any supersession,
    replacement, and durable audit-outbox rows so audit failure cannot leave a
    partially mutated search authority.
    """

    try:
        normalized = normalize_indian_phone(verified_phone)
    except ValueError as exc:
        raise PatientSearchIdentifierError("PATIENT_SEARCH_IDENTIFIER_INVALID") from exc

    await _assert_live_source_authority(
        db, patient_id=patient_id, identity_id=identity_id
    )
    active_version, fingerprints = _phone_fingerprints(normalized)
    await _assert_active_rows_covered_by_keyring(
        db, configured_versions=set(fingerprints)
    )
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
        # Exact replay is intentionally mutation-free. ``verified_at`` records
        # the authoritative event that created the current binding rather than
        # becoming an unaudited freshness field.
        return existing

    try:
        async with db.begin_nested():
            replaced_identifier_id = existing.identifier_id if existing else None
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
            if existing is not None:
                await _audit_superseded(
                    db, previous=existing, replacement=replacement
                )
            await _audit_bound(
                db,
                row=replacement,
                replaced_identifier_id=replaced_identifier_id,
            )
    except IntegrityError as exc:
        # Covers concurrent claims of the same phone and concurrent active-row
        # creation for one patient. Do not expose which invariant collided.
        raise PatientSearchIdentifierConflict() from exc
    return replacement


async def _revoke_rows(
    db: AsyncSession,
    *,
    rows: list[PatientSearchIdentifier],
    reason: str,
    revoked_at: datetime,
) -> int:
    for row in rows:
        row.revoked_at = revoked_at
        row.revocation_reason = reason
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.AUTH),
            idempotency_key=f"patient-search-id:{row.identifier_id}:revoked:{reason}",
            actor_id=_AUDIT_ACTOR,
            event_type="PATIENT_SEARCH_IDENTIFIER_REVOKED",
            target_id=str(row.identifier_id),
            patient_id=str(row.patient_id),
            metadata={
                "identifier_type": row.identifier_type,
                "reason": reason,
            },
        )
    if rows:
        await db.flush()
    return len(rows)


async def revoke_active_identifiers_for_identity(
    db: AsyncSession,
    *,
    identity_id: UUID,
    reason: str = "IDENTITY_REVOKED",
    revoked_at: datetime | None = None,
) -> int:
    """Revoke search authority when its authentication source changes.

    Revocation and its audit-outbox evidence share the caller's transaction.
    Reasons distinguish identity revocation, canonical rebind, erasure, and
    other fail-closed authority invalidations without recording raw PII.
    """

    if reason not in _ALLOWED_REVOCATION_REASONS:
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
    return await _revoke_rows(db, rows=rows, reason=reason, revoked_at=now)


async def quarantine_verified_phone_conflict(
    db: AsyncSession,
    *,
    identity_id: UUID,
    verified_phone: str,
    revoked_at: datetime | None = None,
) -> int:
    """Disable every active authority implicated by one verified-phone conflict.

    A newly verified phone that collides with another patient must never be
    reassigned by winner selection. The current identity's old search authority
    and every active row matching the newly verified phone are revoked together.
    A later, separately authorized source event may establish a new binding.
    """

    try:
        normalized = normalize_indian_phone(verified_phone)
    except ValueError as exc:
        raise PatientSearchIdentifierError("PATIENT_SEARCH_IDENTIFIER_INVALID") from exc
    _, fingerprints = _phone_fingerprints(normalized)
    await _assert_active_rows_covered_by_keyring(
        db, configured_versions=set(fingerprints)
    )
    now = revoked_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise PatientSearchIdentifierError("PATIENT_SEARCH_IDENTIFIER_INVALID")

    rows = list(
        (
            await db.scalars(
                select(PatientSearchIdentifier)
                .where(
                    PatientSearchIdentifier.identifier_type == IDENTIFIER_PHONE,
                    PatientSearchIdentifier.revoked_at.is_(None),
                    or_(
                        PatientSearchIdentifier.identity_id == identity_id,
                        _candidate_predicate(fingerprints),
                    ),
                )
                .with_for_update()
            )
        ).all()
    )
    return await _revoke_rows(
        db,
        rows=rows,
        reason="AUTHORITY_CONFLICT",
        revoked_at=now,
    )


async def resolve_verified_phone_patient(
    db: AsyncSession, *, phone: str
) -> tuple[Patient, bool]:
    """Resolve an indexed verified phone internally; not a public API contract.

    This function is deliberately separate from ``POST /patient-discovery``.
    Route-level anti-enumeration policy must be qualified before it can be
    exposed. A matched index still passes through canonical merge, deletion,
    and erasure checks before a patient object is returned.
    """

    try:
        normalized = normalize_indian_phone(phone)
    except ValueError as exc:
        raise PatientSearchIdentifierNoMatch() from exc
    _, fingerprints = _phone_fingerprints(normalized)
    await _assert_active_rows_covered_by_keyring(
        db, configured_versions=set(fingerprints)
    )
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

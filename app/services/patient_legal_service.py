"""Patient legal document acceptance service.

Legal acceptance is a SEPARATE domain from clinical consent (ConsentEngine).
Do not conflate these concepts.

Transaction ownership: this service stages mutations on the provided
AsyncSession but NEVER commits.  The caller owns BEGIN/COMMIT/ROLLBACK.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ConfigError, get_patient_legal_config
from app.models.patient_legal_acceptance import PatientLegalAcceptance
from app.models.patient_profile import PatientProfile
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event


class LegalAcceptanceError(RuntimeError):
    """Raised when a legal acceptance operation fails."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


ALLOWED_DOCUMENT_TYPES = frozenset({"TERMS_OF_SERVICE", "PRIVACY_NOTICE"})

_AUDIT_EVENT_MAP = {
    "TERMS_OF_SERVICE": "PATIENT_TERMS_ACCEPTED",
    "PRIVACY_NOTICE": "PATIENT_PRIVACY_NOTICE_ACKNOWLEDGED",
}


def _legal_audit_idempotency_key(
    *,
    patient_id: str,
    document_type: str,
    document_version: str,
    document_sha256: str,
) -> str:
    """Return a bounded, non-secret durable key for one legal acceptance."""
    canonical = json.dumps(
        {
            "document_sha256": document_sha256,
            "document_type": document_type,
            "document_version": document_version,
            "operation": "patient_legal_acceptance",
            "patient_id": patient_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "legal:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LegalRequirement:
    """Server-authoritative legal document requirement."""

    document_type: str
    document_version: str
    document_sha256: str
    document_url: str
    accepted_current_version: bool


@dataclass(frozen=True)
class OnboardingStatus:
    """Server-derived patient onboarding status."""

    profile_complete: bool
    terms_current: bool
    privacy_current: bool
    complete: bool
    next_step: str  # PROFILE | LEGAL_ACCEPTANCE | COMPLETE


def _resolve_document_config(document_type: str) -> tuple[str, str, str]:
    """Resolve server-owned version, sha256, and URL for a document type.

    Raises LegalAcceptanceError if legal configuration is invalid/missing.
    """
    try:
        config = get_patient_legal_config()
    except ConfigError as exc:
        raise LegalAcceptanceError(
            "LEGAL_CONFIG_UNAVAILABLE",
            "Legal document configuration is unavailable.",
        ) from exc

    if document_type == "TERMS_OF_SERVICE":
        return config.terms_version, config.terms_sha256, config.terms_url
    elif document_type == "PRIVACY_NOTICE":
        return config.privacy_version, config.privacy_sha256, config.privacy_url
    else:
        raise LegalAcceptanceError(
            "UNSUPPORTED_DOCUMENT_TYPE",
            f"Unsupported document type: {document_type}",
        )


async def get_legal_requirements(
    patient_id: str, db: AsyncSession
) -> list[LegalRequirement]:
    """Return server-authoritative legal requirements with acceptance status."""
    pid = uuid.UUID(patient_id)
    requirements = []

    for doc_type in sorted(ALLOWED_DOCUMENT_TYPES):
        version, sha256, url = _resolve_document_config(doc_type)
        existing = (
            await db.execute(
                select(PatientLegalAcceptance).where(
                    and_(
                        PatientLegalAcceptance.patient_id == pid,
                        PatientLegalAcceptance.document_type == doc_type,
                        PatientLegalAcceptance.document_version == version,
                        PatientLegalAcceptance.document_sha256 == sha256,
                    )
                )
            )
        ).scalar_one_or_none()
        requirements.append(
            LegalRequirement(
                document_type=doc_type,
                document_version=version,
                document_sha256=sha256,
                document_url=url,
                accepted_current_version=existing is not None,
            )
        )
    return requirements


async def accept_legal_documents(
    patient_id: str,
    document_types: list[str],
    db: AsyncSession,
) -> list[LegalRequirement]:
    """Accept one or more legal documents atomically.

    The entire request is treated as ONE transaction.  If ANY document
    fails (e.g. version/digest conflict), the caller MUST roll back and
    no new durable state from this request survives.

    Returns updated legal requirements after successful staging.
    """
    pid = uuid.UUID(patient_id)

    # Validate all document types first
    invalid = set(document_types) - ALLOWED_DOCUMENT_TYPES
    if invalid:
        raise LegalAcceptanceError(
            "UNSUPPORTED_DOCUMENT_TYPE",
            f"Unsupported document types: {', '.join(sorted(invalid))}",
        )
    if not document_types:
        raise LegalAcceptanceError(
            "NO_DOCUMENT_TYPES",
            "At least one document type must be specified.",
        )

    for doc_type in document_types:
        version, sha256, url = _resolve_document_config(doc_type)

        # Check for existing acceptance of same (patient, type, version)
        existing = (
            await db.execute(
                select(PatientLegalAcceptance).where(
                    and_(
                        PatientLegalAcceptance.patient_id == pid,
                        PatientLegalAcceptance.document_type == doc_type,
                        PatientLegalAcceptance.document_version == version,
                    )
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            # Same version exists — check digest for governance conflict
            if existing.document_sha256 != sha256:
                raise LegalAcceptanceError(
                    "LEGAL_DOCUMENT_VERSION_DIGEST_CONFLICT",
                    f"Document {doc_type} version {version} has a digest conflict. "
                    "A new document version is required.",
                )
            # Exact idempotent match — skip (no duplicate row, no duplicate audit)
            continue

        # New acceptance — INSERT
        acceptance = PatientLegalAcceptance(
            patient_id=pid,
            document_type=doc_type,
            document_version=version,
            document_sha256=sha256,
            accepted_at=datetime.now(timezone.utc),
        )
        try:
            async with db.begin_nested():
                db.add(acceptance)
                await db.flush()
        except IntegrityError:
            existing = (
                await db.execute(
                    select(PatientLegalAcceptance).where(
                        and_(
                            PatientLegalAcceptance.patient_id == pid,
                            PatientLegalAcceptance.document_type == doc_type,
                            PatientLegalAcceptance.document_version == version,
                        )
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            if existing.document_sha256 != sha256:
                raise LegalAcceptanceError(
                    "LEGAL_DOCUMENT_VERSION_DIGEST_CONFLICT",
                    f"Document {doc_type} version {version} has a digest conflict. "
                    "A new document version is required.",
                ) from None
            continue

        # Audit event for new acceptance only
        event_type = _AUDIT_EVENT_MAP[doc_type]
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.POLICY),
            idempotency_key=_legal_audit_idempotency_key(
                patient_id=patient_id,
                document_type=doc_type,
                document_version=version,
                document_sha256=sha256,
            ),
            actor_id=patient_id,
            event_type=event_type,
            target_id=patient_id,
            patient_id=patient_id,
            metadata={
                "document_type": doc_type,
                "document_version": version,
                "document_sha256": sha256,
            },
        )

    # Return current requirements after staging
    return await get_legal_requirements(patient_id, db)


async def get_onboarding_status(patient_id: str, db: AsyncSession) -> OnboardingStatus:
    """Derive server-authoritative patient onboarding status."""
    pid = uuid.UUID(patient_id)

    # Check profile existence
    profile = (
        await db.execute(select(PatientProfile).where(PatientProfile.patient_id == pid))
    ).scalar_one_or_none()
    profile_complete = (
        profile is not None
        and profile.full_name_encrypted is not None
        and profile.date_of_birth_encrypted is not None
    )

    # Check legal acceptance status
    try:
        terms_version, terms_sha256, _ = _resolve_document_config("TERMS_OF_SERVICE")
        privacy_version, privacy_sha256, _ = _resolve_document_config("PRIVACY_NOTICE")
    except LegalAcceptanceError:
        # Legal config unavailable — fail closed, never report COMPLETE
        return OnboardingStatus(
            profile_complete=profile_complete,
            terms_current=False,
            privacy_current=False,
            complete=False,
            next_step="LEGAL_ACCEPTANCE" if profile_complete else "PROFILE",
        )

    terms_accepted = (
        await db.execute(
            select(PatientLegalAcceptance).where(
                and_(
                    PatientLegalAcceptance.patient_id == pid,
                    PatientLegalAcceptance.document_type == "TERMS_OF_SERVICE",
                    PatientLegalAcceptance.document_version == terms_version,
                    PatientLegalAcceptance.document_sha256 == terms_sha256,
                )
            )
        )
    ).scalar_one_or_none()
    terms_current = terms_accepted is not None

    privacy_accepted = (
        await db.execute(
            select(PatientLegalAcceptance).where(
                and_(
                    PatientLegalAcceptance.patient_id == pid,
                    PatientLegalAcceptance.document_type == "PRIVACY_NOTICE",
                    PatientLegalAcceptance.document_version == privacy_version,
                    PatientLegalAcceptance.document_sha256 == privacy_sha256,
                )
            )
        )
    ).scalar_one_or_none()
    privacy_current = privacy_accepted is not None

    complete = profile_complete and terms_current and privacy_current

    if not profile_complete:
        next_step = "PROFILE"
    elif not (terms_current and privacy_current):
        next_step = "LEGAL_ACCEPTANCE"
    else:
        next_step = "COMPLETE"

    return OnboardingStatus(
        profile_complete=profile_complete,
        terms_current=terms_current,
        privacy_current=privacy_current,
        complete=complete,
        next_step=next_step,
    )

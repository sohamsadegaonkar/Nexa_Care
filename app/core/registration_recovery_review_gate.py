"""Fail-closed authorization for patient registration-recovery review.

This administrative authority is deliberately separate from clinical capability,
document consent, patient authentication, and device authority.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider
from app.models.patient_registration_recovery_review import (
    REGISTRATION_RECOVERY_REVIEW_AUTHORITY_VERSION,
    REGISTRATION_RECOVERY_REVIEWER_ROLE,
)
from app.models.provider import AffiliationTrustStatus, ProviderHospitalAffiliation
from app.models.provider_context import ProviderContext
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event
from app.services.provider_auth_service import resolve_provider_session_context

REGISTRATION_RECOVERY_REVIEW_MFA_MAX_AGE_SECONDS = 15 * 60


class RegistrationRecoveryReviewGateError(RuntimeError):
    """Stable, value-free recovery-review authorization failure."""

    def __init__(self, code: str, *, http_status: int = 403):
        self.code = code
        self.http_status = http_status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RegistrationRecoveryReviewer:
    reviewer_id: str
    hospital_id: str
    affiliation_id: str
    authority_version: str
    session_binding: str


def _current_session_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization:
        scheme, separator, credential = authorization.partition(" ")
        if (
            scheme.lower() != "bearer"
            or separator != " "
            or not credential
            or credential != credential.strip()
        ):
            raise RegistrationRecoveryReviewGateError(
                "REGISTRATION_RECOVERY_REVIEW_SESSION_REQUIRED", http_status=401
            )
        return credential
    cookie = request.cookies.get("nexa_provider_session")
    if isinstance(cookie, str) and cookie:
        return cookie
    raise RegistrationRecoveryReviewGateError(
        "REGISTRATION_RECOVERY_REVIEW_SESSION_REQUIRED", http_status=401
    )


def _parse_recent_mfa(value: object, *, now: datetime) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        verified_at = datetime.fromisoformat(value)
    except ValueError:
        return False
    if verified_at.tzinfo is None:
        return False
    age = (now - verified_at.astimezone(timezone.utc)).total_seconds()
    # Small negative tolerance protects against ordinary clock skew without
    # accepting a materially future-dated authority assertion.
    return -60 <= age <= REGISTRATION_RECOVERY_REVIEW_MFA_MAX_AGE_SECONDS


def _affiliation_window_is_current(
    affiliation: ProviderHospitalAffiliation, *, now: datetime
) -> bool:
    valid_from = affiliation.valid_from
    valid_until = affiliation.valid_until
    if valid_from is not None:
        if valid_from.tzinfo is None or now < valid_from.astimezone(timezone.utc):
            return False
    if valid_until is not None:
        if valid_until.tzinfo is None or now >= valid_until.astimezone(timezone.utc):
            return False
    return True


async def authorize_registration_recovery_reviewer(
    db: AsyncSession,
    *,
    provider: ProviderContext,
    session_token: str,
    now: datetime | None = None,
    require_recent_mfa: bool = True,
) -> RegistrationRecoveryReviewer:
    """Resolve independent administrative reviewer authority from live state."""

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise RegistrationRecoveryReviewGateError(
            "REGISTRATION_RECOVERY_REVIEW_AUTHORITY_INVALID"
        )
    expected_binding = hashlib.sha256(session_token.encode("utf-8")).hexdigest()
    if (
        not provider.session_binding
        or not secrets.compare_digest(provider.session_binding, expected_binding)
    ):
        raise RegistrationRecoveryReviewGateError(
            "REGISTRATION_RECOVERY_REVIEW_SESSION_BINDING_MISMATCH", http_status=401
        )

    session_context = await resolve_provider_session_context(session_token)
    if session_context is None:
        raise RegistrationRecoveryReviewGateError(
            "REGISTRATION_RECOVERY_REVIEW_SESSION_REQUIRED", http_status=401
        )
    if str(session_context.get("provider_id", "")) != provider.actor_uid:
        raise RegistrationRecoveryReviewGateError(
            "REGISTRATION_RECOVERY_REVIEW_SESSION_BINDING_MISMATCH", http_status=401
        )
    if require_recent_mfa and not _parse_recent_mfa(
        session_context.get("mfa_verified_at"), now=current_time
    ):
        raise RegistrationRecoveryReviewGateError(
            "REGISTRATION_RECOVERY_REVIEW_MFA_REQUIRED", http_status=403
        )

    affiliation = (
        await db.execute(
            select(ProviderHospitalAffiliation).where(
                ProviderHospitalAffiliation.id == provider.affiliation.affiliation_id,
                ProviderHospitalAffiliation.provider_id == provider.provider.provider_id,
                ProviderHospitalAffiliation.hospital_id == provider.hospital.hospital_id,
            )
        )
    ).scalar_one_or_none()
    if affiliation is None:
        raise RegistrationRecoveryReviewGateError(
            "REGISTRATION_RECOVERY_REVIEW_AFFILIATION_INVALID"
        )
    if (
        not affiliation.is_active
        or affiliation.trust_status != AffiliationTrustStatus.ACTIVE.value
        or not _affiliation_window_is_current(affiliation, now=current_time)
    ):
        raise RegistrationRecoveryReviewGateError(
            "REGISTRATION_RECOVERY_REVIEW_AFFILIATION_INVALID"
        )
    if REGISTRATION_RECOVERY_REVIEWER_ROLE not in (affiliation.roles or []):
        raise RegistrationRecoveryReviewGateError(
            "REGISTRATION_RECOVERY_REVIEW_ROLE_REQUIRED"
        )

    return RegistrationRecoveryReviewer(
        reviewer_id=provider.actor_uid,
        hospital_id=str(provider.hospital.hospital_id),
        affiliation_id=str(affiliation.id),
        authority_version=REGISTRATION_RECOVERY_REVIEW_AUTHORITY_VERSION,
        session_binding=expected_binding,
    )


async def _audit_rejected(
    db: AsyncSession, *, provider: ProviderContext, code: str
) -> None:
    await enqueue_audit_event(
        db,
        audit_context=current_audit_context(AuditDomain.AUTH),
        idempotency_key=f"registration-recovery-review:access-rejected:{uuid.uuid4()}",
        actor_id=provider.actor_uid,
        event_type="PATIENT_REGISTRATION_RECOVERY_REVIEW_ACCESS_REJECTED",
        target_id=provider.actor_uid,
        patient_id=None,
        status="REJECTED",
        metadata={"reason": code},
    )
    await db.commit()


async def get_registration_recovery_reviewer(
    request: Request,
    provider: ProviderContext = Depends(get_current_provider),
    db: AsyncSession = Depends(get_db_session),
) -> RegistrationRecoveryReviewer:
    """FastAPI dependency for high-risk registration-recovery review mutations."""

    try:
        token = _current_session_token(request)
        return await authorize_registration_recovery_reviewer(
            db, provider=provider, session_token=token
        )
    except RegistrationRecoveryReviewGateError as exc:
        try:
            await _audit_rejected(db, provider=provider, code=exc.code)
        except Exception:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "REGISTRATION_RECOVERY_REVIEW_AUDIT_UNAVAILABLE",
                    "retryable": True,
                },
            ) from None
        raise HTTPException(
            status_code=exc.http_status,
            detail={"error_code": exc.code},
        ) from None


__all__ = [
    "REGISTRATION_RECOVERY_REVIEW_MFA_MAX_AGE_SECONDS",
    "RegistrationRecoveryReviewGateError",
    "RegistrationRecoveryReviewer",
    "authorize_registration_recovery_reviewer",
    "get_registration_recovery_reviewer",
]

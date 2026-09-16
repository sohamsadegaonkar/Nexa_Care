"""Pure authority constructor for bounded clinical access sessions.

The first Slice-10B increment intentionally creates no new bearer token and
performs no persistence.  It defines and validates the server-owned session
shape that later Redis/PostgreSQL wiring will persist.  This keeps the initial
change fail-closed: no new clinical privilege exists merely because this module
is present.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    SIGNED_CONSENT_V3_PROTOCOL_VERSION,
    ClinicalAccessOperation,
    ClinicalAccessPolicyError,
    operations_for_signed_v3,
)


class ClinicalAccessSessionError(ValueError):
    """Fail-closed validation error for session construction."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ClinicalAccessSession:
    """Server-owned bounded treatment-session authority descriptor."""

    session_id: str
    patient_id: str
    provider_id: str
    hospital_id: str
    consent_request_id: str
    purpose: str
    scope: str
    allowed_operations: tuple[str, ...]
    provider_session_binding_hash: str
    issued_at: datetime
    expires_at: datetime
    policy_version: str = CLINICAL_ACCESS_POLICY_VERSION
    encounter_id: str | None = None
    revoked_at: datetime | None = None
    revocation_reason: str | None = None

    def is_active(self, *, at: datetime | None = None) -> bool:
        """Return whether the session is live at the supplied UTC-aware time."""

        instant = at or datetime.now(timezone.utc)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ClinicalAccessSessionError("CLINICAL_SESSION_TIME_MUST_BE_AWARE")
        return self.revoked_at is None and instant < self.expires_at

    def allows(
        self,
        operation: ClinicalAccessOperation,
        *,
        at: datetime | None = None,
    ) -> bool:
        """Check active lifecycle plus the closed operation vocabulary."""

        return self.is_active(at=at) and operation.value in self.allowed_operations


def hash_provider_session_binding(binding: str) -> str:
    """Hash an exact provider-session binding before any persistence/logging."""

    clean = binding.strip()
    if not clean:
        raise ClinicalAccessSessionError("CLINICAL_SESSION_BINDING_REQUIRED")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = str(data.get(key, "")).strip()
    if not value:
        raise ClinicalAccessSessionError(f"CLINICAL_SESSION_{key.upper()}_REQUIRED")
    return value


def _parse_access_expiry(value: object) -> datetime:
    try:
        expires_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ClinicalAccessSessionError("CLINICAL_SESSION_EXPIRY_INVALID") from exc
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise ClinicalAccessSessionError("CLINICAL_SESSION_EXPIRY_INVALID")
    return expires_at


def build_from_signed_v3_approval(
    *,
    request_data: Mapping[str, Any],
    provider_session_binding: str,
    now: datetime | None = None,
) -> ClinicalAccessSession:
    """Construct a bounded read session from an already-approved V3 request.

    Security properties:
    - only canonical Signed Consent V3 approvals are accepted;
    - patient/provider/hospital/request bindings are mandatory;
    - access expiry must be timezone-aware and still live;
    - the raw provider-session binding is never retained;
    - operation scope is server-derived and cannot widen V3 authority;
    - document-processing consent is rejected because it is a separate grant.
    """

    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ClinicalAccessSessionError("CLINICAL_SESSION_TIME_MUST_BE_AWARE")
    if request_data.get("protocol_version") != SIGNED_CONSENT_V3_PROTOCOL_VERSION:
        raise ClinicalAccessSessionError("CLINICAL_SESSION_PROTOCOL_MISMATCH")
    if request_data.get("status") != "approved":
        raise ClinicalAccessSessionError("CLINICAL_SESSION_CONSENT_NOT_APPROVED")

    request_id = _required_text(request_data, "request_id")
    patient_id = _required_text(request_data, "patient_id")
    provider_id = _required_text(request_data, "provider_id")
    hospital_id = _required_text(request_data, "hospital_id")
    purpose = _required_text(request_data, "purpose")
    scope = _required_text(request_data, "scope")
    expires_at = _parse_access_expiry(request_data.get("access_expires_at"))
    if instant >= expires_at:
        raise ClinicalAccessSessionError("CLINICAL_SESSION_ACCESS_EXPIRED")

    try:
        operations = operations_for_signed_v3(purpose=purpose, scope=scope)
    except ClinicalAccessPolicyError as exc:
        raise ClinicalAccessSessionError("CLINICAL_SESSION_SCOPE_NOT_ELIGIBLE") from exc

    binding_hash = hash_provider_session_binding(provider_session_binding)
    issued_at = instant.astimezone(timezone.utc)
    return ClinicalAccessSession(
        session_id=str(uuid.uuid4()),
        patient_id=patient_id,
        provider_id=provider_id,
        hospital_id=hospital_id,
        consent_request_id=request_id,
        purpose=purpose,
        scope=scope,
        allowed_operations=operations,
        provider_session_binding_hash=binding_hash,
        issued_at=issued_at,
        expires_at=expires_at.astimezone(timezone.utc),
    )


def binding_matches(session: ClinicalAccessSession, raw_binding: str) -> bool:
    """Constant-time verification of the provider session bound to authority."""

    candidate = hash_provider_session_binding(raw_binding)
    return secrets.compare_digest(session.provider_session_binding_hash, candidate)

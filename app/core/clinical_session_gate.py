"""Central fail-closed authority gate for Treatment Session V1.

This module validates the opaque treatment bearer against both live Redis
authority and durable PostgreSQL session/grant state.  It intentionally is not
wired into clinical write routes yet: Slice 10B.5 qualifies this boundary
before any encounter or record mutation may consume it.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_clinical_capability
from app.core.redis import get_async_redis_client
from app.models.clinical_access_session import ClinicalAccessSessionRecord
from app.models.consent_grant import ConsentGrantLog
from app.models.provider_context import ProviderContext
from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    ClinicalAccessOperation,
)
from app.security.provider_capabilities import ClinicalCapability
from app.services.signed_treatment_session_v1 import (
    SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
    TreatmentSessionV1ProtocolError,
    normalize_treatment_operations,
)
from app.services.treatment_session_v1_mint import (
    TREATMENT_CAPABILITY_PREFIX,
    TREATMENT_SESSION_V1_GRANT_TYPE,
    TREATMENT_SESSION_V1_SCOPE,
    provider_session_binding_matches,
    treatment_token_hash,
)


class TreatmentSessionV1GateError(RuntimeError):
    """Base class for a treatment-session gate failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TreatmentSessionV1GateDenied(TreatmentSessionV1GateError):
    """Authority is absent, stale, mismatched, or insufficient."""


class TreatmentSessionV1GateUnavailable(TreatmentSessionV1GateError):
    """An authoritative security store could not be evaluated safely."""


@dataclass(frozen=True, slots=True)
class TreatmentSessionV1Authority:
    """Server-validated authority returned only after live/durable agreement."""

    session_id: uuid.UUID
    request_id: uuid.UUID
    patient_id: uuid.UUID
    provider_id: uuid.UUID
    hospital_id: uuid.UUID
    purpose: str
    allowed_operations: tuple[str, ...]
    required_operation: ClinicalAccessOperation
    provider_session_binding_hash: str
    policy_version: str
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    encounter_id: uuid.UUID | None


def _deny(code: str = "TREATMENT_SESSION_NOT_AUTHORIZED") -> TreatmentSessionV1GateDenied:
    return TreatmentSessionV1GateDenied(code)


def _aware(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _deny() from exc
    else:
        raise _deny()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _deny()
    return parsed.astimezone(timezone.utc)


def _uuid(value: object) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise _deny() from exc


def _sha256_hex(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise _deny()
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise _deny() from exc
    return value


def _parse_live_capability(
    *,
    payload: object,
    token_digest: str,
    provider: ProviderContext,
    required_operation: ClinicalAccessOperation,
    now: datetime,
) -> TreatmentSessionV1Authority:
    if not isinstance(payload, dict):
        raise _deny()
    if (
        payload.get("protocol_version") != SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION
        or payload.get("grant_type") != TREATMENT_SESSION_V1_GRANT_TYPE
        or payload.get("scope") != TREATMENT_SESSION_V1_SCOPE
        or payload.get("clinical_access_policy_version")
        != CLINICAL_ACCESS_POLICY_VERSION
    ):
        raise _deny()

    purpose = payload.get("purpose")
    if (
        not isinstance(purpose, str)
        or not purpose
        or purpose != purpose.strip()
        or len(purpose) > 64
    ):
        raise _deny()

    raw_operations = payload.get("allowed_operations")
    try:
        operations = normalize_treatment_operations(raw_operations)
    except (TreatmentSessionV1ProtocolError, TypeError) as exc:
        raise _deny() from exc
    if not isinstance(raw_operations, list) or raw_operations != list(operations):
        raise _deny()
    if required_operation.value not in operations:
        raise _deny("TREATMENT_OPERATION_NOT_AUTHORIZED")

    session_id = _uuid(payload.get("session_id"))
    request_id = _uuid(payload.get("request_id"))
    patient_id = _uuid(payload.get("patient_id"))
    provider_id = _uuid(payload.get("provider_id"))
    hospital_id = _uuid(payload.get("hospital_id"))
    binding_hash = _sha256_hex(payload.get("provider_session_binding_hash"))

    if provider.actor_uid != str(provider_id) or provider.hospital_id != hospital_id:
        raise _deny()
    if not provider_session_binding_matches(
        stored_hash=binding_hash,
        live_binding=provider.session_binding,
    ):
        raise _deny()

    issued_at = _aware(payload.get("issued_at"))
    expires_at = _aware(payload.get("expires_at"))
    if issued_at > now or expires_at <= issued_at or now >= expires_at:
        raise _deny()

    # Encounter binding is durable server state, not part of the minted Redis
    # capability.  Reject an injected Redis encounter binding rather than
    # allowing live state to impersonate the durable correlation.
    if "encounter_id" in payload:
        raise _deny()

    return TreatmentSessionV1Authority(
        session_id=session_id,
        request_id=request_id,
        patient_id=patient_id,
        provider_id=provider_id,
        hospital_id=hospital_id,
        purpose=purpose,
        allowed_operations=operations,
        required_operation=required_operation,
        provider_session_binding_hash=binding_hash,
        policy_version=CLINICAL_ACCESS_POLICY_VERSION,
        token_hash=token_digest,
        issued_at=issued_at,
        expires_at=expires_at,
        encounter_id=None,
    )


def _durable_session_matches(
    row: object,
    authority: TreatmentSessionV1Authority,
    *,
    now: datetime,
) -> bool:
    try:
        row_issued_at = _aware(getattr(row, "issued_at"))
        row_expires_at = _aware(getattr(row, "expires_at"))
        row_binding_hash = _sha256_hex(getattr(row, "provider_session_binding_hash"))
        row_token_hash = _sha256_hex(getattr(row, "token_hash"))
        row_session_id = _uuid(getattr(row, "session_id"))
        row_patient_id = _uuid(getattr(row, "patient_id"))
        row_provider_id = _uuid(getattr(row, "provider_id"))
        row_hospital_id = _uuid(getattr(row, "hospital_id"))
        row_request_id = _uuid(getattr(row, "consent_request_id"))
        row_operations = normalize_treatment_operations(getattr(row, "allowed_operations"))
    except (AttributeError, TreatmentSessionV1GateDenied, TreatmentSessionV1ProtocolError):
        return False

    return (
        getattr(row, "status", None) == "ACTIVE"
        and getattr(row, "revoked_at", None) is None
        and getattr(row, "revocation_reason", None) is None
        and getattr(row, "scope", None) == TREATMENT_SESSION_V1_SCOPE
        and getattr(row, "purpose", None) == authority.purpose
        and getattr(row, "policy_version", None) == authority.policy_version
        and row_session_id == authority.session_id
        and row_patient_id == authority.patient_id
        and row_provider_id == authority.provider_id
        and row_hospital_id == authority.hospital_id
        and row_request_id == authority.request_id
        and secrets.compare_digest(row_token_hash, authority.token_hash)
        and secrets.compare_digest(
            row_binding_hash, authority.provider_session_binding_hash
        )
        and row_operations == authority.allowed_operations
        and list(getattr(row, "allowed_operations")) == list(authority.allowed_operations)
        and row_issued_at == authority.issued_at
        and row_expires_at == authority.expires_at
        and now < row_expires_at
    )


def _durable_grant_matches(
    grant: object,
    authority: TreatmentSessionV1Authority,
    *,
    now: datetime,
) -> bool:
    try:
        grant_expires_at = _aware(getattr(grant, "expires_at"))
        grant_issued_at = _aware(getattr(grant, "issued_at"))
        assurance_verified_at = _aware(getattr(grant, "assurance_verified_at"))
        grant_hospital_id = _uuid(getattr(grant, "hospital_id"))
        grant_token_hash = _sha256_hex(getattr(grant, "token_hash"))
        grant_request_id = _uuid(getattr(grant, "request_id"))
    except (AttributeError, TreatmentSessionV1GateDenied):
        return False

    return (
        getattr(grant, "is_break_glass", None) is False
        and getattr(grant, "reason_code", None) is None
        and getattr(grant, "revoked_at", None) is None
        and getattr(grant, "revoked_reason", None) is None
        and getattr(grant, "purpose", None) == authority.purpose
        and getattr(grant, "scope", None) == [TREATMENT_SESSION_V1_SCOPE]
        and getattr(grant, "assurance_level", None) == "signed_device_treatment_v1"
        and str(getattr(grant, "patient_id", "")) == str(authority.patient_id)
        and str(getattr(grant, "clinician_id", "")) == str(authority.provider_id)
        and grant_hospital_id == authority.hospital_id
        and grant_request_id == authority.request_id
        and secrets.compare_digest(grant_token_hash, authority.token_hash)
        and grant_issued_at <= now
        and assurance_verified_at == grant_issued_at
        and grant_expires_at == authority.expires_at
        and now < grant_expires_at
    )


async def validate_treatment_session_v1(
    *,
    db: AsyncSession,
    token: str | None,
    provider: ProviderContext,
    required_operation: ClinicalAccessOperation,
    now: datetime | None = None,
) -> TreatmentSessionV1Authority:
    """Validate one treatment operation against live and durable authority."""

    if not isinstance(required_operation, ClinicalAccessOperation):
        raise TypeError("required_operation must be ClinicalAccessOperation")
    if not isinstance(token, str) or not token.strip():
        raise TreatmentSessionV1GateDenied("TREATMENT_SESSION_TOKEN_REQUIRED")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)

    digest = treatment_token_hash(token)
    try:
        redis = get_async_redis_client()
        raw = await redis.get(f"{TREATMENT_CAPABILITY_PREFIX}{digest}")
    except Exception as exc:
        raise TreatmentSessionV1GateUnavailable(
            "TREATMENT_SESSION_SECURITY_STORE_UNAVAILABLE"
        ) from exc
    if raw is None:
        raise _deny()
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _deny() from exc
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise _deny() from exc

    authority = _parse_live_capability(
        payload=payload,
        token_digest=digest,
        provider=provider,
        required_operation=required_operation,
        now=current,
    )

    try:
        session_row = (
            await db.execute(
                select(ClinicalAccessSessionRecord).where(
                    ClinicalAccessSessionRecord.session_id == authority.session_id
                )
            )
        ).scalar_one_or_none()
        if session_row is None or not _durable_session_matches(
            session_row, authority, now=current
        ):
            raise _deny()
        try:
            durable_encounter_id = (
                _uuid(session_row.encounter_id)
                if session_row.encounter_id is not None
                else None
            )
        except TreatmentSessionV1GateDenied:
            raise _deny() from None
        authority = replace(authority, encounter_id=durable_encounter_id)

        grant_row = (
            await db.execute(
                select(ConsentGrantLog).where(
                    ConsentGrantLog.token_hash == authority.token_hash,
                    ConsentGrantLog.request_id == str(authority.request_id),
                )
            )
        ).scalar_one_or_none()
    except TreatmentSessionV1GateDenied:
        raise
    except Exception as exc:
        raise TreatmentSessionV1GateUnavailable(
            "TREATMENT_SESSION_DURABLE_STORE_UNAVAILABLE"
        ) from exc

    if grant_row is None or not _durable_grant_matches(
        grant_row, authority, now=current
    ):
        raise _deny()

    return authority


async def stage_server_encounter_binding(
    *,
    db: AsyncSession,
    authority: TreatmentSessionV1Authority,
) -> uuid.UUID:
    """Reserve one server-generated encounter correlation on a qualified session.

    This does not create a clinical Encounter record and does not authorize a
    clinical write.  It only reserves the server-owned session binding that a
    later canonical encounter transaction must use.
    """

    if authority.required_operation is not ClinicalAccessOperation.CREATE_ENCOUNTER:
        raise TreatmentSessionV1GateDenied("TREATMENT_ENCOUNTER_OPERATION_REQUIRED")

    try:
        row = (
            await db.execute(
                select(ClinicalAccessSessionRecord)
                .where(
                    ClinicalAccessSessionRecord.session_id == authority.session_id
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
    except Exception as exc:
        raise TreatmentSessionV1GateUnavailable(
            "TREATMENT_SESSION_DURABLE_STORE_UNAVAILABLE"
        ) from exc

    current = datetime.now(timezone.utc)
    if row is None or not _durable_session_matches(row, authority, now=current):
        raise _deny()

    existing = getattr(row, "encounter_id", None)
    if existing is not None:
        try:
            return uuid.UUID(str(existing))
        except (TypeError, ValueError, AttributeError) as exc:
            raise _deny() from exc

    encounter_id = uuid.uuid4()
    row.encounter_id = str(encounter_id)
    try:
        await db.flush()
    except Exception as exc:
        raise TreatmentSessionV1GateUnavailable(
            "TREATMENT_ENCOUNTER_BINDING_UNAVAILABLE"
        ) from exc
    return encounter_id


def require_clinical_session(operation: ClinicalAccessOperation):
    """Return a FastAPI dependency for one exact Treatment Session V1 operation."""

    if not isinstance(operation, ClinicalAccessOperation):
        raise TypeError("operation must be ClinicalAccessOperation")

    async def dependency(
        x_treatment_token: str | None = Header(
            default=None,
            alias="X-Treatment-Token",
        ),
        provider: ProviderContext = Depends(
            require_clinical_capability(ClinicalCapability.RECORD_READ)
        ),
        db: AsyncSession = Depends(get_db_session),
    ) -> TreatmentSessionV1Authority:
        try:
            return await validate_treatment_session_v1(
                db=db,
                token=x_treatment_token,
                provider=provider,
                required_operation=operation,
            )
        except TreatmentSessionV1GateUnavailable as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error_code": exc.code},
            ) from exc
        except TreatmentSessionV1GateDenied as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error_code": exc.code},
            ) from exc

    return dependency

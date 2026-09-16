"""Short-lived provider capabilities claimed from signed patient approvals."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.redis import get_async_redis_client
from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    SIGNED_CONSENT_V3_PROTOCOL_VERSION,
    ClinicalAccessPolicyError,
    operation_for_record_category,
    operations_for_signed_v3,
)
from app.security.document_processing_policy import (
    DOCUMENT_PROCESSING_GRANT_TYPE,
    DOCUMENT_PROCESSING_PURPOSE,
    DOCUMENT_PROCESSING_SCOPE,
    DocumentProcessingOperation,
    operations_for_grant,
)
from app.services.clinical_access_session import (
    ClinicalAccessSessionError,
    build_from_signed_v3_approval,
    hash_provider_session_binding,
)


CAPABILITY_PREFIX = "consent_access:capability:"
CLAIM_PREFIX = "consent_access:claim:"
CLINICAL_ACCESS_SESSION_GRANT_TYPE = "clinical_access_session"


_CLAIM_ONCE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[3])
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""


class ApprovedAccessStoreUnavailable(RuntimeError):
    """Raised when the capability store cannot be safely read or written."""


class ApprovedAccessClaimInProgress(RuntimeError):
    """Raised when another worker is rotating this request's capability."""


@dataclass(frozen=True, slots=True)
class ApprovedAccessCapability:
    patient_id: str
    clinician_id: str
    hospital_id: str
    request_id: str
    purpose: str
    scope: list[str]
    is_break_glass: bool
    reason_code: str | None
    issued_at: str
    expires_at: str
    grant_type: str = "clinical"
    allowed_operations: tuple[str, ...] = ()
    clinical_session_id: str | None = None
    provider_session_binding_hash: str | None = None
    clinical_access_policy_version: str | None = None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _capability_key(digest: str) -> str:
    return f"{CAPABILITY_PREFIX}{digest}"


def _claim_key(request_id: str) -> str:
    return f"{CLAIM_PREFIX}{request_id}"


def _decode(raw: object) -> dict | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _scope_allows(scope: str, requested_category: str) -> bool:
    clinical_reads = {"clinical_summary", "timeline_view"}
    if scope == "full":
        return requested_category in clinical_reads | {
            "full",
            "policy_read",
            "policy_update",
        }
    return scope == "clinical" and requested_category in clinical_reads


def _binding_hash_matches(stored_hash: str | None, provider_session_binding: str | None) -> bool:
    if not isinstance(stored_hash, str) or len(stored_hash) != 64:
        return False
    if not isinstance(provider_session_binding, str) or not provider_session_binding.strip():
        return False
    try:
        candidate = hash_provider_session_binding(provider_session_binding)
    except ClinicalAccessSessionError:
        return False
    return secrets.compare_digest(stored_hash, candidate)


async def issue_from_approved_request(
    *,
    request_data: dict,
    provider_session_binding: str | None = None,
) -> tuple[str, ApprovedAccessCapability]:
    """Atomically claim an approved request exactly once.

    Canonical Signed Consent V3 routine grants are upgraded into a bounded
    read-only clinical access session.  The session is bound to the exact
    authenticated provider session through a one-way derived binding hash.
    Legacy routine grants retain their compatibility shape until separately
    retired; document-processing grants remain a distinct operation vocabulary.
    """
    now = datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(str(request_data["access_expires_at"]))
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    ttl = int((expires_at - now).total_seconds())
    if ttl <= 0:
        raise ValueError("Approved access window has expired")

    token = secrets.token_urlsafe(48)
    digest = token_hash(token)
    scope = str(request_data["scope"])
    purpose = str(request_data["purpose"])
    allowed_operations = operations_for_grant(purpose, scope)
    grant_type = DOCUMENT_PROCESSING_GRANT_TYPE if allowed_operations else "clinical"
    clinical_session_id: str | None = None
    provider_session_binding_hash: str | None = None
    clinical_access_policy_version: str | None = None

    if (
        grant_type == "clinical"
        and request_data.get("protocol_version") == SIGNED_CONSENT_V3_PROTOCOL_VERSION
    ):
        session = build_from_signed_v3_approval(
            request_data=request_data,
            provider_session_binding=provider_session_binding or "",
            now=now,
        )
        grant_type = CLINICAL_ACCESS_SESSION_GRANT_TYPE
        allowed_operations = session.allowed_operations
        clinical_session_id = session.session_id
        provider_session_binding_hash = session.provider_session_binding_hash
        clinical_access_policy_version = session.policy_version

    payload = {
        "request_id": str(request_data["request_id"]),
        "provider_id": str(request_data["provider_id"]),
        "hospital_id": str(request_data["hospital_id"]),
        "patient_id": str(request_data["patient_id"]),
        "purpose": purpose,
        "scope": scope,
        "protocol_version": request_data.get("protocol_version"),
        "grant_type": grant_type,
        "allowed_operations": list(allowed_operations),
        "issued_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    if grant_type == CLINICAL_ACCESS_SESSION_GRANT_TYPE:
        payload.update(
            {
                "clinical_session_id": clinical_session_id,
                "provider_session_binding_hash": provider_session_binding_hash,
                "clinical_access_policy_version": clinical_access_policy_version,
            }
        )

    try:
        redis = get_async_redis_client()
        claimed = await redis.eval(
            _CLAIM_ONCE_LUA,
            2,
            _claim_key(payload["request_id"]),
            _capability_key(digest),
            json.dumps(payload),
            digest,
            ttl,
        )
        if int(claimed) != 1:
            raise ApprovedAccessClaimInProgress(
                "The approved request has already been claimed"
            )
    except ApprovedAccessClaimInProgress:
        raise
    except Exception as exc:
        raise ApprovedAccessStoreUnavailable(
            "Approved access store is unavailable"
        ) from exc

    return token, ApprovedAccessCapability(
        patient_id=payload["patient_id"],
        clinician_id=payload["provider_id"],
        hospital_id=payload["hospital_id"],
        request_id=payload["request_id"],
        purpose=payload["purpose"],
        scope=[scope],
        is_break_glass=False,
        reason_code=None,
        issued_at=payload["issued_at"],
        expires_at=payload["expires_at"],
        grant_type=grant_type,
        allowed_operations=tuple(allowed_operations),
        clinical_session_id=clinical_session_id,
        provider_session_binding_hash=provider_session_binding_hash,
        clinical_access_policy_version=clinical_access_policy_version,
    )


async def invalidate_request(request_id: str) -> None:
    try:
        redis = get_async_redis_client()
        digest = await redis.get(_claim_key(request_id))
        if isinstance(digest, bytes):
            digest = digest.decode("utf-8")
        if isinstance(digest, str):
            await redis.delete(_capability_key(digest))
        await redis.delete(_claim_key(request_id))
    except Exception as exc:
        raise ApprovedAccessStoreUnavailable(
            "Approved access store is unavailable"
        ) from exc


async def validate_live_document_processing_request(
    *,
    request_id: str,
    patient_id: str,
    provider_id: str,
    hospital_id: str,
) -> ApprovedAccessCapability | None:
    """Recheck the authoritative live grant by its server-bound request ID."""
    try:
        redis = get_async_redis_client()
        digest = await redis.get(_claim_key(request_id))
        if isinstance(digest, bytes):
            digest = digest.decode("utf-8")
        if not isinstance(digest, str):
            return None
        payload = _decode(await redis.get(_capability_key(digest)))
        request_data = _decode(await redis.get(f"consent_request:{request_id}"))
    except Exception as exc:
        raise ApprovedAccessStoreUnavailable(
            "Approved access store is unavailable"
        ) from exc
    if payload is None or request_data is None:
        return None
    now = datetime.now(timezone.utc)
    try:
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    expected = {
        "request_id": request_id,
        "patient_id": patient_id,
        "provider_id": provider_id,
        "hospital_id": hospital_id,
    }
    if (
        request_data.get("status") != "approved"
        or now >= expires_at
        or any(str(payload.get(key)) != str(value) for key, value in expected.items())
        or any(
            str(request_data.get(key)) != str(value) for key, value in expected.items()
        )
        or payload.get("purpose") != DOCUMENT_PROCESSING_PURPOSE
        or payload.get("scope") != DOCUMENT_PROCESSING_SCOPE
        or payload.get("grant_type") != DOCUMENT_PROCESSING_GRANT_TYPE
    ):
        return None
    return ApprovedAccessCapability(
        patient_id=patient_id,
        clinician_id=provider_id,
        hospital_id=hospital_id,
        request_id=request_id,
        purpose=DOCUMENT_PROCESSING_PURPOSE,
        scope=[DOCUMENT_PROCESSING_SCOPE],
        is_break_glass=False,
        reason_code=None,
        issued_at=str(payload["issued_at"]),
        expires_at=expires_at.isoformat(),
        grant_type=DOCUMENT_PROCESSING_GRANT_TYPE,
        allowed_operations=tuple(
            str(operation)
            for operation in payload.get("allowed_operations", [])
            if isinstance(operation, str)
        ),
    )


async def _load_live_capability(
    *,
    token: str,
    patient_id: str,
    provider_id: str,
    hospital_id: str,
) -> ApprovedAccessCapability | None:
    """Load a live capability after all identity, request, and revocation checks."""
    try:
        redis = get_async_redis_client()
        digest = token_hash(token)
        payload = _decode(await redis.get(_capability_key(digest)))
        if payload is None:
            return None
        request_id = str(payload.get("request_id", ""))
        active_digest = await redis.get(_claim_key(request_id))
        if isinstance(active_digest, bytes):
            active_digest = active_digest.decode("utf-8")
        request_data = _decode(await redis.get(f"consent_request:{request_id}"))
    except Exception as exc:
        raise ApprovedAccessStoreUnavailable(
            "Approved access store is unavailable"
        ) from exc

    now = datetime.now(timezone.utc)
    try:
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None

    expected = {
        "provider_id": provider_id,
        "hospital_id": hospital_id,
        "patient_id": patient_id,
    }
    if (
        active_digest != digest
        or request_data is None
        or request_data.get("status") != "approved"
    ):
        return None
    if now >= expires_at or any(
        str(payload.get(k)) != str(v) for k, v in expected.items()
    ):
        return None
    if any(
        str(request_data.get(k)) != str(payload.get(k))
        for k in (
            "request_id",
            "provider_id",
            "hospital_id",
            "patient_id",
            "purpose",
            "scope",
        )
    ):
        return None

    grant_type = str(payload.get("grant_type", "clinical"))
    allowed_operations = tuple(
        str(operation)
        for operation in payload.get("allowed_operations", [])
        if isinstance(operation, str)
    )
    clinical_session_id = payload.get("clinical_session_id")
    provider_session_binding_hash = payload.get("provider_session_binding_hash")
    policy_version = payload.get("clinical_access_policy_version")

    if grant_type == CLINICAL_ACCESS_SESSION_GRANT_TYPE:
        if (
            request_data.get("protocol_version") != SIGNED_CONSENT_V3_PROTOCOL_VERSION
            or payload.get("protocol_version") != SIGNED_CONSENT_V3_PROTOCOL_VERSION
            or not isinstance(clinical_session_id, str)
            or not clinical_session_id
            or not isinstance(provider_session_binding_hash, str)
            or len(provider_session_binding_hash) != 64
            or policy_version != CLINICAL_ACCESS_POLICY_VERSION
        ):
            return None
        try:
            expected_operations = operations_for_signed_v3(
                purpose=str(payload["purpose"]),
                scope=str(payload["scope"]),
            )
        except (ClinicalAccessPolicyError, KeyError):
            return None
        if allowed_operations != expected_operations:
            return None

    return ApprovedAccessCapability(
        patient_id=patient_id,
        clinician_id=provider_id,
        hospital_id=hospital_id,
        request_id=request_id,
        purpose=str(payload["purpose"]),
        scope=[str(payload["scope"])],
        is_break_glass=False,
        reason_code=None,
        issued_at=str(payload["issued_at"]),
        expires_at=expires_at.isoformat(),
        grant_type=grant_type,
        allowed_operations=allowed_operations,
        clinical_session_id=(
            str(clinical_session_id) if isinstance(clinical_session_id, str) else None
        ),
        provider_session_binding_hash=(
            str(provider_session_binding_hash)
            if isinstance(provider_session_binding_hash, str)
            else None
        ),
        clinical_access_policy_version=(
            str(policy_version) if isinstance(policy_version, str) else None
        ),
    )


async def validate(
    *,
    token: str,
    patient_id: str,
    provider_id: str,
    hospital_id: str,
    requested_category: str,
    provider_session_binding: str | None = None,
) -> ApprovedAccessCapability | None:
    """Validate a routine clinical category without accepting pipeline grants."""
    capability = await _load_live_capability(
        token=token,
        patient_id=patient_id,
        provider_id=provider_id,
        hospital_id=hospital_id,
    )
    if capability is None:
        return None

    if capability.grant_type == CLINICAL_ACCESS_SESSION_GRANT_TYPE:
        if not _binding_hash_matches(
            capability.provider_session_binding_hash, provider_session_binding
        ):
            return None
        try:
            required_operation = operation_for_record_category(requested_category)
        except ClinicalAccessPolicyError:
            return None
        if required_operation.value not in capability.allowed_operations:
            return None
        return capability

    if capability.grant_type != "clinical":
        return None
    if not _scope_allows(capability.scope[0], requested_category):
        return None
    return capability


async def validate_document_processing_access(
    *,
    token: str,
    patient_id: str,
    provider_id: str,
    hospital_id: str,
    required_operation: DocumentProcessingOperation,
    expected_request_id: str | None = None,
) -> ApprovedAccessCapability | None:
    """Validate one trusted document-processing operation."""
    capability = await _load_live_capability(
        token=token,
        patient_id=patient_id,
        provider_id=provider_id,
        hospital_id=hospital_id,
    )
    if capability is None:
        return None
    if (
        capability.purpose != DOCUMENT_PROCESSING_PURPOSE
        or capability.scope != [DOCUMENT_PROCESSING_SCOPE]
        or capability.grant_type != DOCUMENT_PROCESSING_GRANT_TYPE
        or required_operation.value not in capability.allowed_operations
    ):
        return None
    if expected_request_id is not None and capability.request_id != expected_request_id:
        return None
    return capability

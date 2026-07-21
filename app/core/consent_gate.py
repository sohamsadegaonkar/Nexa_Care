"""Reusable Consent and Access Gates for Nexa Care V2 Alpha Milestone.

Defines three distinct access gates:
1. require_consent(purpose): For healthcare providers viewing patient clinical data.
2. require_self_patient_access(): For patients accessing their own records/dashboard.
3. require_role(role): For data operators/admins reviewing AI ingestion jobs.

ALPHA: validate_consent_for_patient() is the server-side patient_id consent
path.  Pipeline routes that reference existing entities (ExtractionJob,
ExtractedFieldRecord) MUST use it instead of require_consent() so that the
patient_id is derived from the DB row, never from a client-supplied value.
This eliminates the patient_id spoofing vector described in threat-model.md T-06.
"""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

import logging
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.dependencies import (
    get_current_provider,
    get_scoped_session,
    require_role as deps_require_role,
)
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.services.consent_engine import (
    ConsentCapability,
    ConsentEngineUnavailable,
    validate as validate_consent_capability,
)
from app.services.approved_access_capability import (
    ApprovedAccessCapability,
    ApprovedAccessStoreUnavailable,
    validate as validate_approved_access,
)

logger = logging.getLogger("nexa_logger")

require_role = deps_require_role


# ── Core validation (shared by require_consent and direct callers) ─────────


async def validate_consent_for_patient(
    patient_id: str | None,
    purpose: str,
    provider: ProviderContext,
    x_consent_token: str | None,
) -> ConsentCapability | ApprovedAccessCapability:
    """Validate consent for an explicitly provided patient_id.

    ALPHA: Use this when patient_id is derived server-side from a DB entity
    (ExtractionJob, ExtractedFieldRecord) to eliminate the patient_id
    spoofing vector.  Unlike require_consent(), this function does NOT
    discover patient_id from the request — it must be provided by the
    caller.

    Raises:
        HTTPException 403: Missing consent token or patient_id, or
            invalid/expired consent.
        HTTPException 503: Consent engine (Redis) unavailable.
    """
    actor_uid = provider.actor_uid if provider else "UNKNOWN"
    target_id = str(patient_id) if patient_id else "UNKNOWN"

    if not x_consent_token or not patient_id:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
            actor_uid=actor_uid,
            event_type="CONSENT_GATED_DECRYPT_FAILED",
            target_id=target_id,
            status="MISSING_CONSENT_TOKEN",
            metadata={"purpose": purpose},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active consent token required for patient data access.",
        )

    hospital_id = str(provider.hospital_id)
    try:
        capability = await validate_consent_capability(
            token=x_consent_token,
            patient_id=str(patient_id),
            clinician_id=actor_uid,
            purpose=purpose,
            hospital_id=hospital_id,
            session_binding=provider.session_binding,
        )
        if capability is None:
            capability = await validate_approved_access(
                token=x_consent_token,
                patient_id=str(patient_id),
                provider_id=actor_uid,
                hospital_id=hospital_id,
                requested_category=purpose,
            )
    except (ConsentEngineUnavailable, ApprovedAccessStoreUnavailable) as exc:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
            actor_uid=actor_uid,
            event_type="CONSENT_GATED_DECRYPT_FAILED",
            target_id=target_id,
            status="CONSENT_ENGINE_UNAVAILABLE",
            metadata={"purpose": purpose, "hospital_id": hospital_id},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Consent service is temporarily unavailable.",
        ) from exc

    if capability is None:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
            actor_uid=actor_uid,
            event_type="CONSENT_GATED_DECRYPT_FAILED",
            target_id=target_id,
            status="FORBIDDEN_INVALID_OR_EXPIRED",
            metadata={"purpose": purpose, "hospital_id": hospital_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active consent token required or expired.",
        )

    if getattr(capability, "is_break_glass", False):
        # Break-glass capabilities are scoped to whole clinical categories
        # with their own audit/filtering contract and may only be used
        # against the dedicated emergency-summary endpoint -- never any
        # routine require_consent()-gated view (summary, timeline, record).
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
            actor_uid=actor_uid,
            event_type="CONSENT_GATED_DECRYPT_FAILED",
            target_id=target_id,
            status="BREAK_GLASS_NOT_VALID_FOR_ROUTINE_ENDPOINT",
            metadata={"purpose": purpose, "hospital_id": hospital_id},
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "BREAK_GLASS_CAPABILITY_NOT_VALID_HERE"},
        )

    approved_request_id = getattr(capability, "request_id", None)
    approved_purpose = getattr(capability, "purpose", purpose)
    audit_metadata = {
        "patient_id": target_id,
        "provider_id": actor_uid,
        "hospital_id": hospital_id,
        "purpose": approved_purpose,
        "data_categories": [purpose],
        "scope": capability.scope,
        "consent_request_id": approved_request_id,
        "is_break_glass": capability.is_break_glass,
    }
    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.CONSENT),
        actor_uid=actor_uid,
        event_type="CONSENT_GATED_DECRYPT_STARTED",
        target_id=target_id,
        status="SUCCESS",
        metadata=audit_metadata,
    )

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.CONSENT),
        actor_uid=actor_uid,
        event_type="PATIENT_RECORD_READ_SUCCESS",
        target_id=target_id,
        status="SUCCESS",
        metadata={**audit_metadata, "outcome": "SUCCESS"},
    )

    return capability


# ── FastAPI dependency factories ────────────────────────────────────────────


def require_consent(
    purpose: str,
) -> Callable[[Request, ProviderContext, str | None], Any]:
    """FastAPI dependency factory enforcing live consent for provider access to patient data.

    Discovers patient_id from the request (path params, query params, headers,
    or request body).  For pipeline endpoints that reference existing entities
    (jobs, fields), prefer loading the entity first and calling
    validate_consent_for_patient() directly — this eliminates the spoofing
    vector where a client provides a patient_id that doesn't match the entity.
    """

    async def _consent_gate(
        request: Request,
        provider: ProviderContext = Depends(get_current_provider),
        x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    ) -> ConsentCapability:
        patient_id = request.path_params.get("patient_id") or request.path_params.get(
            "id"
        )
        if not patient_id:
            patient_id = request.query_params.get("patient_id")
        if not patient_id:
            patient_id = request.headers.get("X-Patient-Id")
        if not patient_id and request.method in ("POST", "PUT", "PATCH"):
            try:
                body_json = await request.json()
                if isinstance(body_json, dict):
                    patient_id = body_json.get("patient_id")
            except (ValueError, UnicodeDecodeError) as exc:
                logger.info(
                    "Consent gate could not parse request body",
                    extra={"error_type": type(exc).__name__},
                )

        return await validate_consent_for_patient(
            patient_id=patient_id,
            purpose=purpose,
            provider=provider,
            x_consent_token=x_consent_token,
        )

    return _consent_gate


def require_self_patient_access() -> Callable[[Request, str], Any]:
    """FastAPI dependency factory for patients accessing their own health records."""

    async def _self_access_gate(
        request: Request,
        session_patient_id: str = Depends(get_scoped_session),
    ) -> str:
        target_id = request.path_params.get("patient_id") or request.path_params.get(
            "id"
        )
        if not target_id:
            target_id = request.query_params.get("patient_id")

        if target_id and str(target_id) != str(session_patient_id):
            await append_audit_log_or_503(
                audit_context=current_audit_context(AuditDomain.CONSENT),
                actor_uid=str(session_patient_id),
                event_type="SESSION_VALIDATION_FAILED",
                target_id=str(target_id),
                status="PATIENT_IDOR_ATTEMPT",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Patient session token does not match target record.",
            )

        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
            actor_uid=str(session_patient_id),
            event_type="PATIENT_RECORD_READ_SUCCESS",
            target_id=str(session_patient_id),
            status="SUCCESS",
            metadata={"access_type": "self_access"},
        )

        return session_patient_id

    return _self_access_gate

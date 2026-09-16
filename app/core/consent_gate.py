"""Reusable Consent and Access Gates for Nexa Care V2 Alpha Milestone.

Defines three distinct access gates:
1. require_consent(purpose): For healthcare providers viewing patient clinical data.
2. require_self_patient_access(): For patients accessing their own records/dashboard.
3. require_role(role): For data operators/admins reviewing AI ingestion jobs.

ALPHA: validate_consent_for_patient() is the server-side patient_id consent
path. Pipeline routes that reference existing entities (ExtractionJob,
ExtractedFieldRecord) MUST use it instead of require_consent() so that the
patient_id is derived from the DB row, never from a client-supplied value.
This eliminates the patient_id spoofing vector described in threat-model.md T-06.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from time import perf_counter
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import (
    get_current_provider,
    get_scoped_session,
    require_role as deps_require_role,
)
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.approved_access_capability import (
    CLINICAL_ACCESS_SESSION_GRANT_TYPE,
    ApprovedAccessCapability,
    ApprovedAccessStoreUnavailable,
    token_hash,
    validate as validate_approved_access,
)
from app.services.clinical_access_session_store import validate_active_session
from app.services.consent_engine import (
    ConsentCapability,
    ConsentEngineUnavailable,
    validate as validate_consent_capability,
)

logger = logging.getLogger("nexa_logger")

require_role = deps_require_role


async def validate_consent_for_patient(
    patient_id: str | None,
    purpose: str,
    provider: ProviderContext,
    x_consent_token: str | None,
    db: AsyncSession | None = None,
) -> ConsentCapability | ApprovedAccessCapability:
    """Validate consent for an explicitly provided patient_id.

    Canonical Signed Consent V3 routine access must validate in both Redis and
    PostgreSQL. A missing durable database session therefore fails closed for
    ``clinical_access_session`` grants. Legacy consent-engine capabilities keep
    their existing compatibility path until separately retired.
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
                provider_session_binding=provider.session_binding,
            )
            if (
                capability is not None
                and capability.grant_type == CLINICAL_ACCESS_SESSION_GRANT_TYPE
            ):
                if (
                    db is None
                    or capability.clinical_session_id is None
                    or capability.provider_session_binding_hash is None
                    or capability.clinical_access_policy_version is None
                    or not await validate_active_session(
                        db,
                        session_id=capability.clinical_session_id,
                        token_hash=token_hash(x_consent_token),
                        patient_id=str(patient_id),
                        provider_id=actor_uid,
                        hospital_id=hospital_id,
                        consent_request_id=capability.request_id,
                        provider_session_binding_hash=(
                            capability.provider_session_binding_hash
                        ),
                        allowed_operations=capability.allowed_operations,
                        policy_version=capability.clinical_access_policy_version,
                    )
                ):
                    capability = None
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
    clinical_session_id = getattr(capability, "clinical_session_id", None)
    if clinical_session_id:
        audit_metadata["clinical_session_id"] = clinical_session_id
        audit_metadata["clinical_access_policy_version"] = getattr(
            capability, "clinical_access_policy_version", None
        )

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


def require_consent(
    purpose: str,
) -> Callable[[Request, ProviderContext, str | None, AsyncSession], Any]:
    """FastAPI dependency factory enforcing live consent for provider access."""

    async def _consent_gate(
        request: Request,
        provider: ProviderContext = Depends(get_current_provider),
        x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
        db: AsyncSession = Depends(get_db_session),
    ) -> ConsentCapability | ApprovedAccessCapability:
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
            db=db,
        )

    return _consent_gate


def require_self_patient_access() -> Callable[[Request, str], Any]:
    """FastAPI dependency factory for patients accessing their own health records."""

    async def _self_access_gate(
        request: Request,
        session_patient_id: str = Depends(get_scoped_session),
    ) -> str:
        gate_started = perf_counter()
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

        try:
            await append_audit_log_or_503(
                audit_context=current_audit_context(AuditDomain.CONSENT),
                actor_uid=str(session_patient_id),
                event_type="PATIENT_RECORD_READ_SUCCESS",
                target_id=str(session_patient_id),
                status="SUCCESS",
                metadata={"access_type": "self_access"},
            )
            return session_patient_id
        finally:
            logger.info(
                "Patient self-access gate timing",
                extra={
                    "operation": "require_self_patient_access",
                    "duration_ms": round((perf_counter() - gate_started) * 1000, 2),
                    "row_count": 1,
                },
            )

    return _self_access_gate

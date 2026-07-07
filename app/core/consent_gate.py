"""Reusable Consent and Access Gates for Nexa Care V2 Alpha Milestone.

Defines three distinct access gates:
1. require_consent(purpose): For healthcare providers viewing patient clinical data.
2. require_self_patient_access(): For patients viewing their own records/dashboard.
3. require_role(role): For data operators/admins reviewing AI ingestion jobs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.dependencies import get_current_provider, get_scoped_session, require_role as deps_require_role
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.services.consent_engine import (
    ConsentCapability,
    ConsentEngineUnavailable,
    validate as validate_consent_capability,
)

logger = logging.getLogger("nexa_logger")

require_role = deps_require_role


def require_consent(purpose: str) -> Callable[[Request, ProviderContext, str | None], Any]:
    """FastAPI dependency factory enforcing live consent for provider access to patient data."""

    async def _consent_gate(
        request: Request,
        provider: ProviderContext = Depends(get_current_provider),
        x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    ) -> ConsentCapability:
        patient_id = request.path_params.get("patient_id") or request.path_params.get("id")
        if not patient_id:
            patient_id = request.query_params.get("patient_id")
        if not patient_id:
            patient_id = request.headers.get("X-Patient-Id")
        if not patient_id and request.method in ("POST", "PUT", "PATCH"):
            try:
                body_json = await request.json()
                if isinstance(body_json, dict):
                    patient_id = body_json.get("patient_id")
            except Exception:
                pass

        actor_uid = provider.actor_uid if provider else "UNKNOWN"
        target_id = str(patient_id) if patient_id else "UNKNOWN"

        if not x_consent_token or not patient_id:
            await append_audit_log_or_503(
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

        try:
            capability = await validate_consent_capability(
                token=x_consent_token,
                patient_id=str(patient_id),
                clinician_id=actor_uid,
                purpose=purpose,
            )
        except ConsentEngineUnavailable as exc:
            await append_audit_log_or_503(
                actor_uid=actor_uid,
                event_type="CONSENT_GATED_DECRYPT_FAILED",
                target_id=target_id,
                status="CONSENT_ENGINE_UNAVAILABLE",
                metadata={"purpose": purpose, "error": str(exc)},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Consent service is temporarily unavailable.",
            ) from exc

        if capability is None:
            await append_audit_log_or_503(
                actor_uid=actor_uid,
                event_type="CONSENT_GATED_DECRYPT_FAILED",
                target_id=target_id,
                status="FORBIDDEN_INVALID_OR_EXPIRED",
                metadata={"purpose": purpose},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Active consent token required or expired.",
            )

        await append_audit_log_or_503(
            actor_uid=actor_uid,
            event_type="CONSENT_GATED_DECRYPT_STARTED",
            target_id=target_id,
            status="SUCCESS",
            metadata={"purpose": purpose, "scope": capability.scope},
        )

        await append_audit_log_or_503(
            actor_uid=actor_uid,
            event_type="PATIENT_RECORD_READ_SUCCESS",
            target_id=target_id,
            status="SUCCESS",
            metadata={"purpose": purpose, "scope": capability.scope},
        )

        return capability

    return _consent_gate


def require_self_patient_access() -> Callable[[Request, str], Any]:
    """FastAPI dependency factory for patients accessing their own health records."""

    async def _self_access_gate(
        request: Request,
        session_patient_id: str = Depends(get_scoped_session),
    ) -> str:
        target_id = request.path_params.get("patient_id") or request.path_params.get("id")
        if not target_id:
            target_id = request.query_params.get("patient_id")

        if target_id and str(target_id) != str(session_patient_id):
            await append_audit_log_or_503(
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
            actor_uid=str(session_patient_id),
            event_type="PATIENT_RECORD_READ_SUCCESS",
            target_id=str(session_patient_id),
            status="SUCCESS",
            metadata={"access_type": "self_access"},
        )

        return session_patient_id

    return _self_access_gate

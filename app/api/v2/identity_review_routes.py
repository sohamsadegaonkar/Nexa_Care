"""Metadata-only API for independent identity quarantine review."""

from __future__ import annotations

import uuid
import json
from typing import Any, TypeVar

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider
from app.models.identity_review import (
    ClaimIdentityReviewCaseRequest,
    CreateIdentityReviewCaseRequest,
    RecoverIdentityReviewSessionRequest,
    SubmitIdentityReviewDispositionRequest,
)
from app.models.provider_context import ProviderContext
from app.services.identity_review import (
    IdentityReviewError,
    case_metadata,
    claim_case,
    create_case,
    list_cases,
    read_case,
    recover_session,
    submit_disposition,
)

router = APIRouter(prefix="/api/v2/pipeline", tags=["identity-review"])
ModelT = TypeVar("ModelT", bound=BaseModel)


def _uuid(value: str, *, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(404, detail={"error_code": code}) from exc


def _payload(model: type[ModelT], raw: Any) -> ModelT:
    try:
        # JSON-mode strict validation accepts the wire representations of
        # enums/tuples while still rejecting coercions such as "1" -> 1.
        return model.model_validate_json(
            json.dumps(raw, sort_keys=True, separators=(",", ":"))
        )
    except ValidationError as exc:
        idempotency_error = any(
            "idempotency_key" in tuple(str(part) for part in error["loc"])
            for error in exc.errors()
        )
        raise IdentityReviewError(
            "IDENTITY_REVIEW_IDEMPOTENCY_KEY_INVALID"
            if idempotency_error
            else "IDENTITY_REVIEW_PAYLOAD_INVALID"
        ) from exc


def _http_error(exc: IdentityReviewError) -> HTTPException:
    not_found = {
        "IDENTITY_REVIEW_JOB_NOT_FOUND",
        "IDENTITY_REVIEW_CASE_NOT_FOUND",
    }
    denied = {
        "IDENTITY_REVIEW_ROLE_REQUIRED",
        "IDENTITY_REVIEW_ACCESS_DENIED",
        "IDENTITY_REVIEW_SELF_REVIEW_FORBIDDEN",
        "IDENTITY_REVIEW_CONSENT_INACTIVE",
        "IDENTITY_REVIEW_ERASURE_ACCESS_BLOCKED",
        "IDENTITY_REVIEW_SESSION_REQUIRED",
        "IDENTITY_REVIEW_SESSION_MISMATCH",
    }
    malformed = {
        "IDENTITY_REVIEW_IDEMPOTENCY_KEY_INVALID",
        "IDENTITY_REVIEW_PAYLOAD_INVALID",
    }
    status_code = (
        404
        if exc.code in not_found
        else 403
        if exc.code in denied
        else 422
        if exc.code in malformed
        else 503
        if exc.code == "IDENTITY_REVIEW_ERASURE_REGISTRY_UNAVAILABLE"
        else 409
    )
    return HTTPException(status_code, detail={"error_code": exc.code})


async def _finish_failure(db: AsyncSession, exc: IdentityReviewError) -> None:
    # Authorization rejection audits are staged before any mutation. Preserve
    # those value-free events; all workflow conflicts roll back.
    if exc.code in {
        "IDENTITY_REVIEW_ROLE_REQUIRED",
        "IDENTITY_REVIEW_ACCESS_DENIED",
        "IDENTITY_REVIEW_SELF_REVIEW_FORBIDDEN",
        "IDENTITY_REVIEW_CONSENT_INACTIVE",
        "IDENTITY_REVIEW_ERASURE_ACCESS_BLOCKED",
        "IDENTITY_REVIEW_ERASURE_REGISTRY_UNAVAILABLE",
    }:
        await db.commit()
    else:
        await db.rollback()


@router.post("/jobs/{job_id}/identity-review-cases", status_code=201)
async def create_identity_review_case(
    job_id: str,
    raw_payload: Any = Body(...),
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        payload = _payload(CreateIdentityReviewCaseRequest, raw_payload)
        case = await create_case(
            db,
            job_id=_uuid(job_id, code="IDENTITY_REVIEW_JOB_NOT_FOUND"),
            provider=provider,
            capability_token=x_consent_token,
            idempotency_key=payload.idempotency_key,
        )
        response = await case_metadata(db, case=case, provider=provider)
        await db.commit()
        return response
    except IdentityReviewError as exc:
        await _finish_failure(db, exc)
        raise _http_error(exc) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.get("/patients/{patient_id}/identity-review-cases")
async def list_identity_review_cases(
    patient_id: str,
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        rows = await list_cases(
            db,
            patient_id=_uuid(patient_id, code="IDENTITY_REVIEW_CASE_NOT_FOUND"),
            provider=provider,
            capability_token=x_consent_token,
        )
        response = [
            await case_metadata(db, case=case, provider=provider) for case in rows
        ]
        await db.commit()
        return response
    except IdentityReviewError as exc:
        await _finish_failure(db, exc)
        raise _http_error(exc) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.get("/identity-review-cases/{case_id}")
async def get_identity_review_case(
    case_id: str,
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        case = await read_case(
            db,
            case_id=_uuid(case_id, code="IDENTITY_REVIEW_CASE_NOT_FOUND"),
            provider=provider,
            capability_token=x_consent_token,
        )
        response = await case_metadata(db, case=case, provider=provider)
        await db.commit()
        return response
    except IdentityReviewError as exc:
        await _finish_failure(db, exc)
        raise _http_error(exc) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/identity-review-cases/{case_id}/claim")
async def claim_identity_review_case(
    case_id: str,
    raw_payload: Any = Body(...),
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        payload = _payload(ClaimIdentityReviewCaseRequest, raw_payload)
        case = await claim_case(
            db,
            case_id=_uuid(case_id, code="IDENTITY_REVIEW_CASE_NOT_FOUND"),
            provider=provider,
            capability_token=x_consent_token,
            expected_version=payload.expected_version,
            idempotency_key=payload.idempotency_key,
        )
        response = await case_metadata(db, case=case, provider=provider)
        await db.commit()
        return response
    except IdentityReviewError as exc:
        await _finish_failure(db, exc)
        raise _http_error(exc) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/identity-review-cases/{case_id}/recover-session")
async def recover_identity_review_session(
    case_id: str,
    raw_payload: Any = Body(...),
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        payload = _payload(RecoverIdentityReviewSessionRequest, raw_payload)
        case = await recover_session(
            db,
            case_id=_uuid(case_id, code="IDENTITY_REVIEW_CASE_NOT_FOUND"),
            provider=provider,
            capability_token=x_consent_token,
            expected_version=payload.expected_version,
            idempotency_key=payload.idempotency_key,
        )
        response = await case_metadata(db, case=case, provider=provider)
        await db.commit()
        return response
    except IdentityReviewError as exc:
        await _finish_failure(db, exc)
        raise _http_error(exc) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/identity-review-cases/{case_id}/dispositions", status_code=201)
async def create_identity_review_disposition(
    case_id: str,
    raw_payload: Any = Body(...),
    provider: ProviderContext = Depends(get_current_provider),
    x_consent_token: str | None = Header(default=None, alias="X-Consent-Token"),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        payload = _payload(SubmitIdentityReviewDispositionRequest, raw_payload)
        disposition = await submit_disposition(
            db,
            case_id=_uuid(case_id, code="IDENTITY_REVIEW_CASE_NOT_FOUND"),
            provider=provider,
            capability_token=x_consent_token,
            expected_version=payload.expected_version,
            idempotency_key=payload.idempotency_key,
            outcome=payload.outcome,
            reason_codes=payload.reason_codes,
        )
        await db.commit()
        return {
            "disposition_id": str(disposition.id),
            "case_id": str(disposition.case_id),
            "outcome": disposition.outcome,
            "reason_codes": list(disposition.reason_codes),
            "submitted_at": disposition.submitted_at,
            "contract_version": disposition.contract_version,
            "policy_version": disposition.policy_version,
        }
    except IdentityReviewError as exc:
        await _finish_failure(db, exc)
        raise _http_error(exc) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


identity_review_v2_router = router

__all__ = ["identity_review_v2_router", "router"]

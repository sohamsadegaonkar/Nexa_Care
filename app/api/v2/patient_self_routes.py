"""Patient-self API routes for profile, legal acceptance, and onboarding.

Prefix: ``/api/v2/patient/me``

All routes use strict patient JWT/session dependencies. No biometric/session
fallback and no body/path/query patient ID override are accepted.

Transaction ownership: mutation routes explicitly commit on success and
roll back on failure. Read-only routes do not mutate state.

Crypto error mapping: endpoint-level exception handlers return minimal stable
error codes. No patient_id, DEK version, KMS key ID, AWS error, wrapped key,
ciphertext, provider subject, verified phone, or stack trace is returned.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.client_ip import resolve_client_ip
from app.core.config import ConfigError
from app.core.database import get_db_session
from app.core.dependencies import (
    AuthenticatedPatient,
    AuthenticatedPatientSession,
    get_current_patient,
    get_current_patient_session,
)
from app.core.rate_limiter import (
    OtpRateLimitBackendUnavailable,
    OtpRateLimitExceeded,
    OtpRedisRateLimiter,
)
from app.core.supabase import get_supabase_client
from app.services.crypto_kms import EncryptionError, PatientDataErased
from app.security.erasure_registry import ErasureRegistryUnavailable
from app.services.patient_auth_service import normalize_indian_phone
from app.services.patient_legal_service import (
    LegalAcceptanceError,
    get_legal_requirements,
    accept_legal_documents,
    get_onboarding_status,
)
from app.services.patient_phone_discoverability_service import (
    PatientPhoneDiscoverabilityError,
    disable_phone_discoverability,
    enable_phone_discoverability,
    get_phone_discoverability_state,
)
from app.services.patient_profile_service import (
    ProfileValidationError,
    create_or_update_profile,
    get_profile,
)

router = APIRouter(prefix="/api/v2/patient/me", tags=["patient-self"])
_phone_discoverability_otp_limiter = OtpRedisRateLimiter()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(..., min_length=1, max_length=200)
    date_of_birth: date


class ProfileResponse(BaseModel):
    full_name: str
    date_of_birth: str
    public_patient_id: str


class LegalRequirementResponse(BaseModel):
    document_type: str
    document_version: str
    document_sha256: str
    document_url: str
    accepted_current_version: bool


class LegalAcceptanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_types: list[str] = Field(..., min_length=1)


class OnboardingStatusResponse(BaseModel):
    profile_complete: bool
    terms_current: bool
    privacy_current: bool
    complete: bool
    next_step: str


class PhoneDiscoverabilityEnableRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phone: str = Field(..., min_length=10, max_length=32)
    otp: str = Field(..., pattern=r"^\d{6}$")


class PhoneDiscoverabilityResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool


# ---------------------------------------------------------------------------
# Safe error mapping
# ---------------------------------------------------------------------------


def _handle_crypto_error(exc: Exception) -> HTTPException:
    """Map crypto/erasure exceptions to minimal stable HTTP responses."""
    if isinstance(exc, PatientDataErased):
        return HTTPException(status_code=410, detail="PATIENT_DATA_ERASED")
    if isinstance(exc, ErasureRegistryUnavailable):
        return HTTPException(status_code=503, detail="ERASURE_REGISTRY_UNAVAILABLE")
    if isinstance(exc, EncryptionError):
        return HTTPException(status_code=503, detail="ENCRYPTION_SERVICE_UNAVAILABLE")
    return HTTPException(status_code=503, detail="ENCRYPTION_SERVICE_UNAVAILABLE")


def _handle_legal_error(exc: LegalAcceptanceError) -> HTTPException:
    """Map legal acceptance errors to HTTP responses."""
    if exc.code == "LEGAL_DOCUMENT_VERSION_DIGEST_CONFLICT":
        return HTTPException(status_code=409, detail=exc.code)
    if exc.code == "LEGAL_CONFIG_UNAVAILABLE":
        return HTTPException(status_code=503, detail=exc.code)
    if exc.code == "UNSUPPORTED_DOCUMENT_TYPE":
        return HTTPException(status_code=422, detail=exc.code)
    if exc.code == "NO_DOCUMENT_TYPES":
        return HTTPException(status_code=422, detail=exc.code)
    return HTTPException(status_code=500, detail="INTERNAL_ERROR")


def _phone_discoverability_http_error(code: str) -> HTTPException:
    if code == "PHONE_DISCOVERABILITY_CONFLICT":
        return HTTPException(
            status_code=409,
            detail={"error_code": code, "retryable": False},
        )
    if code == "PHONE_DISCOVERABILITY_IDENTITY_INVALID":
        return HTTPException(status_code=401, detail={"error_code": code})
    return HTTPException(
        status_code=503,
        detail={"error_code": "PHONE_DISCOVERABILITY_UNAVAILABLE", "retryable": True},
    )


def _normalize_discoverability_phone(phone: str) -> str:
    try:
        return normalize_indian_phone(phone)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "INVALID_PHONE_FORMAT"},
        ) from exc


def _validated_verified_phone(
    result: object, *, submitted_phone: str, expected_subject: str
) -> str:
    user = getattr(result, "user", None)
    session = getattr(result, "session", None)
    provider_phone = getattr(user, "phone", None)
    provider_subject = getattr(user, "id", None)
    provider_access_token = getattr(session, "access_token", None)
    if not provider_phone or not provider_subject or not provider_access_token:
        raise HTTPException(
            status_code=401,
            detail={"error_code": "PHONE_DISCOVERABILITY_VERIFICATION_FAILED"},
        )
    try:
        authoritative_phone = normalize_indian_phone(str(provider_phone))
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail={"error_code": "PHONE_DISCOVERABILITY_VERIFICATION_FAILED"},
        ) from None
    if authoritative_phone != submitted_phone or str(provider_subject) != expected_subject:
        raise HTTPException(
            status_code=401,
            detail={"error_code": "PHONE_DISCOVERABILITY_VERIFICATION_FAILED"},
        )
    return authoritative_phone


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/profile", response_model=ProfileResponse)
async def read_profile(
    auth: AuthenticatedPatient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Read the authenticated patient's decrypted profile."""
    try:
        data = await get_profile(auth.patient_id, db)
    except (
        PatientDataErased,
        ErasureRegistryUnavailable,
        EncryptionError,
        ConfigError,
    ) as exc:
        raise _handle_crypto_error(exc) from None

    if data is None:
        raise HTTPException(status_code=404, detail="PROFILE_NOT_FOUND")

    return {
        "full_name": data.full_name,
        "date_of_birth": data.date_of_birth,
        "public_patient_id": auth.patient.public_patient_id,
    }


@router.put("/profile", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    auth: AuthenticatedPatient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Create or update the authenticated patient's encrypted profile."""
    try:
        data, _created = await create_or_update_profile(
            auth.patient_id, body.full_name, body.date_of_birth, db
        )
        await db.commit()
    except ProfileValidationError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=exc.code) from None
    except (
        PatientDataErased,
        ErasureRegistryUnavailable,
        EncryptionError,
        ConfigError,
    ) as exc:
        await db.rollback()
        raise _handle_crypto_error(exc) from None
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=503, detail="ENCRYPTION_SERVICE_UNAVAILABLE"
        ) from None

    return {
        "full_name": data.full_name,
        "date_of_birth": data.date_of_birth,
        "public_patient_id": auth.patient.public_patient_id,
    }


@router.get("/legal-requirements", response_model=list[LegalRequirementResponse])
async def read_legal_requirements(
    auth: AuthenticatedPatient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Return server-authoritative legal document requirements."""
    try:
        requirements = await get_legal_requirements(auth.patient_id, db)
    except LegalAcceptanceError as exc:
        raise _handle_legal_error(exc) from None

    return [
        {
            "document_type": r.document_type,
            "document_version": r.document_version,
            "document_sha256": r.document_sha256,
            "document_url": r.document_url,
            "accepted_current_version": r.accepted_current_version,
        }
        for r in requirements
    ]


@router.post("/legal-acceptances", response_model=list[LegalRequirementResponse])
async def accept_legal(
    body: LegalAcceptanceRequest,
    auth: AuthenticatedPatient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Accept one or more legal documents atomically."""
    try:
        requirements = await accept_legal_documents(
            auth.patient_id, body.document_types, db
        )
        await db.commit()
    except LegalAcceptanceError as exc:
        await db.rollback()
        raise _handle_legal_error(exc) from None
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=503, detail="LEGAL_CONFIG_UNAVAILABLE"
        ) from None

    return [
        {
            "document_type": r.document_type,
            "document_version": r.document_version,
            "document_sha256": r.document_sha256,
            "document_url": r.document_url,
            "accepted_current_version": r.accepted_current_version,
        }
        for r in requirements
    ]


@router.get("/onboarding-status", response_model=OnboardingStatusResponse)
async def read_onboarding_status(
    auth: AuthenticatedPatient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Return server-derived patient onboarding status."""
    try:
        status = await get_onboarding_status(auth.patient_id, db)
    except LegalAcceptanceError as exc:
        raise _handle_legal_error(exc) from None

    return {
        "profile_complete": status.profile_complete,
        "terms_current": status.terms_current,
        "privacy_current": status.privacy_current,
        "complete": status.complete,
        "next_step": status.next_step,
    }


@router.get(
    "/discoverability/phone", response_model=PhoneDiscoverabilityResponse
)
async def read_phone_discoverability(
    auth: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
) -> PhoneDiscoverabilityResponse:
    """Return only whether phone lookup is enabled; never return the phone."""
    try:
        state = await get_phone_discoverability_state(
            db,
            patient_id=UUID(auth.patient_id),
            provider_subject=auth.supabase_user_id,
        )
    except PatientPhoneDiscoverabilityError as exc:
        raise _phone_discoverability_http_error(exc.code) from None
    return PhoneDiscoverabilityResponse(enabled=state.enabled)


@router.post(
    "/discoverability/phone/enable", response_model=PhoneDiscoverabilityResponse
)
async def enable_phone_discoverability_route(
    body: PhoneDiscoverabilityEnableRequest,
    request: Request,
    auth: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
) -> PhoneDiscoverabilityResponse:
    """Opt in using a fresh OTP from the same authoritative Supabase identity."""
    phone = _normalize_discoverability_phone(body.phone)
    try:
        await _phone_discoverability_otp_limiter.check(
            action="verify",
            ip=resolve_client_ip(request) or "unknown",
            normalized_phone=phone,
        )
    except OtpRateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail={"error_code": "PHONE_DISCOVERABILITY_RATE_LIMITED"},
        ) from exc
    except OtpRateLimitBackendUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "PHONE_DISCOVERABILITY_SECURITY_CONTROL_UNAVAILABLE",
                "retryable": True,
            },
        ) from exc

    try:
        provider_result = await run_in_threadpool(
            get_supabase_client().auth.verify_otp,
            {"phone": phone, "token": body.otp, "type": "sms"},
        )
    except Exception as exc:
        code = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if code in {400, 401, 403}:
            raise HTTPException(
                status_code=401,
                detail={"error_code": "PHONE_DISCOVERABILITY_VERIFICATION_FAILED"},
            ) from None
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "PHONE_DISCOVERABILITY_VERIFICATION_UNAVAILABLE",
                "retryable": True,
            },
        ) from None

    verified_phone = _validated_verified_phone(
        provider_result,
        submitted_phone=phone,
        expected_subject=auth.supabase_user_id,
    )

    try:
        state = await enable_phone_discoverability(
            db,
            patient_id=UUID(auth.patient_id),
            provider_subject=auth.supabase_user_id,
            verified_phone=verified_phone,
        )
        await db.commit()
    except PatientPhoneDiscoverabilityError as exc:
        if exc.code == "PHONE_DISCOVERABILITY_CONFLICT":
            try:
                # The conflict path deliberately quarantines all implicated
                # search rows. Persist that fail-closed state before returning.
                await db.commit()
            except Exception:
                await db.rollback()
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error_code": "PHONE_DISCOVERABILITY_UNAVAILABLE",
                        "retryable": True,
                    },
                ) from None
            raise _phone_discoverability_http_error(exc.code) from None
        await db.rollback()
        raise _phone_discoverability_http_error(exc.code) from None
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "PHONE_DISCOVERABILITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from None

    return PhoneDiscoverabilityResponse(enabled=state.enabled)


@router.delete(
    "/discoverability/phone", response_model=PhoneDiscoverabilityResponse
)
async def disable_phone_discoverability_route(
    auth: AuthenticatedPatientSession = Depends(get_current_patient_session),
    db: AsyncSession = Depends(get_db_session),
) -> PhoneDiscoverabilityResponse:
    """Opt out without requiring the phone value or another OTP."""
    try:
        state = await disable_phone_discoverability(
            db,
            patient_id=UUID(auth.patient_id),
            provider_subject=auth.supabase_user_id,
        )
        await db.commit()
    except PatientPhoneDiscoverabilityError as exc:
        await db.rollback()
        raise _phone_discoverability_http_error(exc.code) from None
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "PHONE_DISCOVERABILITY_UNAVAILABLE",
                "retryable": True,
            },
        ) from None
    return PhoneDiscoverabilityResponse(enabled=state.enabled)

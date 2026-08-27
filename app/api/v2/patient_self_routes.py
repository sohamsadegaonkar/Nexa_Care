"""Patient-self API routes for profile, legal acceptance, and onboarding.

Prefix: ``/api/v2/patient/me``

All routes use the strict ``get_current_patient`` JWT-only dependency.
No biometric/session fallback.  No body/path/query patient ID override.

Transaction ownership: mutation routes explicitly commit on success and
roll back on failure.  Read-only routes do not mutate state.

Crypto error mapping: endpoint-level exception handlers return minimal
stable error codes.  No patient_id, DEK version, KMS key ID, AWS error,
wrapped key, ciphertext, provider subject, or stack trace in responses.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ConfigError
from app.core.database import get_db_session
from app.core.dependencies import AuthenticatedPatient, get_current_patient
from app.services.crypto_kms import EncryptionError, PatientDataErased
from app.security.erasure_registry import ErasureRegistryUnavailable
from app.services.patient_legal_service import (
    LegalAcceptanceError,
    get_legal_requirements,
    accept_legal_documents,
    get_onboarding_status,
)
from app.services.patient_profile_service import (
    ProfileValidationError,
    create_or_update_profile,
    get_profile,
)

router = APIRouter(prefix="/api/v2/patient/me", tags=["patient-self"])


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


# ---------------------------------------------------------------------------
# Safe crypto error mapping
# ---------------------------------------------------------------------------


def _handle_crypto_error(exc: Exception) -> HTTPException:
    """Map crypto/erasure exceptions to minimal stable HTTP responses.

    NEVER include patient_id, DEK version, KMS key ID, AWS error,
    wrapped key, ciphertext, provider subject, or stack trace.
    """
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/profile", response_model=ProfileResponse)
async def read_profile(
    auth: AuthenticatedPatient = Depends(get_current_patient),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Read the authenticated patient's decrypted profile.

    Strictly read-only — NEVER provisions DEKs.
    """
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
    """Create or update the authenticated patient's encrypted profile.

    Explicit transaction ownership: commits on success, rolls back on any failure.
    """
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
    """Accept one or more legal documents atomically.

    Explicit transaction ownership: commits on success, rolls back entirely
    on any failure (including partial conflict on one document type).
    """
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

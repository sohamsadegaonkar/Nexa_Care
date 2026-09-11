"""Patient-safe status and reviewer operations for registration-recovery cases."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.registration_recovery_review_gate import (
    RegistrationRecoveryReviewer,
    get_registration_recovery_reviewer,
)
from app.models.patient_registration_recovery_review import (
    RegistrationRecoveryReviewOutcome,
    RegistrationRecoveryReviewReason,
    RegistrationRecoveryReviewStatus,
)
from app.services.patient_registration_recovery_review_service import (
    REGISTRATION_RECOVERY_REVIEW_ACCESS_DENIED,
    REGISTRATION_RECOVERY_REVIEW_ALREADY_RESOLVED,
    REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT,
    REGISTRATION_RECOVERY_REVIEW_CASE_NOT_FOUND,
    REGISTRATION_RECOVERY_REVIEW_IDEMPOTENCY_CONFLICT,
    REGISTRATION_RECOVERY_REVIEW_PAYLOAD_INVALID,
    REGISTRATION_RECOVERY_REVIEW_REPAIR_NOT_AUTHORIZED,
    REGISTRATION_RECOVERY_REVIEW_SESSION_MISMATCH,
    REGISTRATION_RECOVERY_REVIEW_STATE_CHANGED,
    REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT,
    PatientRegistrationRecoveryReviewError,
    claim_reviewer_case,
    list_reviewer_cases,
    patient_review_status,
    read_reviewer_case,
    recover_reviewer_session,
    resolve_reviewer_case,
    reviewer_case_metadata,
)

router = APIRouter(
    prefix="/api/v2/auth/registration-recovery/review",
    tags=["registration-recovery-review"],
)


class PatientReviewStatusResponse(BaseModel):
    case_reference: str
    status: RegistrationRecoveryReviewStatus
    terminal: bool
    next_action: str
    created_at: datetime
    resolved_at: datetime | None = None


class ReviewerCaseResponse(BaseModel):
    case_reference: str
    patient_id: str | None = None
    status: RegistrationRecoveryReviewStatus
    reason_codes: list[RegistrationRecoveryReviewReason]
    version: int
    assigned_to_current_reviewer: bool
    created_at: datetime
    claimed_at: datetime | None = None
    resolved_at: datetime | None = None
    contract_version: str
    policy_version: str


class ReviewerCaseListResponse(BaseModel):
    cases: list[ReviewerCaseResponse]


class ReviewerMutationRequest(BaseModel):
    expected_version: int = Field(..., ge=1)


class ReviewerResolveRequest(ReviewerMutationRequest):
    idempotency_key: str = Field(..., min_length=8, max_length=192)
    outcome: RegistrationRecoveryReviewOutcome
    reason_codes: list[RegistrationRecoveryReviewReason] = Field(
        ..., min_length=1, max_length=4
    )


class ReviewerResolveResponse(ReviewerCaseResponse):
    outcome: RegistrationRecoveryReviewOutcome


def _review_http_error(exc: PatientRegistrationRecoveryReviewError) -> HTTPException:
    if exc.code == REGISTRATION_RECOVERY_REVIEW_CASE_NOT_FOUND:
        http_status = status.HTTP_404_NOT_FOUND
    elif exc.code == REGISTRATION_RECOVERY_REVIEW_ACCESS_DENIED:
        http_status = status.HTTP_403_FORBIDDEN
    elif exc.code == REGISTRATION_RECOVERY_REVIEW_PAYLOAD_INVALID:
        http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
    elif exc.code in {
        REGISTRATION_RECOVERY_REVIEW_ALREADY_RESOLVED,
        REGISTRATION_RECOVERY_REVIEW_CASE_CONFLICT,
        REGISTRATION_RECOVERY_REVIEW_IDEMPOTENCY_CONFLICT,
        REGISTRATION_RECOVERY_REVIEW_REPAIR_NOT_AUTHORIZED,
        REGISTRATION_RECOVERY_REVIEW_SESSION_MISMATCH,
        REGISTRATION_RECOVERY_REVIEW_STATE_CHANGED,
        REGISTRATION_RECOVERY_REVIEW_VERSION_CONFLICT,
    }:
        http_status = status.HTTP_409_CONFLICT
    else:
        http_status = status.HTTP_409_CONFLICT
    return HTTPException(status_code=http_status, detail={"error_code": exc.code})


async def _rollback_and_raise(
    db: AsyncSession, exc: PatientRegistrationRecoveryReviewError
) -> None:
    await db.rollback()
    raise _review_http_error(exc) from None


@router.get(
    "/cases/{case_reference}",
    response_model=PatientReviewStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def registration_recovery_review_status(
    case_reference: str,
    db: AsyncSession = Depends(get_db_session),
) -> PatientReviewStatusResponse:
    """Poll one opaque case handle without exposing reviewer or graph internals."""

    try:
        payload = await patient_review_status(db, case_reference=case_reference)
        await db.rollback()
        return PatientReviewStatusResponse(**payload)
    except PatientRegistrationRecoveryReviewError as exc:
        await _rollback_and_raise(db, exc)
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_REVIEW_UNAVAILABLE",
                "retryable": True,
            },
        ) from None


@router.get(
    "/reviewer/cases",
    response_model=ReviewerCaseListResponse,
    status_code=status.HTTP_200_OK,
)
async def registration_recovery_reviewer_cases(
    case_status: RegistrationRecoveryReviewStatus | None = Query(
        default=None, alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=100),
    reviewer: RegistrationRecoveryReviewer = Depends(
        get_registration_recovery_reviewer
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ReviewerCaseListResponse:
    try:
        rows = await list_reviewer_cases(
            db,
            reviewer=reviewer,
            status_filter=case_status,
            limit=limit,
        )
        await db.rollback()
        return ReviewerCaseListResponse(cases=[ReviewerCaseResponse(**row) for row in rows])
    except PatientRegistrationRecoveryReviewError as exc:
        await _rollback_and_raise(db, exc)
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_REVIEW_UNAVAILABLE",
                "retryable": True,
            },
        ) from None


@router.get(
    "/reviewer/cases/{case_reference}",
    response_model=ReviewerCaseResponse,
    status_code=status.HTTP_200_OK,
)
async def registration_recovery_reviewer_case(
    case_reference: str,
    reviewer: RegistrationRecoveryReviewer = Depends(
        get_registration_recovery_reviewer
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ReviewerCaseResponse:
    try:
        row = await read_reviewer_case(
            db, case_reference=case_reference, reviewer=reviewer
        )
        await db.rollback()
        return ReviewerCaseResponse(**row)
    except PatientRegistrationRecoveryReviewError as exc:
        await _rollback_and_raise(db, exc)
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_REVIEW_UNAVAILABLE",
                "retryable": True,
            },
        ) from None


@router.post(
    "/reviewer/cases/{case_reference}/claim",
    response_model=ReviewerCaseResponse,
    status_code=status.HTTP_200_OK,
)
async def registration_recovery_reviewer_claim(
    case_reference: str,
    payload: ReviewerMutationRequest,
    reviewer: RegistrationRecoveryReviewer = Depends(
        get_registration_recovery_reviewer
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ReviewerCaseResponse:
    try:
        case = await claim_reviewer_case(
            db,
            case_reference=case_reference,
            reviewer=reviewer,
            expected_version=payload.expected_version,
        )
        await db.commit()
        return ReviewerCaseResponse(**reviewer_case_metadata(case, reviewer=reviewer))
    except PatientRegistrationRecoveryReviewError as exc:
        await _rollback_and_raise(db, exc)
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_REVIEW_UNAVAILABLE",
                "retryable": True,
            },
        ) from None


@router.post(
    "/reviewer/cases/{case_reference}/recover-session",
    response_model=ReviewerCaseResponse,
    status_code=status.HTTP_200_OK,
)
async def registration_recovery_reviewer_recover_session(
    case_reference: str,
    payload: ReviewerMutationRequest,
    reviewer: RegistrationRecoveryReviewer = Depends(
        get_registration_recovery_reviewer
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ReviewerCaseResponse:
    try:
        case = await recover_reviewer_session(
            db,
            case_reference=case_reference,
            reviewer=reviewer,
            expected_version=payload.expected_version,
        )
        await db.commit()
        return ReviewerCaseResponse(**reviewer_case_metadata(case, reviewer=reviewer))
    except PatientRegistrationRecoveryReviewError as exc:
        await _rollback_and_raise(db, exc)
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_REVIEW_UNAVAILABLE",
                "retryable": True,
            },
        ) from None


@router.post(
    "/reviewer/cases/{case_reference}/resolve",
    response_model=ReviewerResolveResponse,
    status_code=status.HTTP_200_OK,
)
async def registration_recovery_reviewer_resolve(
    case_reference: str,
    payload: ReviewerResolveRequest,
    reviewer: RegistrationRecoveryReviewer = Depends(
        get_registration_recovery_reviewer
    ),
    db: AsyncSession = Depends(get_db_session),
) -> ReviewerResolveResponse:
    try:
        case, disposition = await resolve_reviewer_case(
            db,
            case_reference=case_reference,
            reviewer=reviewer,
            expected_version=payload.expected_version,
            idempotency_key=payload.idempotency_key,
            outcome=payload.outcome,
            reason_codes=payload.reason_codes,
        )
        await db.commit()
        row = reviewer_case_metadata(case, reviewer=reviewer)
        return ReviewerResolveResponse(**row, outcome=disposition.outcome)
    except PatientRegistrationRecoveryReviewError as exc:
        await _rollback_and_raise(db, exc)
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": "REGISTRATION_RECOVERY_REVIEW_UNAVAILABLE",
                "retryable": True,
            },
        ) from None

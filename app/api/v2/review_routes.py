"""Human-in-the-loop document review routes for Nexa Care V2."""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

import json
import logging
from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_provider_context
from app.models.ai_models import ExtractedMedicalDocument
from app.models.document_review import DocumentReviewQueue, DocumentReviewStatus
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log, append_audit_log_or_503

logger = logging.getLogger("nexa_logger")

router = APIRouter(prefix="/api/v2/reviews", tags=["reviews"])


async def _audit_best_effort(actor_uid: str, event_type: str, target_id: str, status_: str) -> None:
    """Best-effort audit write for failure-path logging.

    Hard-failing (append_audit_log_or_503) is correct for the pre-mutation
    ATTEMPT and post-mutation SUCCESS audits below -- an approval/rejection
    whose audit entry can't be written must not be allowed to start, or to
    be reported as successful. It would be WRONG here: this runs inside an
    `except` block where a *real* failure (the DB write itself failed) is
    already being reported to the caller. Raising a fresh
    HTTPException(503) from inside that path would silently replace the
    true error with an unrelated one. So this logs loudly on failure
    instead of raising -- matching the convention in app/api/routes.py.
    """
    success = await append_audit_log(
        audit_context=current_audit_context(AuditDomain.PIPELINE),
        actor_uid=actor_uid, event_type=event_type, target_id=target_id, status=status_,
    )
    if not success:
        logger.critical(json.dumps({
            "event": "audit_log_write_failed_best_effort",
            "context": event_type,
            "target_id": target_id,
        }))


class DocumentReviewItem(BaseModel):
    """Review queue item returned to the uploading provider."""

    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: UUID
    provider_uid: str
    status: str
    confidence_score: float
    extracted_data: dict[str, JsonValue]
    created_at: datetime
    updated_at: datetime


class PendingReviewsResponse(BaseModel):
    """Pending review collection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reviews: list[DocumentReviewItem] = Field(default_factory=list)


class ReviewStatusResponse(BaseModel):
    """Mutation response for a review queue item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    review_id: UUID
    status: str


async def _load_owned_pending_review(
    db: AsyncSession,
    review_id: UUID,
    provider_uid: str,
) -> DocumentReviewQueue:
    stmt = select(DocumentReviewQueue).where(
        DocumentReviewQueue.id == review_id,
        DocumentReviewQueue.provider_uid == provider_uid,
        DocumentReviewQueue.status == DocumentReviewStatus.PENDING.value,
    )
    result = await db.execute(stmt)
    review = result.scalar_one_or_none()
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending review not found.",
        )
    return review


@router.get("/pending", response_model=PendingReviewsResponse)
async def list_pending_reviews(
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
) -> PendingReviewsResponse:
    """List pending AI extraction reviews owned by the authenticated provider."""

    stmt = select(DocumentReviewQueue).where(
        DocumentReviewQueue.provider_uid == provider.actor_uid,
        DocumentReviewQueue.status == DocumentReviewStatus.PENDING.value,
    )
    result = await db.execute(stmt)
    reviews = result.scalars().all()
    return PendingReviewsResponse(
        reviews=[DocumentReviewItem.model_validate(review) for review in reviews]
    )


@router.post("/{review_id}/approve", response_model=ReviewStatusResponse)
async def approve_review(
    review_id: UUID,
    corrected_data: ExtractedMedicalDocument,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
) -> ReviewStatusResponse:
    """Retired unbound review path; staged field review is authoritative."""
    _ = (corrected_data, provider, db)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "error_code": "LEGACY_DOCUMENT_REVIEW_RETIRED",
            "message": "Use /api/v2/pipeline/fields/{field_id}/review.",
        },
    )


@router.post("/{review_id}/reject", response_model=ReviewStatusResponse)
async def reject_review(
    review_id: UUID,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
) -> ReviewStatusResponse:
    """Reject a pending AI extraction review without writing primary shards."""

    review = await _load_owned_pending_review(db, review_id, provider.actor_uid)

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PIPELINE),
        actor_uid=provider.actor_uid,
        event_type="DOCUMENT_REVIEW_REJECTION_ATTEMPT",
        target_id=str(review.id),
        status="STARTED",
        metadata={
            "review_id": str(review.id),
            "provider_uid": provider.actor_uid,
        },
    )

    review.status = DocumentReviewStatus.REJECTED.value
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        await _audit_best_effort(
            actor_uid=provider.actor_uid,
            event_type="DOCUMENT_REVIEW_REJECTION_FAILED",
            target_id=str(review.id),
            status_="FAILED",
        )
        raise

    await append_audit_log_or_503(
        audit_context=current_audit_context(AuditDomain.PIPELINE),
        actor_uid=provider.actor_uid,
        event_type="DOCUMENT_REVIEW_REJECTED",
        target_id=str(review.id),
        status="REJECTED",
        metadata={
            "review_id": str(review.id),
            "provider_uid": provider.actor_uid,
        },
    )

    return ReviewStatusResponse(review_id=review.id, status=review.status)

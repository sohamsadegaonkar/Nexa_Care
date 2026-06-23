"""Human-in-the-loop document review routes for Nexa Care V2."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.pipeline import _persist_auto_processed_document
from app.core.database import get_db_session
from app.core.dependencies import get_provider_context
from app.models.ai_models import ExtractedMedicalDocument
from app.models.document_review import DocumentReviewQueue, DocumentReviewStatus
from app.models.provider_context import ProviderContext
from app.observability.audit_ledger import append_audit_log_or_503
from app.services.sharding import split_pii_and_clinical_fields

router = APIRouter(prefix="/api/v2/reviews", tags=["reviews"])


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
    """Approve corrected AI extraction data and persist it to primary shards."""

    review = await _load_owned_pending_review(db, review_id, provider.actor_uid)
    payload = corrected_data.model_dump(exclude={"extraction_confidence"})
    vault_payload, clinical_payload, unrecognized_payload = split_pii_and_clinical_fields(payload)
    if unrecognized_payload:
        vault_payload.update(unrecognized_payload)

    masked_internal_id = str(uuid4())
    try:
        await _persist_auto_processed_document(
            db=db,
            masked_internal_id=masked_internal_id,
            vault_payload=vault_payload,
            clinical_payload=clinical_payload,
            commit=False,
        )
        review.status = DocumentReviewStatus.APPROVED.value
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    await append_audit_log_or_503(
        actor_uid=provider.actor_uid,
        event_type="DOCUMENT_REVIEW_APPROVED",
        target_id=str(review.id),
        status="APPROVED",
        metadata={
            "review_id": str(review.id),
            "masked_internal_id": masked_internal_id,
            "provider_uid": provider.actor_uid,
        },
    )

    return ReviewStatusResponse(review_id=review.id, status=review.status)


@router.post("/{review_id}/reject", response_model=ReviewStatusResponse)
async def reject_review(
    review_id: UUID,
    provider: ProviderContext = Depends(get_provider_context),
    db: AsyncSession = Depends(get_db_session),
) -> ReviewStatusResponse:
    """Reject a pending AI extraction review without writing primary shards."""

    review = await _load_owned_pending_review(db, review_id, provider.actor_uid)
    review.status = DocumentReviewStatus.REJECTED.value
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    await append_audit_log_or_503(
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

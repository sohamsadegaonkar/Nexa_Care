"""Document AI human-review queue ORM model."""

from __future__ import annotations

import enum

from sqlalchemy import Float, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DocumentReviewStatus(str, enum.Enum):
    """Lifecycle states for queued AI extraction reviews."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DocumentReviewQueue(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Holds medium-confidence AI extraction output pending provider review."""

    __tablename__ = "document_review_queue"

    provider_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DocumentReviewStatus.PENDING.value,
        server_default=DocumentReviewStatus.PENDING.value,
    )
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    extracted_data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_document_review_queue_provider_uid", "provider_uid"),
        Index("ix_document_review_queue_status", "status"),
    )

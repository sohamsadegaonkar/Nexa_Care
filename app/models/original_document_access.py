"""Patient decision about one retained original; no source bytes or credentials."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OriginalDocumentAccess(Base):
    __tablename__ = "original_document_access"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    patient_id: Mapped[UUID] = mapped_column(
        ForeignKey("patients.patient_uuid", ondelete="CASCADE")
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_references.id", ondelete="CASCADE")
    )
    provider_id: Mapped[UUID]
    hospital_id: Mapped[UUID]
    history_id: Mapped[UUID]
    session_hash: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    nonce: Mapped[str] = mapped_column(String(64), unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12))
    key_id: Mapped[UUID | None]
    signature: Mapped[bytes | None]

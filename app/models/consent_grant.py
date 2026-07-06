"""Durable Postgres audit trail for ConsentEngine grants.

See app/services/consent_engine.py for the full design rationale. In short:
every consent grant is dual-written -- a live, bearer-token-keyed capability
in Redis (fast validate/consume on the request path) and a durable row here,
keyed by a SHA-256 hash of the bearer token rather than the raw token itself.
The raw token is never persisted to Postgres: a database dump or backup leak
must not be able to hand out a live capability, only prove that some grant
existed and record its lifecycle (issued -> consumed/revoked/expired).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ConsentGrantLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per consent grant issued by ConsentEngine.issue().

    ``patient_id`` and ``clinician_id`` are opaque string identifiers
    (masked internal IDs / provider UIDs), matching the convention used by
    ``NexaVault.masked_internal_id`` elsewhere -- not DB-level UUID columns.
    """

    __tablename__ = "consent_grant_log"

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    patient_id: Mapped[str] = mapped_column(String(64), nullable=False)
    clinician_id: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[list] = mapped_column(JSONB, nullable=False)
    is_break_glass: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    reason_code: Mapped[str | None] = mapped_column(String(256), nullable=True)

    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)

    assurance_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    assurance_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_consent_grant_log_token_hash"),
        CheckConstraint(
            "NOT is_break_glass OR reason_code IS NOT NULL",
            name="ck_consent_grant_log_break_glass_requires_reason",
        ),
        Index("ix_consent_grant_log_token_hash", "token_hash"),
        Index("ix_consent_grant_log_patient_id", "patient_id"),
        Index("ix_consent_grant_log_clinician_id", "clinician_id"),
    )
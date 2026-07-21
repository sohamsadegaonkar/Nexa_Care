"""Canonical ExtractedField Schema for Nexa Care V2 (Workstreams 4, 5, 8).

Single source of truth for AI pipeline extracted observation schemas.
Enforces strict confidence and risk level metadata rules.
"""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator


class ValidationResult(BaseModel):
    """Range check and clinical validity diagnostic results."""

    is_valid: bool = True
    has_conflict: bool = False
    checks: list[dict[str, Any]] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    reference_range: dict[str, Any] | None = None


class ExtractedField(BaseModel):
    """Authoritative schema representing an atomic data point extracted by AI pipeline."""

    field_id: str | None = None
    job_id: str | None = None
    field_name: str
    raw_value: str
    normalized_value: str | None = None
    units: str | None = None
    confidence: float | None = None
    risk_level: (
        Literal["LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "CRITICAL_RISK"] | str | None
    ) = None
    validation_result: ValidationResult | dict[str, Any] | None = None
    source_page: int | None = None
    source_bbox: list[float] | None = None
    status: Literal["needs_review", "approved", "rejected", "edited"] | str = (
        "needs_review"
    )
    corrected_value: str | None = None
    source_document_id: str | None = None
    has_conflict: bool = False

    @field_validator("confidence", mode="before")
    @classmethod
    def reject_boolean_confidence(cls, value):
        if isinstance(value, bool):
            raise ValueError("confidence must be numeric, not boolean")
        return value

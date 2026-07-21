"""Canonical API Contract Routes for Nexa Care V2 Alpha Milestone.

Implements the exact endpoints specified in docs/API-CONTRACTS.md, enforcing
mandatory dual-gating (Provider Auth + Zero-Trust require_consent dependency).
"""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

logger = logging.getLogger("nexa_logger")

router = APIRouter(tags=["contracts"])


# ── Request / Response Pydantic Models ───────────────────────────────────────


class ConsentChallengeRequest(BaseModel):
    patient_id: str
    provider_id: str
    purpose: str
    scope: str


class SignedApprovalRequest(BaseModel):
    request_id: str
    patient_id: str
    decision: str
    challenge_nonce: str
    signature: str
    device_id: str


class AppendVitalsRequest(BaseModel):
    encounter_id: str
    systolic_bp: int
    diastolic_bp: int
    heart_rate: int
    temperature_celsius: float
    sp_o2_percentage: int
    recorded_at: str
    source: str = "manual"
    confidence: float | None = None
    risk_level: str = "LOW_RISK"
    source_document_id: str | None = None


class AppendMedicationRequest(BaseModel):
    name: str
    strength: str
    frequency: str
    prescribed_at: str
    source: str = "manual"
    confidence: float | None = None
    risk_level: str = "MEDIUM_RISK"
    source_document_id: str | None = None


class AppendLabResultRequest(BaseModel):
    test_name: str
    value: str
    unit: str
    reference_range: str
    is_abnormal: bool = False
    recorded_at: str
    source: str = "manual"
    confidence: float | None = None
    risk_level: str = "MEDIUM_RISK"
    source_document_id: str | None = None


class AppendAllergyRequest(BaseModel):
    allergen: str
    severity: str
    source: str = "manual"
    confidence: float | None = None
    risk_level: str = "HIGH_RISK"
    source_document_id: str | None = None


def _validate_provenance(
    source: str, confidence: float | None, risk_level: str, source_doc: str | None
) -> None:
    if source == "ai_extracted":
        if (
            confidence is None
            or not (0.0 <= confidence <= 1.0)
            or not risk_level
            or not source_doc
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="AI-extracted field must have numeric confidence, risk_level, and source_document_id",
            )


class FieldReviewRequest(BaseModel):
    action: str
    corrected_value: str | None = None
    review_notes: str | None = None


class CommitJobRequest(BaseModel):
    patient_id: str
    encounter_summary: str | None = None
    fields: list[dict[str, Any]] | None = None


# ── 3. Patient Records Endpoints (Implemented in patient_record_routes.py) ──


# ── 4. AI Pipeline & Ingestion Endpoints (Implemented in pipeline_routes.py) ──

"""Typed AI extraction payloads for Nexa Care document processing."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ExtractedMedicalDocument(BaseModel):
    """Strict boundary between ML output and downstream shard routing.

    Extra keys are allowed so unexpected model fields are not silently lost;
    the pipeline's sharding step can route unrecognized values to the vault by
    default instead of guessing that they are safe clinical data.
    """

    model_config = ConfigDict(strict=True, extra="allow")

    patient_name: str = Field(..., description="Patient name extracted from the document")
    aadhaar_abha_id: str = Field(..., description="Aadhaar or ABHA identifier if present")
    phone: str = Field(..., description="Indian phone number if present")
    diagnoses: list[str] = Field(..., description="Diagnoses or clinical impressions")
    lab_results: list[str] = Field(..., description="Lab result summaries")
    prescriptions: list[str] = Field(..., description="Medication or prescription entries")

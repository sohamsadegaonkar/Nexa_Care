"""Typed AI extraction payloads for Nexa Care document processing."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from app.models.field_evidence import NormalizedBoundingBox


class ProviderFieldEvidence(BaseModel):
    """Provider-authentic candidate and its field-level source evidence."""

    model_config = ConfigDict(strict=True, extra="forbid")

    canonical_field_name: str
    raw_value: str
    source_text: str | None = None
    page_number: int | None = Field(default=None, ge=0)
    bounding_box: NormalizedBoundingBox | None = None
    field_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    provider_name: str
    provider_api_version: str
    extraction_timestamp: datetime
    evidence_hash: str | None = None
    source_type: Literal["QUERY_RESULT", "KEY_VALUE_SET", "CELL"] | None = None
    source_block_ids: tuple[str, ...] = ()
    supporting_evidence_hashes: tuple[str, ...] = ()
    supporting_source_block_ids: tuple[str, ...] = ()
    normalized_value: str | None = None
    raw_unit: str | None = None
    normalized_unit: str | None = None
    reference_range: str | None = None
    structured_value: dict[str, str | bool | None] | None = None
    incomplete: bool = False

    _trusted_clinical_fact_id: str | None = PrivateAttr(default=None)

    @property
    def trusted_clinical_fact_id(self) -> str | None:
        """Return a Nexa-owned fact identity that is never accepted from JSON."""

        return self._trusted_clinical_fact_id

    def _bind_trusted_clinical_fact_id(self, value: str | None) -> None:
        """Bind deterministic parser context at the trusted in-process boundary."""

        if value is None:
            return
        if self._trusted_clinical_fact_id not in {None, value}:
            raise ValueError("Trusted clinical fact identity is immutable")
        self._trusted_clinical_fact_id = value


class ExtractedMedicalDocument(BaseModel):
    """Strict boundary between remote AI output and shard persistence.

    Extra keys are allowed so unexpected model fields are not silently lost;
    the pipeline's sharding step can route unrecognized values to the vault by
    default instead of guessing that they are safe clinical data.
    """

    model_config = ConfigDict(strict=True, extra="allow")

    patient_name: str = Field(
        ..., description="Patient name extracted from the document, if present"
    )
    aadhaar_abha_id: str = Field(
        ..., description="Aadhaar or ABHA identifier if present"
    )
    phone: str = Field(..., description="Indian phone number if present")
    diagnoses: list[str] = Field(..., description="Directly written diagnoses only")
    lab_results: list[str] = Field(..., description="Lab result summaries")
    prescriptions: list[str] = Field(
        ..., description="Medication or prescription entries"
    )
    extraction_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Provider document-level confidence, when actually supplied",
    )
    field_evidence: list[ProviderFieldEvidence] = Field(default_factory=list)

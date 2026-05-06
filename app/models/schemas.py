"""Pydantic models for Nexa Care vertical sharding."""

from uuid import UUID

from pydantic import BaseModel, Field


class PIIVaultSchema(BaseModel):
    masked_internal_id: UUID = Field(..., description="Masked internal UUID linking to clinical shard")
    patient_name: str = Field(..., description="Patient full name (PII)")
    phone: str = Field(..., description="Patient phone number (PII)")
    aadhaar_abha_id: str = Field(..., description="Aadhaar/ABHA identifier (PII)")


class ClinicalRecordSchema(BaseModel):
    masked_internal_id: UUID = Field(..., description="Masked internal UUID linking to PII vault")
    diagnoses: list[str] = Field(default_factory=list, description="Anonymized diagnoses")
    lab_results: list[str] = Field(default_factory=list, description="Anonymized lab results")
    prescriptions: list[str] = Field(default_factory=list, description="Anonymized prescriptions")

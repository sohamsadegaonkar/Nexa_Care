"""Clinical ingestion only accepts fully adjudicated, provenance-complete fields."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.extracted_field import ExtractedField, ValidationResult
from app.models.pipeline import PipelineCommit
from app.services.record_ingestion import ingest_extracted_fields

PATIENT_ID = "11111111-1111-4111-8111-111111111111"
JOB_ID = "22222222-2222-4222-8222-222222222222"
PROVIDER_ID = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def db():
    session = AsyncMock()
    session.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result
    return session


def field(**overrides):
    data = {
        "field_id": str(uuid.uuid4()),
        "job_id": JOB_ID,
        "field_name": "blood_pressure",
        "raw_value": "120 over 80",
        "units": "mmHg",
        "confidence": 0.91,
        "risk_level": "MEDIUM_RISK",
        "source_document_id": str(uuid.uuid4()),
        "status": "approved",
    }
    data.update(overrides)
    return ExtractedField(**data)


async def ingest(db, value):
    with patch(
        "app.services.record_ingestion.append_audit_log_or_503", new=AsyncMock()
    ):
        return await ingest_extracted_fields(
            PATIENT_ID, JOB_ID, [value], db, PROVIDER_ID
        )


@pytest.mark.asyncio
async def test_approved_vital_with_units_is_ingested(db):
    result = await ingest(db, field())
    assert result.vitals_created == 1 and result.ingested_count == 1
    commit = next(
        item
        for item in db.add.call_args_list
        if isinstance(item.args[0], PipelineCommit)
    ).args[0]
    assert commit.committed_by == PROVIDER_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["needs_review", "rejected", "auto_approved"])
async def test_non_adjudicated_status_is_rejected(db, status):
    with pytest.raises(HTTPException) as exc:
        await ingest(db, field(status=status))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("confidence", [None, -0.1, 1.1, True])
async def test_invalid_confidence_is_rejected(db, confidence):
    with pytest.raises((HTTPException, ValidationError)):
        await ingest(db, field(confidence=confidence))


@pytest.mark.asyncio
async def test_missing_vital_units_is_rejected(db):
    with pytest.raises(HTTPException) as exc:
        await ingest(db, field(units=None))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["medication", "allergy"])
async def test_unstructured_high_risk_types_are_rejected(db, name):
    with pytest.raises(HTTPException) as exc:
        await ingest(db, field(field_name=name))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_lab_requires_reference_range(db):
    with pytest.raises(HTTPException) as exc:
        await ingest(
            db,
            field(field_name="hba1c", units="%", validation_result=ValidationResult()),
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_unsupported_field_type_is_rejected(db):
    with pytest.raises(HTTPException) as exc:
        await ingest(db, field(field_name="free_text_diagnosis"))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_invalid_patient_or_job_identifier_is_rejected_before_write(db):
    with patch(
        "app.services.record_ingestion.append_audit_log_or_503", new=AsyncMock()
    ):
        with pytest.raises(HTTPException) as exc:
            await ingest_extracted_fields(
                "not-a-uuid", JOB_ID, [field()], db, PROVIDER_ID
            )
    assert exc.value.status_code == 422

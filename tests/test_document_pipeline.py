import pytest
from fastapi.testclient import TestClient

from app.ai.pipeline import (
    LegacyDocumentPipelineDisabled,
    process_medical_document_background,
)
from app.main import app


@pytest.mark.asyncio
async def test_legacy_pipeline_cannot_persist_any_extraction():
    with pytest.raises(LegacyDocumentPipelineDisabled):
        await process_medical_document_background("unbound.pdf", "provider", object())


def test_unbound_document_upload_is_explicitly_gone():
    response = TestClient(app).post("/api/v2/documents/upload")
    assert response.status_code == 410
    assert response.json()["error_code"] == "UNBOUND_DOCUMENT_UPLOAD_RETIRED"


def test_legacy_failure_response_contains_no_mock_clinical_output():
    body = TestClient(app).post("/api/v2/documents/upload").json()
    serialized = str(body).lower()
    assert "asha" not in serialized
    assert "diagnos" not in serialized
    assert "prescription" not in serialized

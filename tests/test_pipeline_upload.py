import base64
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.v2.pipeline_routes import CommitJobRequest, _validated_upload_type, commit_extraction_job
from app.models.ai_models import ExtractedMedicalDocument
from app.services.document_storage import DocumentStorageError, LocalEncryptedDocumentStorage
from app.core.config import DocumentStorageConfig
from app.services.pipeline_orchestrator import _candidate_fields, process_extraction_job


def test_upload_validation_uses_magic_mime_and_sanitizes_filename():
    name, mime = _validated_upload_type("../../panel.pdf", "application/pdf", b"%PDF-1.7\n")
    assert name == "panel.pdf"
    assert mime == "application/pdf"


@pytest.mark.parametrize("name", ["panel.pdf", "demo.pdf", "aarav.pdf"])
def test_ordinary_filenames_do_not_change_validation(name):
    assert _validated_upload_type(name, "application/pdf", b"%PDF-1.7")[1] == "application/pdf"


def test_extension_mime_mismatch_is_rejected():
    with pytest.raises(HTTPException) as exc:
        _validated_upload_type("report.pdf", "image/png", b"\x89PNG\r\n\x1a\n")
    assert exc.value.status_code == 415


@pytest.mark.asyncio
async def test_local_storage_roundtrip_enforces_patient_tenant_ownership(tmp_path):
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    storage = LocalEncryptedDocumentStorage(DocumentStorageConfig(
        provider="local", environment="test", local_root=tmp_path, encryption_key=key,
    ))
    stored = await storage.put_document(b"%PDF-private", tenant_id="tenant-a", patient_id="patient-a", mime_type="application/pdf")
    assert await storage.get_document_bytes(stored.storage_ref, tenant_id="tenant-a", patient_id="patient-a") == b"%PDF-private"
    with pytest.raises(DocumentStorageError):
        await storage.get_document_bytes(stored.storage_ref, tenant_id="tenant-a", patient_id="patient-b")
    await storage.delete_document(stored.storage_ref, tenant_id="tenant-a", patient_id="patient-a")


def test_ocr_identity_is_not_a_candidate_and_no_default_field_is_created():
    document = ExtractedMedicalDocument(
        patient_name="OCR Name", aadhaar_abha_id="ocr-id", phone="ocr-phone",
        diagnoses=[], lab_results=[], prescriptions=[], extraction_confidence=0.99,
    )
    assert _candidate_fields(document) == []


@pytest.mark.asyncio
async def test_malformed_job_id_is_not_uuid5_coerced():
    result = await process_extraction_job("job-demo", object())
    assert result == {"status": "extraction_failed_terminal", "error_code": "INVALID_JOB_ID"}


@pytest.mark.asyncio
async def test_commit_rejects_client_supplied_fields_before_any_database_write():
    with pytest.raises(HTTPException) as exc:
        await commit_extraction_job(
            str(uuid.uuid4()), CommitJobRequest(patient_id=str(uuid.uuid4()), fields=[{"field_name": "lab_result"}]),
            provider=None, x_consent_token="token", db=object(),
        )
    assert exc.value.detail["error_code"] == "CLIENT_SUPPLIED_COMMIT_FIELDS_FORBIDDEN"

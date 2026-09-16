from __future__ import annotations

import base64

import pytest

from app.core.config import DocumentStorageConfig
from app.services.document_storage import (
    DocumentStorage,
    DocumentStorageError,
    LocalEncryptedDocumentStorage,
)


@pytest.mark.asyncio
async def test_patient_document_namespace_round_trip_and_ownership(tmp_path) -> None:
    key = base64.urlsafe_b64encode(b"p" * 32).decode("ascii")
    storage = LocalEncryptedDocumentStorage(
        DocumentStorageConfig(
            provider="local",
            environment="test",
            local_root=tmp_path,
            encryption_key=key,
        )
    )
    patient_id = "11111111-1111-1111-1111-111111111111"
    other_patient_id = "22222222-2222-2222-2222-222222222222"
    payload = b"%PDF-1.7 patient external record"

    stored = await storage.put_patient_document(
        payload, patient_id=patient_id, mime_type="application/pdf"
    )

    assert stored.object_key.startswith(f"patient-self/{patient_id}/")
    assert patient_id not in stored.storage_ref.split("patient-self/", 1)[0]
    assert await storage.get_patient_document_bytes(
        stored.storage_ref, patient_id=patient_id
    ) == payload

    with pytest.raises(DocumentStorageError, match="ownership mismatch"):
        await storage.get_patient_document_bytes(
            stored.storage_ref, patient_id=other_patient_id
        )


@pytest.mark.asyncio
async def test_patient_document_ciphertext_is_not_plaintext(tmp_path) -> None:
    key = base64.urlsafe_b64encode(b"q" * 32).decode("ascii")
    storage = LocalEncryptedDocumentStorage(
        DocumentStorageConfig(
            provider="local",
            environment="test",
            local_root=tmp_path,
            encryption_key=key,
        )
    )
    patient_id = "33333333-3333-3333-3333-333333333333"
    payload = b"%PDF-1.7 highly sensitive medical source"

    stored = await storage.put_patient_document(
        payload, patient_id=patient_id, mime_type="application/pdf"
    )
    ciphertext = (tmp_path / stored.object_key).read_bytes()

    assert payload not in ciphertext
    assert ciphertext != payload


@pytest.mark.asyncio
async def test_provider_and_patient_namespaces_are_not_interchangeable(tmp_path) -> None:
    key = base64.urlsafe_b64encode(b"r" * 32).decode("ascii")
    storage = LocalEncryptedDocumentStorage(
        DocumentStorageConfig(
            provider="local",
            environment="test",
            local_root=tmp_path,
            encryption_key=key,
        )
    )
    patient_id = "44444444-4444-4444-4444-444444444444"
    tenant_id = "55555555-5555-5555-5555-555555555555"
    payload = b"%PDF-1.7 namespace separation"

    patient_source = await storage.put_patient_document(
        payload, patient_id=patient_id, mime_type="application/pdf"
    )
    provider_source = await storage.put_document(
        payload,
        tenant_id=tenant_id,
        patient_id=patient_id,
        mime_type="application/pdf",
    )

    with pytest.raises(DocumentStorageError, match="ownership mismatch"):
        await storage.get_document_bytes(
            patient_source.storage_ref,
            tenant_id=tenant_id,
            patient_id=patient_id,
        )
    with pytest.raises(DocumentStorageError, match="ownership mismatch"):
        await storage.get_patient_document_bytes(
            provider_source.storage_ref, patient_id=patient_id
        )


class _ProviderOnlyStorage(DocumentStorage):
    async def put_document(
        self, data: bytes, *, tenant_id: str, patient_id: str, mime_type: str
    ):
        raise AssertionError("provider write path is not exercised")

    async def get_document_bytes(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> bytes:
        raise AssertionError("provider read path is not exercised")

    async def delete_document(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> None:
        raise AssertionError("provider delete path is not exercised")


@pytest.mark.asyncio
async def test_provider_only_storage_remains_instantiable_and_patient_calls_fail_closed() -> None:
    storage = _ProviderOnlyStorage()

    with pytest.raises(DocumentStorageError, match="Patient-self document storage is unsupported"):
        await storage.put_patient_document(
            b"source", patient_id="patient-1", mime_type="application/pdf"
        )
    with pytest.raises(DocumentStorageError, match="Patient-self document storage is unsupported"):
        await storage.get_patient_document_bytes(
            "provider://source", patient_id="patient-1"
        )
    with pytest.raises(DocumentStorageError, match="Patient-self document storage is unsupported"):
        await storage.delete_patient_document(
            "provider://source", patient_id="patient-1"
        )

"""Offline SDK-double tests; never contact Azure or use patient data."""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from app.core.config import ConfigError, DocumentStorageConfig, get_document_storage_config
from app.services import azure_document_storage as module
from app.services.document_storage import DocumentStorageError, get_document_storage

PATIENT = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
TENANT = "33333333-3333-3333-3333-333333333333"
KEY = base64.urlsafe_b64encode(b"a" * 32).decode("ascii")
PAYLOAD = b"%PDF-1.7 synthetic test"


@pytest.fixture
def fake_cloud(monkeypatch):
    state = SimpleNamespace(objects={}, public_access=None, calls=[], fail=False)

    class NotFound(Exception):
        error_code = "BlobNotFound"

    class ClosedContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Identity(ClosedContext):
        def __init__(self, **kwargs):
            state.calls.append(("identity", kwargs))

    class Blob(ClosedContext):
        def __init__(self, name):
            self.name = name

        def upload_blob(self, data, *, overwrite, logging_enable):
            assert overwrite is False and logging_enable is False
            state.calls.append(("write", self.name))
            if state.fail:
                raise RuntimeError("private sdk error detail")
            assert self.name not in state.objects
            state.objects[self.name] = data

        def download_blob(self, **kwargs):
            assert kwargs["offset"] == 0 and kwargs["logging_enable"] is False
            state.calls.append(("read", self.name))
            if self.name not in state.objects:
                raise NotFound()
            return SimpleNamespace(readall=lambda: state.objects[self.name][:kwargs["length"]])

        def delete_blob(self, **kwargs):
            assert kwargs["logging_enable"] is False
            state.calls.append(("delete", self.name))
            if self.name not in state.objects:
                raise NotFound()
            del state.objects[self.name]

    class Container(ClosedContext):
        def __init__(self, **kwargs):
            assert kwargs["account_url"] == "https://nexademotest.blob.core.windows.net"
            assert kwargs["container_name"] == "nexa-demo-documents"
            assert isinstance(kwargs["credential"], Identity)

        def get_container_properties(self, **_kwargs):
            return SimpleNamespace(public_access=state.public_access)

        def get_blob_client(self, name):
            return Blob(name)

    modules = {
        "azure.identity": SimpleNamespace(ManagedIdentityCredential=Identity),
        "azure.storage.blob": SimpleNamespace(ContainerClient=Container),
        "azure.core.exceptions": SimpleNamespace(ResourceNotFoundError=NotFound),
    }
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("DOCUMENT_STORAGE_PROVIDER", "azure_blob")
    monkeypatch.setenv("DOCUMENT_STORAGE_AZURE_SYNTHETIC_ONLY", "YES")
    monkeypatch.setenv("DOCUMENT_STORAGE_AZURE_ACCOUNT", "nexademotest")
    monkeypatch.setenv("DOCUMENT_STORAGE_AZURE_CONTAINER", "nexa-demo-documents")
    monkeypatch.setenv("DOCUMENT_STORAGE_ENCRYPTION_KEY", KEY)
    monkeypatch.delenv("DOCUMENT_STORAGE_AZURE_MANAGED_IDENTITY_CLIENT_ID", raising=False)
    monkeypatch.setattr(module, "import_module", lambda name: modules[name])
    return state


def test_explicit_config_and_production_denial(fake_cloud, monkeypatch):
    assert get_document_storage_config().provider == "azure_blob"
    for env in ("alpha", "staging", "preview", "pilot", "production"):
        monkeypatch.setenv("ENVIRONMENT", env)
        with pytest.raises(ConfigError, match="not qualified"):
            get_document_storage_config()
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("DOCUMENT_STORAGE_AZURE_SYNTHETIC_ONLY")
    with pytest.raises(ConfigError, match="synthetic-only"):
        get_document_storage_config()


def test_no_shared_campuspool_container(fake_cloud, monkeypatch):
    monkeypatch.setenv("DOCUMENT_STORAGE_AZURE_CONTAINER", "campuspool-staging-backups")
    with pytest.raises(ConfigError, match="dedicated"):
        get_document_storage_config()


@pytest.mark.asyncio
async def test_patient_roundtrip_ciphertext_and_idempotent_delete(fake_cloud):
    storage = get_document_storage()
    stored = await storage.put_patient_document(
        PAYLOAD, patient_id=PATIENT, mime_type="application/pdf"
    )
    name, encrypted = next(iter(fake_cloud.objects.items()))
    assert PATIENT not in name
    assert PAYLOAD not in encrypted
    assert stored.storage_ref.startswith("azure+encrypted://nexademotest/nexa-demo-documents/")
    assert await storage.get_patient_document_bytes(stored.storage_ref, patient_id=PATIENT) == PAYLOAD
    await storage.delete_patient_document(stored.storage_ref, patient_id=PATIENT)
    await storage.delete_patient_document(stored.storage_ref, patient_id=PATIENT)
    assert fake_cloud.objects == {}


@pytest.mark.asyncio
async def test_owner_tenant_legacy_and_malformed_refs_denied_before_network(fake_cloud):
    storage = get_document_storage()
    doc = await storage.put_document(
        PAYLOAD, tenant_id=TENANT, patient_id=PATIENT, mime_type="application/pdf"
    )
    assert await storage.get_document_bytes(
        doc.storage_ref, tenant_id=TENANT, patient_id=PATIENT
    ) == PAYLOAD
    before = list(fake_cloud.calls)
    with pytest.raises(DocumentStorageError, match="ownership"):
        await storage.get_document_bytes(doc.storage_ref, tenant_id=OTHER, patient_id=PATIENT)
    with pytest.raises(DocumentStorageError, match="ownership"):
        await storage.get_patient_document_bytes(doc.storage_ref, patient_id=PATIENT)
    with pytest.raises(DocumentStorageError, match="ownership"):
        await storage.delete_document(doc.storage_ref, tenant_id=TENANT, patient_id=OTHER)
    with pytest.raises(DocumentStorageError, match="ownership"):
        await storage.get_document_bytes("s3://old/document", tenant_id=TENANT, patient_id=PATIENT)
    with pytest.raises(DocumentStorageError, match="Invalid document object"):
        await storage.delete_document(doc.storage_ref + "/other", tenant_id=TENANT, patient_id=PATIENT)
    assert fake_cloud.calls == before


@pytest.mark.asyncio
async def test_copy_or_ciphertext_tampering_denied(fake_cloud):
    storage = get_document_storage()
    first = await storage.put_patient_document(PAYLOAD, patient_id=PATIENT, mime_type="application/pdf")
    second = await storage.put_patient_document(PAYLOAD, patient_id=PATIENT, mime_type="application/pdf")
    name_a, name_b = fake_cloud.objects
    fake_cloud.objects[name_b] = fake_cloud.objects[name_a]
    with pytest.raises(DocumentStorageError, match="authentication failed"):
        await storage.get_patient_document_bytes(second.storage_ref, patient_id=PATIENT)
    original = fake_cloud.objects[name_a]
    fake_cloud.objects[name_a] = original[:-1] + bytes([original[-1] ^ 1])
    with pytest.raises(DocumentStorageError, match="authentication failed"):
        await storage.get_patient_document_bytes(first.storage_ref, patient_id=PATIENT)


@pytest.mark.asyncio
async def test_public_container_and_provider_errors_fail_closed(fake_cloud):
    storage = get_document_storage()
    fake_cloud.public_access = "blob"
    with pytest.raises(DocumentStorageError, match="not private"):
        await storage.put_patient_document(PAYLOAD, patient_id=PATIENT, mime_type="application/pdf")
    assert not fake_cloud.objects
    fake_cloud.public_access = None
    fake_cloud.fail = True
    with pytest.raises(DocumentStorageError, match="^Azure document storage operation failed$") as captured:
        await storage.put_patient_document(PAYLOAD, patient_id=PATIENT, mime_type="application/pdf")
    assert captured.value.__suppress_context__
    assert not fake_cloud.objects


def test_direct_constructor_cannot_bypass_production_guard(fake_cloud):
    with pytest.raises(ConfigError, match="isolated synthetic"):
        module.AzureEncryptedDocumentStorage(
            DocumentStorageConfig(provider="azure_blob", environment="production")
        )


@pytest.mark.asyncio
async def test_exact_size_enforced_for_patient_original_retrieval(fake_cloud):
    storage = get_document_storage()
    saved = await storage.put_patient_document(
        PAYLOAD, patient_id=PATIENT, mime_type="application/pdf"
    )
    assert await storage.get_patient_document_bytes(
        saved.storage_ref, patient_id=PATIENT, expected_size=len(PAYLOAD)
    ) == PAYLOAD
    for size in (len(PAYLOAD) - 1, len(PAYLOAD) + 1):
        with pytest.raises(DocumentStorageError, match="size mismatch"):
            await storage.get_patient_document_bytes(
                saved.storage_ref, patient_id=PATIENT, expected_size=size
            )
    for size in (0, -1, 20 * 1024 * 1024 + 1, True):
        with pytest.raises(DocumentStorageError, match="Invalid expected"):
            await storage.get_patient_document_bytes(
                saved.storage_ref, patient_id=PATIENT, expected_size=size
            )

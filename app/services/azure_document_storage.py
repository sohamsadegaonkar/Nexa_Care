"""Development-only Azure Blob adapter for synthetic clinical documents.

Never grants access. Callers retain their existing authorization, quarantine,
audit, integrity and erasure checks. Legacy refs are not migrated or reinterpreted.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import re
import uuid
from collections.abc import Callable
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import ConfigError, DocumentStorageConfig
from app.services.document_storage import (
    DocumentStorage,
    DocumentStorageError,
    StoredDocument,
    _aad,
    _key_bytes,
    _patient_aad,
    _patient_object_prefix,
)

_MAX_PLAINTEXT = 20 * 1024 * 1024
_MAX_CIPHERTEXT = _MAX_PLAINTEXT + 12 + 16


class AzureEncryptedDocumentStorage(DocumentStorage):
    """Write-once encrypted blobs, authenticated using managed identity only."""

    def __init__(self, config: DocumentStorageConfig) -> None:
        if config.provider != "azure_blob" or config.environment not in {
            "local", "development", "test"
        } or os.getenv("DOCUMENT_STORAGE_AZURE_SYNTHETIC_ONLY") != "YES":
            raise ConfigError("Azure document storage requires isolated synthetic development")
        account = config.azure_account or ""
        container = config.azure_container or ""
        if not re.fullmatch(r"[a-z0-9]{3,24}", account):
            raise ConfigError("Invalid Azure storage account name")
        if not re.fullmatch(r"nexa-demo-[a-z0-9]+(?:-[a-z0-9]+)*", container) or len(container) > 63:
            raise ConfigError("Use a dedicated nexa-demo- Azure container")
        if not config.encryption_key:
            raise ConfigError("Missing document storage encryption key")
        self.key = _key_bytes(config.encryption_key)
        self.account = account
        self.container = container
        self.client_id = config.azure_managed_identity_client_id
        if self.client_id:
            self._canonical_id(self.client_id)
        self._prefix = f"azure+encrypted://{account}/{container}/"
        try:
            self._identity_type = importlib.import_module("azure.identity").ManagedIdentityCredential
            self._container_type = importlib.import_module("azure.storage.blob").ContainerClient
            self._not_found = importlib.import_module("azure.core.exceptions").ResourceNotFoundError
        except (ImportError, AttributeError) as exc:
            raise ConfigError("Install the optional Azure document-storage dependencies") from exc

    @staticmethod
    def _canonical_id(value: str) -> str:
        try:
            if str(uuid.UUID(value)) == value:
                return value
        except (ValueError, TypeError, AttributeError):
            pass
        raise DocumentStorageError("Invalid document owner identifier")

    def _owner_prefix(self, patient_id: str, tenant_id: str | None = None) -> str:
        patient = self._canonical_id(patient_id)
        if tenant_id is None:
            return _patient_object_prefix(patient)
        return f"{self._canonical_id(tenant_id)}/{patient}/"

    def _key_for_ref(self, storage_ref: str, owner_prefix: str) -> str:
        prefix = self._prefix + owner_prefix
        if not storage_ref.startswith(prefix):
            raise DocumentStorageError("Document ownership or storage namespace mismatch")
        suffix = storage_ref[len(prefix):]
        if not re.fullmatch(r"[0-9a-f]{32}\.bin", suffix):
            raise DocumentStorageError("Invalid document object reference")
        return owner_prefix + suffix

    def _bound_aad(self, object_key: str, patient_id: str, tenant_id: str | None) -> bytes:
        aad = (
            _aad(tenant_id, patient_id, object_key)
            if tenant_id is not None
            else _patient_aad(patient_id, object_key)
        )
        return b"nexa-azure-document-v1\0" + self._prefix.encode("ascii") + b"\0" + aad

    def _blob_name(self, object_key: str) -> str:
        # Avoid patient/tenant identifiers in cloud object names and HTTP paths.
        return hashlib.sha256((self._prefix + object_key).encode("ascii")).hexdigest() + ".bin"

    def _with_blob(self, object_key: str, operation: Callable[[Any], Any]) -> Any:
        kwargs = {"client_id": self.client_id} if self.client_id else {}
        # Construct a single-purpose client per operation; no ambient CLI/shared
        # key/SAS fallback, no container creation and no public source URL.
        try:
            with self._identity_type(**kwargs) as credential:
                with self._container_type(
                    account_url=f"https://{self.account}.blob.core.windows.net",
                    container_name=self.container,
                    credential=credential,
                    logging_enable=False,
                ) as container:
                    properties = container.get_container_properties(logging_enable=False)
                    if properties.public_access is not None:
                        raise DocumentStorageError("Azure document container is not private")
                    with container.get_blob_client(self._blob_name(object_key)) as blob:
                        return operation(blob)
        except DocumentStorageError:
            raise
        except Exception:
            # Provider SDK exceptions may include request paths and credentials.
            raise DocumentStorageError("Azure document storage operation failed") from None

    async def _put(
        self, data: bytes, *, patient_id: str, tenant_id: str | None, mime_type: str
    ) -> StoredDocument:
        if not data or len(data) > _MAX_PLAINTEXT:
            raise DocumentStorageError("Unsupported document size")
        key = self._owner_prefix(patient_id, tenant_id) + uuid.uuid4().hex + ".bin"
        nonce = os.urandom(12)
        encrypted = nonce + AESGCM(self.key).encrypt(
            nonce, data, self._bound_aad(key, patient_id, tenant_id)
        )

        def upload(blob: Any) -> None:
            blob.upload_blob(encrypted, overwrite=False, logging_enable=False)

        await asyncio.to_thread(self._with_blob, key, upload)
        return StoredDocument(
            self._prefix + key, hashlib.sha256(data).hexdigest(), len(data), mime_type, key
        )

    async def _get(
        self, storage_ref: str, *, patient_id: str, tenant_id: str | None
    ) -> bytes:
        key = self._key_for_ref(storage_ref, self._owner_prefix(patient_id, tenant_id))

        def download(blob: Any) -> bytes:
            # Bound memory and reject oversized ciphertext rather than streaming
            # partial unauthenticated plaintext to a caller.
            downloader = blob.download_blob(
                offset=0, length=_MAX_CIPHERTEXT + 1, logging_enable=False
            )
            return downloader.readall()

        encrypted = await asyncio.to_thread(self._with_blob, key, download)
        if not 29 <= len(encrypted) <= _MAX_CIPHERTEXT:
            raise DocumentStorageError("Stored document size is invalid")
        try:
            return AESGCM(self.key).decrypt(
                encrypted[:12], encrypted[12:], self._bound_aad(key, patient_id, tenant_id)
            )
        except Exception:
            raise DocumentStorageError("Stored document authentication failed") from None

    async def _delete(
        self, storage_ref: str, *, patient_id: str, tenant_id: str | None
    ) -> None:
        key = self._key_for_ref(storage_ref, self._owner_prefix(patient_id, tenant_id))

        def delete(blob: Any) -> None:
            try:
                blob.delete_blob(logging_enable=False)
            except self._not_found as exc:
                # A missing container/permission/transport error is not proof of
                # successful deletion. Only an absent current blob is idempotent.
                if exc.error_code != "BlobNotFound":
                    raise

        await asyncio.to_thread(self._with_blob, key, delete)

    async def put_document(
        self, data: bytes, *, tenant_id: str, patient_id: str, mime_type: str
    ) -> StoredDocument:
        return await self._put(data, patient_id=patient_id, tenant_id=tenant_id, mime_type=mime_type)

    async def get_document_bytes(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> bytes:
        return await self._get(storage_ref, patient_id=patient_id, tenant_id=tenant_id)

    async def delete_document(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> None:
        await self._delete(storage_ref, patient_id=patient_id, tenant_id=tenant_id)

    async def put_patient_document(
        self, data: bytes, *, patient_id: str, mime_type: str
    ) -> StoredDocument:
        return await self._put(data, patient_id=patient_id, tenant_id=None, mime_type=mime_type)

    async def get_patient_document_bytes(self, storage_ref: str, *, patient_id: str) -> bytes:
        return await self._get(storage_ref, patient_id=patient_id, tenant_id=None)

    async def delete_patient_document(self, storage_ref: str, *, patient_id: str) -> None:
        await self._delete(storage_ref, patient_id=patient_id, tenant_id=None)

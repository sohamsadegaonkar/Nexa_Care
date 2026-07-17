"""Tenant/patient-bound encrypted document storage adapters."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import ConfigError, DocumentStorageConfig, get_document_storage_config


@dataclass(frozen=True)
class StoredDocument:
    storage_ref: str
    content_hash: str
    size: int
    mime_type: str
    object_key: str


class DocumentStorageError(RuntimeError):
    pass


class DocumentStorage(ABC):
    @abstractmethod
    async def put_document(self, data: bytes, *, tenant_id: str, patient_id: str, mime_type: str) -> StoredDocument: ...

    @abstractmethod
    async def get_document_bytes(self, storage_ref: str, *, tenant_id: str, patient_id: str) -> bytes: ...

    @abstractmethod
    async def delete_document(self, storage_ref: str, *, tenant_id: str, patient_id: str) -> None: ...


def _key_bytes(value: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:
        raise ConfigError("DOCUMENT_STORAGE_ENCRYPTION_KEY must be urlsafe base64") from exc
    if len(key) != 32:
        raise ConfigError("DOCUMENT_STORAGE_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def _aad(tenant_id: str, patient_id: str, object_key: str) -> bytes:
    return f"nexa-document-v1\0{tenant_id}\0{patient_id}\0{object_key}".encode()


class LocalEncryptedDocumentStorage(DocumentStorage):
    def __init__(self, config: DocumentStorageConfig) -> None:
        assert config.local_root is not None and config.encryption_key is not None
        self.root = config.local_root
        self.key = _key_bytes(config.encryption_key)

    def _path(self, object_key: str) -> Path:
        candidate = (self.root / object_key).resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise DocumentStorageError("Invalid object key")
        return candidate

    async def put_document(self, data: bytes, *, tenant_id: str, patient_id: str, mime_type: str) -> StoredDocument:
        content_hash = hashlib.sha256(data).hexdigest()
        object_key = f"{tenant_id}/{patient_id}/{uuid.uuid4().hex}.bin"
        nonce = os.urandom(12)
        encrypted = nonce + AESGCM(self.key).encrypt(nonce, data, _aad(tenant_id, patient_id, object_key))
        path = self._path(object_key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as handle:
                handle.write(encrypted)

        await asyncio.to_thread(write)
        return StoredDocument(f"local+encrypted://{object_key}", content_hash, len(data), mime_type, object_key)

    async def get_document_bytes(self, storage_ref: str, *, tenant_id: str, patient_id: str) -> bytes:
        prefix = "local+encrypted://"
        if not storage_ref.startswith(prefix):
            raise DocumentStorageError("Unsupported local storage reference")
        object_key = storage_ref[len(prefix):]
        expected_prefix = f"{tenant_id}/{patient_id}/"
        if not object_key.startswith(expected_prefix):
            raise DocumentStorageError("Document ownership mismatch")
        encrypted = await asyncio.to_thread(self._path(object_key).read_bytes)
        if len(encrypted) < 13:
            raise DocumentStorageError("Stored document is corrupt")
        try:
            return AESGCM(self.key).decrypt(encrypted[:12], encrypted[12:], _aad(tenant_id, patient_id, object_key))
        except Exception as exc:
            raise DocumentStorageError("Stored document authentication failed") from exc

    async def delete_document(self, storage_ref: str, *, tenant_id: str, patient_id: str) -> None:
        prefix = "local+encrypted://"
        if not storage_ref.startswith(prefix):
            raise DocumentStorageError("Unsupported local storage reference")
        object_key = storage_ref[len(prefix):]
        if not object_key.startswith(f"{tenant_id}/{patient_id}/"):
            raise DocumentStorageError("Document ownership mismatch")
        path = self._path(object_key)
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            return


class S3EncryptedDocumentStorage(DocumentStorage):
    """Durable S3 adapter using client-side authenticated encryption."""

    def __init__(self, config: DocumentStorageConfig) -> None:
        if not config.s3_bucket or not config.s3_region or not config.s3_kms_key_id or not config.encryption_key:
            raise ConfigError("Incomplete S3 document storage configuration")
        try:
            boto3 = importlib.import_module("boto3")
        except ImportError as exc:
            raise ConfigError("boto3 is required for DOCUMENT_STORAGE_PROVIDER=s3") from exc
        self.client = boto3.client("s3", region_name=config.s3_region)
        self.bucket = config.s3_bucket
        self.kms_key_id = config.s3_kms_key_id
        self.key = _key_bytes(config.encryption_key)

    async def put_document(self, data: bytes, *, tenant_id: str, patient_id: str, mime_type: str) -> StoredDocument:
        digest = hashlib.sha256(data).hexdigest()
        object_key = f"{tenant_id}/{patient_id}/{uuid.uuid4().hex}.bin"
        nonce = os.urandom(12)
        body = nonce + AESGCM(self.key).encrypt(nonce, data, _aad(tenant_id, patient_id, object_key))
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket, Key=object_key, Body=body,
            ContentType="application/octet-stream",
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=self.kms_key_id,
            Metadata={"sha256": digest, "source-mime": mime_type},
        )
        return StoredDocument(f"s3://{self.bucket}/{object_key}", digest, len(data), mime_type, object_key)

    def _object_key(self, storage_ref: str, tenant_id: str, patient_id: str) -> str:
        prefix = f"s3://{self.bucket}/"
        if not storage_ref.startswith(prefix):
            raise DocumentStorageError("Unexpected S3 bucket")
        key = storage_ref[len(prefix):]
        if not key.startswith(f"{tenant_id}/{patient_id}/"):
            raise DocumentStorageError("Document ownership mismatch")
        return key

    async def get_document_bytes(self, storage_ref: str, *, tenant_id: str, patient_id: str) -> bytes:
        key = self._object_key(storage_ref, tenant_id, patient_id)
        response = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        body = await asyncio.to_thread(response["Body"].read)
        try:
            return AESGCM(self.key).decrypt(body[:12], body[12:], _aad(tenant_id, patient_id, key))
        except Exception as exc:
            raise DocumentStorageError("Stored document authentication failed") from exc

    async def delete_document(self, storage_ref: str, *, tenant_id: str, patient_id: str) -> None:
        key = self._object_key(storage_ref, tenant_id, patient_id)
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)


def get_document_storage() -> DocumentStorage:
    config = get_document_storage_config()
    return LocalEncryptedDocumentStorage(config) if config.provider == "local" else S3EncryptedDocumentStorage(config)

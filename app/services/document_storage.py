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

from app.core.config import (
    ConfigError,
    DocumentStorageConfig,
    get_document_storage_config,
)


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
    async def put_document(
        self, data: bytes, *, tenant_id: str, patient_id: str, mime_type: str
    ) -> StoredDocument: ...

    @abstractmethod
    async def get_document_bytes(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> bytes: ...

    @abstractmethod
    async def delete_document(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> None: ...

    @abstractmethod
    async def put_patient_document(
        self, data: bytes, *, patient_id: str, mime_type: str
    ) -> StoredDocument: ...

    @abstractmethod
    async def get_patient_document_bytes(
        self, storage_ref: str, *, patient_id: str
    ) -> bytes: ...

    @abstractmethod
    async def delete_patient_document(
        self, storage_ref: str, *, patient_id: str
    ) -> None: ...


def _key_bytes(value: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:
        raise ConfigError(
            "DOCUMENT_STORAGE_ENCRYPTION_KEY must be urlsafe base64"
        ) from exc
    if len(key) != 32:
        raise ConfigError(
            "DOCUMENT_STORAGE_ENCRYPTION_KEY must decode to exactly 32 bytes"
        )
    return key


def _aad(tenant_id: str, patient_id: str, object_key: str) -> bytes:
    return f"nexa-document-v1\0{tenant_id}\0{patient_id}\0{object_key}".encode()


def _patient_aad(patient_id: str, object_key: str) -> bytes:
    return f"nexa-patient-document-v1\0{patient_id}\0{object_key}".encode()


def _patient_object_prefix(patient_id: str) -> str:
    return f"patient-self/{patient_id}/"


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

    async def _write_encrypted(
        self, data: bytes, *, object_key: str, aad: bytes, mime_type: str
    ) -> StoredDocument:
        content_hash = hashlib.sha256(data).hexdigest()
        nonce = os.urandom(12)
        encrypted = nonce + AESGCM(self.key).encrypt(nonce, data, aad)
        path = self._path(object_key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as handle:
                handle.write(encrypted)

        await asyncio.to_thread(write)
        return StoredDocument(
            f"local+encrypted://{object_key}",
            content_hash,
            len(data),
            mime_type,
            object_key,
        )

    async def put_document(
        self, data: bytes, *, tenant_id: str, patient_id: str, mime_type: str
    ) -> StoredDocument:
        object_key = f"{tenant_id}/{patient_id}/{uuid.uuid4().hex}.bin"
        return await self._write_encrypted(
            data,
            object_key=object_key,
            aad=_aad(tenant_id, patient_id, object_key),
            mime_type=mime_type,
        )

    async def put_patient_document(
        self, data: bytes, *, patient_id: str, mime_type: str
    ) -> StoredDocument:
        object_key = f"{_patient_object_prefix(patient_id)}{uuid.uuid4().hex}.bin"
        return await self._write_encrypted(
            data,
            object_key=object_key,
            aad=_patient_aad(patient_id, object_key),
            mime_type=mime_type,
        )

    def _local_object_key(self, storage_ref: str) -> str:
        prefix = "local+encrypted://"
        if not storage_ref.startswith(prefix):
            raise DocumentStorageError("Unsupported local storage reference")
        return storage_ref[len(prefix) :]

    async def _read_encrypted(self, object_key: str, *, aad: bytes) -> bytes:
        encrypted = await asyncio.to_thread(self._path(object_key).read_bytes)
        if len(encrypted) < 13:
            raise DocumentStorageError("Stored document is corrupt")
        try:
            return AESGCM(self.key).decrypt(encrypted[:12], encrypted[12:], aad)
        except Exception as exc:
            raise DocumentStorageError("Stored document authentication failed") from exc

    async def get_document_bytes(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> bytes:
        object_key = self._local_object_key(storage_ref)
        expected_prefix = f"{tenant_id}/{patient_id}/"
        if not object_key.startswith(expected_prefix):
            raise DocumentStorageError("Document ownership mismatch")
        return await self._read_encrypted(
            object_key, aad=_aad(tenant_id, patient_id, object_key)
        )

    async def get_patient_document_bytes(
        self, storage_ref: str, *, patient_id: str
    ) -> bytes:
        object_key = self._local_object_key(storage_ref)
        if not object_key.startswith(_patient_object_prefix(patient_id)):
            raise DocumentStorageError("Document ownership mismatch")
        return await self._read_encrypted(
            object_key, aad=_patient_aad(patient_id, object_key)
        )

    async def _delete_local(self, object_key: str) -> None:
        path = self._path(object_key)
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            return

    async def delete_document(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> None:
        object_key = self._local_object_key(storage_ref)
        if not object_key.startswith(f"{tenant_id}/{patient_id}/"):
            raise DocumentStorageError("Document ownership mismatch")
        await self._delete_local(object_key)

    async def delete_patient_document(
        self, storage_ref: str, *, patient_id: str
    ) -> None:
        object_key = self._local_object_key(storage_ref)
        if not object_key.startswith(_patient_object_prefix(patient_id)):
            raise DocumentStorageError("Document ownership mismatch")
        await self._delete_local(object_key)


class S3EncryptedDocumentStorage(DocumentStorage):
    """Durable S3 adapter using client-side authenticated encryption."""

    def __init__(self, config: DocumentStorageConfig) -> None:
        if (
            not config.s3_bucket
            or not config.s3_region
            or not config.s3_kms_key_id
            or not config.encryption_key
        ):
            raise ConfigError("Incomplete S3 document storage configuration")
        try:
            boto3 = importlib.import_module("boto3")
        except ImportError as exc:
            raise ConfigError(
                "boto3 is required for DOCUMENT_STORAGE_PROVIDER=s3"
            ) from exc
        self.client = boto3.client("s3", region_name=config.s3_region)
        self.bucket = config.s3_bucket
        self.kms_key_id = config.s3_kms_key_id
        self.key = _key_bytes(config.encryption_key)

    async def _put_s3(
        self, data: bytes, *, object_key: str, aad: bytes, mime_type: str
    ) -> StoredDocument:
        digest = hashlib.sha256(data).hexdigest()
        nonce = os.urandom(12)
        body = nonce + AESGCM(self.key).encrypt(nonce, data, aad)
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=object_key,
            Body=body,
            ContentType="application/octet-stream",
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=self.kms_key_id,
            Metadata={"sha256": digest, "source-mime": mime_type},
        )
        return StoredDocument(
            f"s3://{self.bucket}/{object_key}", digest, len(data), mime_type, object_key
        )

    async def put_document(
        self, data: bytes, *, tenant_id: str, patient_id: str, mime_type: str
    ) -> StoredDocument:
        object_key = f"{tenant_id}/{patient_id}/{uuid.uuid4().hex}.bin"
        return await self._put_s3(
            data,
            object_key=object_key,
            aad=_aad(tenant_id, patient_id, object_key),
            mime_type=mime_type,
        )

    async def put_patient_document(
        self, data: bytes, *, patient_id: str, mime_type: str
    ) -> StoredDocument:
        object_key = f"{_patient_object_prefix(patient_id)}{uuid.uuid4().hex}.bin"
        return await self._put_s3(
            data,
            object_key=object_key,
            aad=_patient_aad(patient_id, object_key),
            mime_type=mime_type,
        )

    def _raw_object_key(self, storage_ref: str) -> str:
        prefix = f"s3://{self.bucket}/"
        if not storage_ref.startswith(prefix):
            raise DocumentStorageError("Unexpected S3 bucket")
        return storage_ref[len(prefix) :]

    def _object_key(self, storage_ref: str, tenant_id: str, patient_id: str) -> str:
        key = self._raw_object_key(storage_ref)
        if not key.startswith(f"{tenant_id}/{patient_id}/"):
            raise DocumentStorageError("Document ownership mismatch")
        return key

    def _patient_object_key(self, storage_ref: str, patient_id: str) -> str:
        key = self._raw_object_key(storage_ref)
        if not key.startswith(_patient_object_prefix(patient_id)):
            raise DocumentStorageError("Document ownership mismatch")
        return key

    async def _get_s3_bytes(self, key: str, *, aad: bytes) -> bytes:
        response = await asyncio.to_thread(
            self.client.get_object, Bucket=self.bucket, Key=key
        )
        body = await asyncio.to_thread(response["Body"].read)
        if len(body) < 13:
            raise DocumentStorageError("Stored document is corrupt")
        try:
            return AESGCM(self.key).decrypt(body[:12], body[12:], aad)
        except Exception as exc:
            raise DocumentStorageError("Stored document authentication failed") from exc

    async def get_document_bytes(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> bytes:
        key = self._object_key(storage_ref, tenant_id, patient_id)
        return await self._get_s3_bytes(key, aad=_aad(tenant_id, patient_id, key))

    async def get_patient_document_bytes(
        self, storage_ref: str, *, patient_id: str
    ) -> bytes:
        key = self._patient_object_key(storage_ref, patient_id)
        return await self._get_s3_bytes(key, aad=_patient_aad(patient_id, key))

    async def delete_document(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> None:
        key = self._object_key(storage_ref, tenant_id, patient_id)
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    async def delete_patient_document(
        self, storage_ref: str, *, patient_id: str
    ) -> None:
        key = self._patient_object_key(storage_ref, patient_id)
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)


def get_document_storage() -> DocumentStorage:
    config = get_document_storage_config()
    return (
        LocalEncryptedDocumentStorage(config)
        if config.provider == "local"
        else S3EncryptedDocumentStorage(config)
    )

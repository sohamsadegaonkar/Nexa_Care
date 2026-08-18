"""Ephemeral, tenant-bound source staging for asynchronous Textract.

The durable document archive remains client-side encrypted.  This module is
the deliberately small boundary that decrypts an authorized document in
memory, validates it, and creates a short-lived SSE-KMS S3 object that
Textract can read.  It never writes plaintext to local disk or logs source
values.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import re
import uuid
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import (
    DocumentStorage as DocumentStorageRecord,
    ExtractionJob,
    ExtractionProviderJobRecord,
)
from app.services.document_storage import DocumentStorage, DocumentStorageError

_MAX_BYTES = 500 * 1024 * 1024
_MAX_PAGES = 3000
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class TextractSourceStagingError(RuntimeError):
    """Stable, value-free source staging failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TextractStagingConfig:
    bucket: str
    region: str
    kms_key_id: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.bucket, str)
            or not self.bucket.strip()
            or "/" in self.bucket
            or "://" in self.bucket
            or not isinstance(self.region, str)
            or not self.region.strip()
            or not isinstance(self.kms_key_id, str)
            or not self.kms_key_id.strip()
        ):
            raise TextractSourceStagingError("ASYNC_PROVIDER_STAGING_CONFIG_INVALID")


@dataclass(frozen=True, slots=True)
class PreparedTextractSource:
    """Validated source material held only by the caller for one operation."""

    tenant_id: uuid.UUID
    patient_id: uuid.UUID
    job_id: uuid.UUID
    source_document_id: uuid.UUID
    provider_attempt_id: uuid.UUID
    content_hash: str
    page_count: int
    content_type: str
    bytes_: bytes
    bucket: str
    key: str

    @property
    def location(self) -> tuple[str, str]:
        return self.bucket, self.key


@dataclass(frozen=True, slots=True)
class TextractStagingMetadata:
    bucket: str
    key: str
    content_type: str
    content_hash: str
    page_count: int
    reused: bool = False


def _uuid(value: object, code: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise TextractSourceStagingError(code) from exc


def inspect_pdf_page_count(source: bytes, *, require_multipage: bool = True) -> int:
    """Independently count PDF pages using pypdf, never provider output."""

    if not isinstance(source, bytes) or not source or len(source) > _MAX_BYTES:
        raise TextractSourceStagingError("ASYNC_PROVIDER_SOURCE_INVALID")
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(source), strict=True)
        if reader.is_encrypted:
            raise TextractSourceStagingError("ASYNC_PROVIDER_SOURCE_ENCRYPTED")
        page_count = len(reader.pages)
    except TextractSourceStagingError:
        raise
    except Exception as exc:  # noqa: BLE001 - parser details are not public
        raise TextractSourceStagingError("ASYNC_PROVIDER_SOURCE_PDF_INVALID") from exc
    if not isinstance(page_count, int) or not 1 <= page_count <= _MAX_PAGES:
        raise TextractSourceStagingError("ASYNC_PROVIDER_SOURCE_PAGE_COUNT_INVALID")
    if require_multipage and page_count <= 1:
        raise TextractSourceStagingError("ASYNC_PROVIDER_SOURCE_NOT_MULTIPAGE")
    return page_count


def deterministic_staging_key(
    *, tenant_id: uuid.UUID | str, provider_attempt_id: uuid.UUID | str
) -> str:
    """Derive a non-PHI key solely from authoritative server UUIDs."""

    tenant = _uuid(tenant_id, "ASYNC_PROVIDER_TENANT_BINDING_MISMATCH")
    attempt = _uuid(provider_attempt_id, "ASYNC_PROVIDER_ATTEMPT_NOT_FOUND")
    return f"textract-staging/{tenant}/{attempt}/source.pdf"


def _not_found(exc: BaseException) -> bool:
    if not isinstance(exc, ClientError):
        return False
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def _metadata_value(metadata: object, key: str) -> str | None:
    if not isinstance(metadata, dict):
        return None
    lowered = {str(k).lower(): v for k, v in metadata.items()}
    value = lowered.get(key.lower())
    return value if isinstance(value, str) else None


class TextractSourceStager:
    """Resolve, validate, stage, and clean up one provider attempt's source."""

    def __init__(
        self,
        *,
        config: TextractStagingConfig,
        storage: DocumentStorage,
        s3_client: Any,
        io_timeout_seconds: float = 30.0,
    ) -> None:
        if io_timeout_seconds <= 0:
            raise ValueError("io_timeout_seconds must be positive")
        self.config = config
        self.storage = storage
        self.s3 = s3_client
        self.io_timeout_seconds = io_timeout_seconds

    async def _io(self, method: str, **kwargs: Any) -> Any:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(getattr(self.s3, method), **kwargs),
                timeout=self.io_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise TextractSourceStagingError(
                "ASYNC_PROVIDER_STAGING_UNAVAILABLE"
            ) from exc
        except ClientError as exc:
            raise exc
        except Exception as exc:  # noqa: BLE001 - never expose client details
            raise TextractSourceStagingError(
                "ASYNC_PROVIDER_STAGING_UNAVAILABLE"
            ) from exc

    async def prepare_for_attempt(
        self,
        db: AsyncSession,
        *,
        provider_attempt_id: uuid.UUID | str,
        require_multipage: bool = True,
    ) -> PreparedTextractSource:
        attempt_uuid = _uuid(provider_attempt_id, "ASYNC_PROVIDER_ATTEMPT_NOT_FOUND")
        attempt = (
            await db.execute(
                select(ExtractionProviderJobRecord).where(
                    ExtractionProviderJobRecord.id == attempt_uuid
                )
            )
        ).scalar_one_or_none()
        if attempt is None:
            raise TextractSourceStagingError("ASYNC_PROVIDER_ATTEMPT_NOT_FOUND")
        return await self.prepare_for_graph(
            db,
            provider_attempt_id=attempt.id,
            job_id=attempt.job_id,
            tenant_id=attempt.tenant_id,
            patient_id=attempt.patient_id,
            source_document_id=attempt.source_document_id,
            require_multipage=require_multipage,
        )

    async def prepare_for_graph(
        self,
        db: AsyncSession,
        *,
        provider_attempt_id: uuid.UUID | str,
        job_id: uuid.UUID | str,
        tenant_id: uuid.UUID | str,
        patient_id: uuid.UUID | str,
        source_document_id: uuid.UUID | str,
        require_multipage: bool = True,
    ) -> PreparedTextractSource:
        attempt_uuid = _uuid(provider_attempt_id, "ASYNC_PROVIDER_ATTEMPT_NOT_FOUND")
        job_uuid = _uuid(job_id, "ASYNC_PROVIDER_JOB_NOT_FOUND")
        tenant_uuid = _uuid(tenant_id, "ASYNC_PROVIDER_TENANT_BINDING_MISMATCH")
        patient_uuid = _uuid(patient_id, "ASYNC_PROVIDER_PATIENT_BINDING_MISMATCH")
        document_uuid = _uuid(
            source_document_id, "ASYNC_PROVIDER_DOCUMENT_BINDING_MISMATCH"
        )
        job = (
            await db.execute(
                select(ExtractionJob).where(
                    ExtractionJob.id == job_uuid,
                    ExtractionJob.tenant_id == tenant_uuid,
                    ExtractionJob.patient_id == patient_uuid,
                    ExtractionJob.document_id == document_uuid,
                )
            )
        ).scalar_one_or_none()
        document = (
            await db.execute(
                select(DocumentStorageRecord).where(
                    DocumentStorageRecord.id == document_uuid,
                    DocumentStorageRecord.tenant_id == tenant_uuid,
                    DocumentStorageRecord.patient_id == patient_uuid,
                )
            )
        ).scalar_one_or_none()
        if (
            job is None
            or document is None
            or not document.storage_ref.startswith("s3://")
        ):
            raise TextractSourceStagingError("ASYNC_PROVIDER_GRAPH_BINDING_MISMATCH")
        try:
            source = await self.storage.get_document_bytes(
                document.storage_ref,
                tenant_id=str(tenant_uuid),
                patient_id=str(patient_uuid),
            )
        except DocumentStorageError as exc:
            raise TextractSourceStagingError(
                "ASYNC_PROVIDER_SOURCE_UNAVAILABLE"
            ) from exc
        digest = hashlib.sha256(source).hexdigest()
        if document.content_hash and (
            not _SHA256_RE.fullmatch(document.content_hash)
            or not hmac.compare_digest(digest, document.content_hash)
        ):
            raise TextractSourceStagingError("ASYNC_PROVIDER_SOURCE_HASH_MISMATCH")
        if document.content_type.lower().split(";", 1)[0].strip() != "application/pdf":
            raise TextractSourceStagingError("ASYNC_PROVIDER_SOURCE_TYPE_UNSUPPORTED")
        page_count = inspect_pdf_page_count(source, require_multipage=require_multipage)
        return PreparedTextractSource(
            tenant_id=tenant_uuid,
            patient_id=patient_uuid,
            job_id=job_uuid,
            source_document_id=document_uuid,
            provider_attempt_id=attempt_uuid,
            content_hash=digest,
            page_count=page_count,
            content_type="application/pdf",
            bytes_=source,
            bucket=self.config.bucket,
            key=deterministic_staging_key(
                tenant_id=tenant_uuid, provider_attempt_id=attempt_uuid
            ),
        )

    async def stage(self, prepared: PreparedTextractSource) -> TextractStagingMetadata:
        expected = {
            "source-sha256": prepared.content_hash,
            "source-document-id": str(prepared.source_document_id),
            "source-tenant-id": str(prepared.tenant_id),
            "page-count": str(prepared.page_count),
        }
        head: dict[str, Any] | None = None
        try:
            head = await self._io(
                "head_object", Bucket=prepared.bucket, Key=prepared.key
            )
        except ClientError as exc:
            if not _not_found(exc):
                raise TextractSourceStagingError(
                    "ASYNC_PROVIDER_STAGING_UNAVAILABLE"
                ) from exc
        if head is not None:
            metadata = head.get("Metadata")
            if any(
                _metadata_value(metadata, key) != value
                for key, value in expected.items()
            ):
                raise TextractSourceStagingError(
                    "ASYNC_PROVIDER_SOURCE_STAGING_CONFLICT"
                )
            return TextractStagingMetadata(
                prepared.bucket,
                prepared.key,
                prepared.content_type,
                prepared.content_hash,
                prepared.page_count,
                reused=True,
            )
        kwargs = {
            "Bucket": prepared.bucket,
            "Key": prepared.key,
            "Body": prepared.bytes_,
            "ContentType": prepared.content_type,
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": self.config.kms_key_id,
            "Metadata": expected,
            "IfNoneMatch": "*",
        }
        try:
            await self._io("put_object", **kwargs)
        except ClientError as exc:
            # A concurrent creator may win the conditional put.  Re-read and
            # verify its immutable source metadata before reusing it.
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in {"PreconditionFailed", "412"}:
                raise TextractSourceStagingError(
                    "ASYNC_PROVIDER_STAGING_UNAVAILABLE"
                ) from exc
            try:
                head = await self._io(
                    "head_object", Bucket=prepared.bucket, Key=prepared.key
                )
            except Exception as head_exc:  # noqa: BLE001
                raise TextractSourceStagingError(
                    "ASYNC_PROVIDER_STAGING_UNAVAILABLE"
                ) from head_exc
            metadata = head.get("Metadata")
            if any(
                _metadata_value(metadata, key) != value
                for key, value in expected.items()
            ):
                raise TextractSourceStagingError(
                    "ASYNC_PROVIDER_SOURCE_STAGING_CONFLICT"
                )
            return TextractStagingMetadata(
                prepared.bucket,
                prepared.key,
                prepared.content_type,
                prepared.content_hash,
                prepared.page_count,
                reused=True,
            )
        return TextractStagingMetadata(
            prepared.bucket,
            prepared.key,
            prepared.content_type,
            prepared.content_hash,
            prepared.page_count,
        )

    async def delete(
        self, *, tenant_id: uuid.UUID | str, provider_attempt_id: uuid.UUID | str
    ) -> None:
        key = deterministic_staging_key(
            tenant_id=tenant_id, provider_attempt_id=provider_attempt_id
        )
        try:
            await self._io("delete_object", Bucket=self.config.bucket, Key=key)
        except ClientError as exc:
            if _not_found(exc):
                return
            raise TextractSourceStagingError(
                "ASYNC_PROVIDER_STAGING_CLEANUP_FAILED"
            ) from exc
        except TextractSourceStagingError as exc:
            raise TextractSourceStagingError(
                "ASYNC_PROVIDER_STAGING_CLEANUP_FAILED"
            ) from exc

    async def cleanup_terminal_attempts(
        self, db: AsyncSession, *, batch_size: int = 25
    ) -> int:
        if not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        rows = (
            (
                await db.execute(
                    select(ExtractionProviderJobRecord)
                    .where(
                        ExtractionProviderJobRecord.status.in_(
                            (
                                "COMPLETE",
                                "FAILED_RETRYABLE",
                                "FAILED_TERMINAL",
                                "PROVIDER_UNREACHABLE_MANUAL_REVIEW",
                                "SUPERSEDED",
                            )
                        )
                    )
                    .order_by(ExtractionProviderJobRecord.id)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        cleaned = 0
        for row in rows:
            try:
                await self.delete(tenant_id=row.tenant_id, provider_attempt_id=row.id)
            except TextractSourceStagingError:
                continue
            cleaned += 1
        return cleaned

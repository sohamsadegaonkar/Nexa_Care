from __future__ import annotations

import io
import uuid

import pytest
from botocore.exceptions import ClientError
from pypdf import PdfWriter

from app.services.textract_source_staging import (
    PreparedTextractSource,
    TextractSourceStager,
    TextractSourceStagingError,
    TextractStagingConfig,
    deterministic_staging_key,
    inspect_pdf_page_count,
)


def _pdf(pages: int, *, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    if encrypted:
        writer.encrypt("test-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class _MemoryStorage:
    def __init__(self, source: bytes):
        self.source = source
        self.calls: list[str] = []

    async def get_document_bytes(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> bytes:
        self.calls.append(storage_ref)
        return self.source


class _MemoryS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], dict] = {}
        self.put_calls = 0
        self.delete_calls = 0

    def head_object(self, *, Bucket: str, Key: str):
        try:
            return self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject") from exc

    def put_object(self, **kwargs):
        self.put_calls += 1
        key = (kwargs["Bucket"], kwargs["Key"])
        if key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[key] = {
            "Metadata": kwargs["Metadata"],
            "Body": kwargs["Body"],
            "ContentType": kwargs["ContentType"],
            "ServerSideEncryption": kwargs["ServerSideEncryption"],
            "SSEKMSKeyId": kwargs["SSEKMSKeyId"],
        }

    def delete_object(self, *, Bucket: str, Key: str):
        self.delete_calls += 1
        self.objects.pop((Bucket, Key), None)


def _prepared(
    source: bytes, *, tenant: uuid.UUID, attempt: uuid.UUID, document: uuid.UUID
) -> PreparedTextractSource:
    import hashlib

    return PreparedTextractSource(
        tenant_id=tenant,
        patient_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        source_document_id=document,
        provider_attempt_id=attempt,
        content_hash=hashlib.sha256(source).hexdigest(),
        page_count=inspect_pdf_page_count(source),
        content_type="application/pdf",
        bytes_=source,
        bucket="pilot-bucket",
        key=deterministic_staging_key(tenant_id=tenant, provider_attempt_id=attempt),
    )


def _stager(source: bytes, s3: _MemoryS3) -> TextractSourceStager:
    return TextractSourceStager(
        config=TextractStagingConfig("pilot-bucket", "ap-south-1", "alias/pilot"),
        storage=_MemoryStorage(source),
        s3_client=s3,
    )


def test_independent_pdf_page_count_and_fail_closed_inputs():
    assert inspect_pdf_page_count(_pdf(3)) == 3
    with pytest.raises(TextractSourceStagingError) as malformed:
        inspect_pdf_page_count(b"not a pdf")
    assert malformed.value.code == "ASYNC_PROVIDER_SOURCE_PDF_INVALID"
    with pytest.raises(TextractSourceStagingError) as encrypted:
        inspect_pdf_page_count(_pdf(3, encrypted=True))
    assert encrypted.value.code == "ASYNC_PROVIDER_SOURCE_ENCRYPTED"
    with pytest.raises(TextractSourceStagingError) as single:
        inspect_pdf_page_count(_pdf(1))
    assert single.value.code == "ASYNC_PROVIDER_SOURCE_NOT_MULTIPAGE"


@pytest.mark.asyncio
async def test_staging_is_sse_kms_idempotent_and_cleanup_is_repeatable():
    source = _pdf(3)
    s3 = _MemoryS3()
    stager = _stager(source, s3)
    tenant = uuid.uuid4()
    attempt = uuid.uuid4()
    prepared = _prepared(source, tenant=tenant, attempt=attempt, document=uuid.uuid4())
    first = await stager.stage(prepared)
    second = await stager.stage(prepared)
    assert first.reused is False
    assert second.reused is True
    assert s3.put_calls == 1
    stored = s3.objects[(prepared.bucket, prepared.key)]
    assert stored["Body"] == source
    assert stored["ServerSideEncryption"] == "aws:kms"
    assert stored["SSEKMSKeyId"] == "alias/pilot"
    assert "filename" not in prepared.key.lower()
    assert await stager.delete(tenant_id=tenant, provider_attempt_id=attempt) is None
    assert await stager.delete(tenant_id=tenant, provider_attempt_id=attempt) is None
    assert s3.delete_calls == 2


@pytest.mark.asyncio
async def test_conflicting_existing_staging_source_fails_closed():
    source = _pdf(3)
    s3 = _MemoryS3()
    stager = _stager(source, s3)
    prepared = _prepared(
        source, tenant=uuid.uuid4(), attempt=uuid.uuid4(), document=uuid.uuid4()
    )
    s3.objects[(prepared.bucket, prepared.key)] = {
        "Metadata": {"source-sha256": "0" * 64}
    }
    with pytest.raises(TextractSourceStagingError) as error:
        await stager.stage(prepared)
    assert error.value.code == "ASYNC_PROVIDER_SOURCE_STAGING_CONFLICT"

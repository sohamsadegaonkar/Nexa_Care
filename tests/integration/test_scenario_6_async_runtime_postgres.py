"""Canonical Scenario 6 timeout/recovery proof on disposable PostgreSQL."""

from __future__ import annotations

import hashlib
import io
import os
import time
import uuid
from unittest.mock import AsyncMock

import pytest
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pypdf import PdfWriter
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai.async_textract import AsyncTextractProvider
from app.ai.extractor import ProviderTimeoutError
from app.core.document_processing_gate import DelegatedClinicalTrustError
from app.models.pipeline import (
    DocumentStorage,
    ExtractionCandidateRecord,
    ExtractionDecisionRecord,
    ExtractionJob,
    ExtractionProviderJobRecord,
    ExtractionRoutingRecord,
)
from app.models.provider import HospitalRegistry
from app.services.document_storage import DocumentStorage as DocumentStorageAdapter
from app.services.provider_job_lifecycle import (
    ProviderJobStatus,
    ProviderReconciliationClaim,
    ProviderReconciliationOutcome,
    ReconciliationOutcomeType,
    apply_reconciliation_outcome,
    mark_provider_succeeded,
)
from app.services.textract_async_runtime import (
    prepare_and_create_provider_attempt,
    retrieve_and_complete_provider_attempt,
    start_async_provider_attempt,
)
from app.services.textract_source_staging import (
    TextractSourceStager,
    TextractStagingConfig,
)

pytestmark = pytest.mark.postgres


def _url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is not configured")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


def _pdf(page_count: int = 3) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _block(block_id: str, page: int) -> dict:
    return {
        "BlockType": "LINE",
        "Id": block_id,
        "Text": f"SYNTHETIC PAGE {page}",
        "Confidence": 99.0,
        "Page": page,
        "Geometry": {
            "BoundingBox": {"Left": 0.1, "Top": 0.1, "Width": 0.2, "Height": 0.1}
        },
    }


class _EncryptedArchive(DocumentStorageAdapter):
    def __init__(self, source: bytes, *, key: bytes, object_key: str):
        self._key = key
        self._object_key = object_key
        nonce = b"0123456789ab"
        aad = f"nexa-document-v1\0tenant\0patient\0{object_key}".encode()
        self._ciphertext = nonce + AESGCM(key).encrypt(nonce, source, aad)
        self.decrypted_reads = 0

    async def put_document(
        self, data: bytes, *, tenant_id: str, patient_id: str, mime_type: str
    ):
        raise NotImplementedError

    async def get_document_bytes(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> bytes:
        self.decrypted_reads += 1
        nonce, body = self._ciphertext[:12], self._ciphertext[12:]
        aad = f"nexa-document-v1\0tenant\0patient\0{self._object_key}".encode()
        return AESGCM(self._key).decrypt(nonce, body, aad)

    async def delete_document(
        self, storage_ref: str, *, tenant_id: str, patient_id: str
    ) -> None:
        return None


class _FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], dict] = {}

    def head_object(self, *, Bucket: str, Key: str):
        try:
            return self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject") from exc

    def put_object(self, **kwargs):
        identity = (kwargs["Bucket"], kwargs["Key"])
        if identity in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[identity] = {
            "Body": kwargs["Body"],
            "Metadata": kwargs["Metadata"],
            "ServerSideEncryption": kwargs["ServerSideEncryption"],
            "SSEKMSKeyId": kwargs["SSEKMSKeyId"],
        }

    def delete_object(self, *, Bucket: str, Key: str):
        self.objects.pop((Bucket, Key), None)


class _FakeTextract:
    def __init__(self, s3: _FakeS3):
        self.s3 = s3
        self.job_id = f"synthetic-job-{uuid.uuid4().hex}"
        self.calls = 0
        self.timeout_mode = True
        self.start_requests: list[dict] = []

    def start_document_analysis(self, **kwargs):
        location = kwargs["DocumentLocation"]["S3Object"]
        assert (location["Bucket"], location["Name"]) in self.s3.objects
        self.start_requests.append(kwargs)
        return {"JobId": self.job_id}

    def get_document_analysis(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return {"JobStatus": "SUCCEEDED"}
        if self.timeout_mode and self.calls == 3:
            time.sleep(0.2)
        if self.calls in {2, 4}:
            return {
                "JobStatus": "SUCCEEDED",
                "AnalyzeDocumentModelVersion": "synthetic-model-1",
                "DocumentMetadata": {"Pages": 3},
                "Blocks": [_block(f"block-1-{self.calls}", 1)],
                "NextToken": "page-2",
            }
        if self.calls in {3, 5}:
            return {
                "JobStatus": "SUCCEEDED",
                "AnalyzeDocumentModelVersion": "synthetic-model-1",
                "DocumentMetadata": {"Pages": 3},
                "Blocks": [_block(f"block-2-{self.calls}", 2)],
                "NextToken": "page-3",
            }
        return {
            "JobStatus": "SUCCEEDED",
            "AnalyzeDocumentModelVersion": "synthetic-model-1",
            "DocumentMetadata": {"Pages": 3},
            "Blocks": [_block("block-3-6", 3)],
        }


async def _count(db, model, job_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).select_from(model).where(model.job_id == job_id)
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
async def test_scenario_6_timeout_recovery_preserves_attempt_and_blocks_partial_handoff(
    monkeypatch,
):
    # This Scenario 6 fixture intentionally predates provider-trust rows; it
    # qualifies provider pagination/lifecycle only, not delegated authority.
    trust_guard = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.services.textract_async_runtime.recheck_delegated_document_processing_trust",
        trust_guard,
    )
    engine = create_async_engine(_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    source = _pdf(3)
    tenant, patient, document_id, job_id = (uuid.uuid4() for _ in range(4))
    object_key = f"archive/{document_id}.bin"
    archive = _EncryptedArchive(source, key=b"0" * 32, object_key=object_key)
    s3 = _FakeS3()
    textract_client = _FakeTextract(s3)
    stager = TextractSourceStager(
        config=TextractStagingConfig(
            "synthetic-bucket", "ap-south-1", "alias/synthetic"
        ),
        storage=archive,
        s3_client=s3,
    )
    provider = AsyncTextractProvider(
        region="ap-south-1", timeout_seconds=0.05, client=textract_client
    )
    now_sql = "SELECT now()"
    try:
        async with factory() as db:
            now = (await db.execute(text(now_sql))).scalar_one()
            await db.execute(
                text("INSERT INTO public.patients (patient_uuid) VALUES (:patient)"),
                {"patient": patient},
            )
            db.add(
                HospitalRegistry(
                    id=tenant,
                    facility_code=f"A6-{uuid.uuid4().hex[:20]}",
                    legal_name="A6 synthetic",
                    display_name="A6 synthetic",
                    country_code="IN",
                    is_active=True,
                )
            )
            await db.flush()
            db.add(
                DocumentStorage(
                    id=document_id,
                    patient_id=patient,
                    tenant_id=tenant,
                    uploader_id="A6_SYNTHETIC",
                    storage_ref=f"s3://synthetic-bucket/{object_key}",
                    content_type="application/pdf",
                    size=len(source),
                    content_hash=hashlib.sha256(source).hexdigest(),
                    uploaded_at=now,
                )
            )
            db.add(
                ExtractionJob(
                    id=job_id,
                    patient_id=patient,
                    tenant_id=tenant,
                    uploader_id="A6_SYNTHETIC",
                    authorization_provider_id="A6_SYNTHETIC",
                    consent_request_id="A6_SYNTHETIC",
                    document_id=document_id,
                    document_type="application/pdf",
                    status="extracting",
                    request_id=f"a6-{uuid.uuid4().hex}",
                    attempt_count=1,
                    created_at=now,
                )
            )
            await db.commit()

        async with factory() as db:
            attempt, prepared = await prepare_and_create_provider_attempt(
                db,
                stager=stager,
                job_id=job_id,
                tenant_id=tenant,
                patient_id=patient,
                source_document_id=document_id,
                job_attempt_number=1,
                before_source_retrieval=AsyncMock(),
            )
            assert prepared.page_count == 3
            assert prepared.bytes_ == source
            started = await start_async_provider_attempt(
                db,
                provider_attempt_id=attempt.id,
                prepared=prepared,
                stager=stager,
                provider=provider,
                before_provider_submission=AsyncMock(),
            )
            assert started.provider_job_id == textract_client.job_id
            assert started.expected_page_count == 3
            assert textract_client.start_requests
            assert s3.objects[("synthetic-bucket", prepared.key)]["Body"] == source
            row = (
                await db.execute(
                    select(ExtractionProviderJobRecord).where(
                        ExtractionProviderJobRecord.id == attempt.id
                    )
                )
            ).scalar_one()
            assert (
                await provider.check_status(provider_job_id=row.provider_job_id)
            ).outcome is ReconciliationOutcomeType.SUCCEEDED
            row = await mark_provider_succeeded(
                db, provider_attempt_id=row.id, expected_version=row.version
            )
            await db.commit()
            assert row.provider_job_id == textract_client.job_id

        async with factory() as db:
            with pytest.raises(ProviderTimeoutError):
                await retrieve_and_complete_provider_attempt(
                    db, provider_attempt_id=attempt.id, provider=provider, stager=stager
                )
            row = (
                await db.execute(
                    select(ExtractionProviderJobRecord).where(
                        ExtractionProviderJobRecord.id == attempt.id
                    )
                )
            ).scalar_one()
            assert row.status == "RECONCILING"
            assert row.response_complete is False
            assert row.result_retrieval_complete is False
            assert await _count(db, ExtractionCandidateRecord, job_id) == 0
            assert await _count(db, ExtractionDecisionRecord, job_id) == 0
            assert await _count(db, ExtractionRoutingRecord, job_id) == 0
            claim = ProviderReconciliationClaim(
                provider_attempt_id=row.id,
                job_id=row.job_id,
                provider_adapter=row.provider_adapter,
                provider_contract_version=row.provider_contract_version,
                provider_job_id=row.provider_job_id,
                client_request_token_digest=row.client_request_token_digest,
                provider_request_fingerprint=row.provider_request_fingerprint,
                claimed_version=row.version,
                reconciliation_attempt_number=row.reconciliation_attempt_count,
                reconciliation_deadline_at=row.reconciliation_deadline_at,
            )
            await apply_reconciliation_outcome(
                db,
                claim=claim,
                outcome=ProviderReconciliationOutcome(
                    ReconciliationOutcomeType.SUCCEEDED
                ),
            )
            await db.commit()

        textract_client.timeout_mode = False
        async with factory() as db:
            result = await retrieve_and_complete_provider_attempt(
                db, provider_attempt_id=attempt.id, provider=provider, stager=stager
            )
            assert result.response_complete is True
            row = (
                await db.execute(
                    select(ExtractionProviderJobRecord).where(
                        ExtractionProviderJobRecord.id == attempt.id
                    )
                )
            ).scalar_one()
            assert row.status == "COMPLETE"
            assert row.expected_page_count == 3
            assert row.observed_page_count == 3
            assert row.provider_job_id == textract_client.job_id
            assert row.response_complete is True
            assert row.result_retrieval_complete is True
            assert await _count(db, ExtractionCandidateRecord, job_id) == 0
            assert await _count(db, ExtractionDecisionRecord, job_id) == 0
            assert await _count(db, ExtractionRoutingRecord, job_id) == 0
            assert s3.objects == {}
            # Initial async submission prepares the source exactly once; the
            # start phase receives that prepared value and must not decrypt it
            # a second time.
            assert archive.decrypted_reads == 1

        # A second immutable attempt proves the handoff race: current trust is
        # lost after the staging cleanup but before COMPLETE/result return.
        trust_guard.side_effect = [
            None,
            None,
            None,
            None,
            DelegatedClinicalTrustError("PROFESSIONAL_SUSPENDED"),
        ]
        # Provider job IDs are globally unique across immutable attempts.
        textract_client.job_id = f"synthetic-job-{uuid.uuid4().hex}"
        textract_client.calls = 3
        async with factory() as db:
            denied_attempt, denied_prepared = await prepare_and_create_provider_attempt(
                db,
                stager=stager,
                job_id=job_id,
                tenant_id=tenant,
                patient_id=patient,
                source_document_id=document_id,
                job_attempt_number=2,
                before_source_retrieval=trust_guard,
            )
            await start_async_provider_attempt(
                db,
                provider_attempt_id=denied_attempt.id,
                prepared=denied_prepared,
                stager=stager,
                provider=provider,
                before_provider_submission=trust_guard,
            )
            row = (
                await db.execute(
                    select(ExtractionProviderJobRecord).where(
                        ExtractionProviderJobRecord.id == denied_attempt.id
                    )
                )
            ).scalar_one()
            row = await mark_provider_succeeded(
                db, provider_attempt_id=row.id, expected_version=row.version
            )
            await db.commit()

            with pytest.raises(DelegatedClinicalTrustError) as denial:
                await retrieve_and_complete_provider_attempt(
                    db,
                    provider_attempt_id=denied_attempt.id,
                    provider=provider,
                    stager=stager,
                )
            assert denial.value.code == "PROFESSIONAL_SUSPENDED"

            denied_job = (
                await db.execute(
                    select(ExtractionJob).where(ExtractionJob.id == job_id)
                )
            ).scalar_one()
            denied_row = (
                await db.execute(
                    select(ExtractionProviderJobRecord).where(
                        ExtractionProviderJobRecord.id == denied_attempt.id
                    )
                )
            ).scalar_one()
            assert denied_job.status == "quarantined"
            assert denied_job.error_code == "PROFESSIONAL_SUSPENDED"
            assert denied_row.status == ProviderJobStatus.FAILED_TERMINAL.value
            assert denied_row.result_retrieval_complete is False
            assert s3.objects == {}
            assert await _count(db, ExtractionCandidateRecord, job_id) == 0
            assert await _count(db, ExtractionDecisionRecord, job_id) == 0
            assert await _count(db, ExtractionRoutingRecord, job_id) == 0
            assert archive.decrypted_reads == 2
    finally:
        await engine.dispose()

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from pypdf import PdfWriter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai.extractor import (
    DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
    ExtractionProviderResult,
)
from app.models.ai_models import ExtractedMedicalDocument, ProviderFieldEvidence
from app.models.patient import Patient
from app.models.patient_external_record_import import PatientExternalRecordCandidate
from app.models.patient_records import DocumentReference, TimelineEvent
from app.security.patient_source_malware_scanner import (
    ClamdPatientSourceMalwareScanner,
    MalwareScanOutcome,
    PatientSourceMalwareScannerConfig,
    get_patient_source_malware_scanner_config,
)
from app.services.patient_external_record_extraction import (
    process_patient_external_record,
    retry_patient_external_record,
)
from app.services.patient_external_record_finalization import (
    finalize_patient_external_record,
)
from app.services.patient_external_record_import import (
    read_patient_external_record_source,
    stage_patient_external_record,
)
from app.services.patient_external_record_review import (
    get_patient_external_record_review,
    review_patient_external_record_candidate,
)
from app.services.patient_external_record_source_safety import (
    PatientSourceSafetyError,
    qualify_patient_source_for_extraction,
)


pytestmark = pytest.mark.asyncio

_EICAR = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!"
    b"$H+H*"
)


class _SyntheticReviewableExtractor:
    adapter_identity = "demo"
    contract_version = DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION

    def __init__(self) -> None:
        self.calls = 0

    async def extract_bytes(
        self,
        document_bytes: bytes,
        *,
        mime_type: str,
        request_id: str,
    ) -> ExtractionProviderResult:
        _ = (document_bytes, mime_type, request_id)
        self.calls += 1
        evidence = ProviderFieldEvidence(
            canonical_field_name="hba1c",
            raw_value="6.1%",
            source_text="HbA1c 6.1%",
            page_number=0,
            field_confidence=0.95,
            provider_name="demo",
            provider_api_version="demo-v1",
            extraction_timestamp=datetime.now(timezone.utc),
            source_type="QUERY_RESULT",
        )
        document = ExtractedMedicalDocument(
            patient_name="Synthetic Patient",
            aadhaar_abha_id="SYNTHETIC-ID",
            phone="0000000000",
            diagnoses=[],
            lab_results=[],
            prescriptions=[],
            extraction_confidence=0.95,
            field_evidence=[evidence],
        )
        return ExtractionProviderResult(
            document=document,
            provider_adapter="demo",
            provider_contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
            provider_model_version=None,
            response_complete=True,
            provider_attempt_traces=(),
        )


def _database_url() -> str:
    value = os.environ["TEST_DATABASE_URL"]
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


def _pdf(*, malicious: bool = False) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    data = buffer.getvalue()
    if not malicious:
        return data
    marker = b"%%EOF"
    index = data.rfind(marker)
    assert index >= 0
    return data[:index] + b"% " + _EICAR + b"\n" + data[index:]


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


async def _real_scanner() -> ClamdPatientSourceMalwareScanner:
    config = get_patient_source_malware_scanner_config()
    assert config.provider == "clamd"
    scanner = ClamdPatientSourceMalwareScanner(config)
    assert await scanner.ready() is True
    return scanner


async def _serve_once(
    handler,
) -> tuple[asyncio.AbstractServer, int]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    socket = server.sockets[0]
    return server, int(socket.getsockname()[1])


async def test_malformed_clamd_reply_fails_closed_at_socket_boundary() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(4096)
        writer.write(b"malformed-response\0")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server, port = await _serve_once(handler)
    try:
        scanner = ClamdPatientSourceMalwareScanner(
            PatientSourceMalwareScannerConfig(
                provider="clamd",
                host="127.0.0.1",
                port=port,
                connect_timeout_seconds=1.0,
                scan_timeout_seconds=1.0,
                max_bytes=1024,
                max_signature_age_hours=168,
            )
        )
        result = await scanner.scan(
            b"synthetic",
            content_hash="e" * 64,
            mime_type="application/pdf",
        )
        assert result.outcome is MalwareScanOutcome.UNAVAILABLE
    finally:
        server.close()
        await server.wait_closed()


async def test_delayed_clamd_reply_times_out_fail_closed() -> None:
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(4096)
        await asyncio.sleep(0.2)
        writer.write(b"stream: OK\0")
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server, port = await _serve_once(handler)
    try:
        scanner = ClamdPatientSourceMalwareScanner(
            PatientSourceMalwareScannerConfig(
                provider="clamd",
                host="127.0.0.1",
                port=port,
                connect_timeout_seconds=1.0,
                scan_timeout_seconds=0.05,
                max_bytes=1024,
                max_signature_age_hours=168,
            )
        )
        result = await scanner.scan(
            b"synthetic",
            content_hash="f" * 64,
            mime_type="application/pdf",
        )
        assert result.outcome is MalwareScanOutcome.UNAVAILABLE
    finally:
        server.close()
        await server.wait_closed()


async def test_real_clamd_clean_and_eicar_verdicts_and_exact_hash_binding() -> None:
    scanner = await _real_scanner()
    for data, mime_type in (
        (_pdf(), "application/pdf"),
        (_png(), "image/png"),
        (_jpeg(), "image/jpeg"),
    ):
        digest = hashlib.sha256(data).hexdigest()
        result = await scanner.scan(
            data,
            content_hash=digest,
            mime_type=mime_type,
        )
        assert result.outcome is MalwareScanOutcome.CLEAN
        assert result.content_hash == digest
        decision = await qualify_patient_source_for_extraction(
            data,
            mime_type=mime_type,
            expected_hash=digest,
            scanner=scanner,
        )
        assert decision.content_hash == digest

    malicious = _pdf(malicious=True)
    malicious_hash = hashlib.sha256(malicious).hexdigest()
    verdict = await scanner.scan(
        malicious,
        content_hash=malicious_hash,
        mime_type="application/pdf",
    )
    assert verdict.outcome is MalwareScanOutcome.MALICIOUS
    assert verdict.content_hash == malicious_hash

    with pytest.raises(PatientSourceSafetyError) as caught:
        await qualify_patient_source_for_extraction(
            malicious,
            mime_type="application/pdf",
            expected_hash=malicious_hash,
            scanner=scanner,
        )
    assert caught.value.code == "SOURCE_MALWARE_DETECTED"
    assert caught.value.retryable is False


async def test_real_scanner_clean_patient_flow_reaches_review_save_and_canonical_records(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    patient_id = uuid.uuid4()
    extractor = _SyntheticReviewableExtractor()
    try:
        async with factory() as db:
            db.add(Patient(patient_uuid=patient_id))
            await db.commit()

        async with factory() as db:
            upload = await stage_patient_external_record(
                db,
                patient_id=str(patient_id),
                category_slug="lab_report",
                filename="clean.pdf",
                content_type="application/pdf",
                data=_pdf(),
                request_id=f"d6-clean-{uuid.uuid4().hex}",
            )
            import_id = upload.import_row.id

        with patch(
            "app.services.patient_external_record_extraction.get_medical_document_extractor",
            return_value=extractor,
        ):
            async with factory() as db:
                processed = await process_patient_external_record(
                    db,
                    patient_id=str(patient_id),
                    import_id=import_id,
                )
                assert processed.status == "REVIEW_REQUIRED"
        assert extractor.calls == 1

        async with factory() as db:
            snapshot = await get_patient_external_record_review(
                db,
                patient_id=str(patient_id),
                import_id=import_id,
            )
            assert len(snapshot.items) == 1
            candidate_id = snapshot.items[0].candidate_id

        async with factory() as db:
            reviewed = await review_patient_external_record_candidate(
                db,
                patient_id=str(patient_id),
                import_id=import_id,
                candidate_id=candidate_id,
                decision="accept",
            )
            assert reviewed.status == "READY_TO_SAVE"

        async with factory() as db:
            completed = await finalize_patient_external_record(
                db,
                patient_id=str(patient_id),
                import_id=import_id,
            )
            assert completed.status == "COMPLETED"
            assert completed.final_record_id is not None
            assert completed.timeline_event_id is not None

        async with factory() as db:
            document = await db.get(DocumentReference, completed.final_record_id)
            timeline = await db.get(TimelineEvent, completed.timeline_event_id)
            assert document is not None
            assert document.patient_id == patient_id
            assert timeline is not None
            assert timeline.patient_id == patient_id
            assert timeline.event_type == "DOCUMENT"
    finally:
        await engine.dispose()


async def test_real_clamd_malicious_patient_source_never_reaches_extractor_or_clinical_output(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    patient_id = uuid.uuid4()
    extractor = _SyntheticReviewableExtractor()
    try:
        async with factory() as db:
            db.add(Patient(patient_uuid=patient_id))
            await db.commit()

        async with factory() as db:
            upload = await stage_patient_external_record(
                db,
                patient_id=str(patient_id),
                category_slug="lab_report",
                filename="malicious.pdf",
                content_type="application/pdf",
                data=_pdf(malicious=True),
                request_id=f"d6-malicious-{uuid.uuid4().hex}",
            )
            import_id = upload.import_row.id

        with patch(
            "app.services.patient_external_record_extraction.get_medical_document_extractor",
            return_value=extractor,
        ):
            async with factory() as db:
                blocked = await process_patient_external_record(
                    db,
                    patient_id=str(patient_id),
                    import_id=import_id,
                )
                assert blocked.status == "FAILED_TERMINAL"
                assert blocked.error_code == "SOURCE_MALWARE_DETECTED"
        assert extractor.calls == 0

        async with factory() as db:
            candidate_count = (
                await db.execute(
                    select(func.count(PatientExternalRecordCandidate.id)).where(
                        PatientExternalRecordCandidate.import_id == import_id
                    )
                )
            ).scalar_one()
            document_count = (
                await db.execute(
                    select(func.count(DocumentReference.id)).where(
                        DocumentReference.patient_id == patient_id
                    )
                )
            ).scalar_one()
            timeline_count = (
                await db.execute(
                    select(func.count(TimelineEvent.id)).where(
                        TimelineEvent.patient_id == patient_id
                    )
                )
            ).scalar_one()
            assert candidate_count == 0
            assert document_count == 0
            assert timeline_count == 0

        async with factory() as db:
            with pytest.raises(Exception) as caught:
                await read_patient_external_record_source(
                    db,
                    patient_id=str(patient_id),
                    import_id=import_id,
                )
            assert getattr(caught.value, "status_code", None) == 409
            assert getattr(caught.value, "detail", None) == {
                "error_code": "SOURCE_DOCUMENT_QUARANTINED",
                "retryable": False,
            }
    finally:
        await engine.dispose()


async def test_scanner_outage_is_retryable_and_retry_must_rescan_before_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(_database_url(), pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    patient_id = uuid.uuid4()
    extractor = _SyntheticReviewableExtractor()
    live_port = os.environ["PATIENT_SOURCE_CLAMD_PORT"]
    try:
        async with factory() as db:
            db.add(Patient(patient_uuid=patient_id))
            await db.commit()

        async with factory() as db:
            upload = await stage_patient_external_record(
                db,
                patient_id=str(patient_id),
                category_slug="lab_report",
                filename="retry.pdf",
                content_type="application/pdf",
                data=_pdf(),
                request_id=f"d6-retry-{uuid.uuid4().hex}",
            )
            import_id = upload.import_row.id

        monkeypatch.setenv("PATIENT_SOURCE_CLAMD_PORT", "9")
        with patch(
            "app.services.patient_external_record_extraction.get_medical_document_extractor",
            return_value=extractor,
        ):
            async with factory() as db:
                failed = await process_patient_external_record(
                    db,
                    patient_id=str(patient_id),
                    import_id=import_id,
                )
                assert failed.status == "FAILED_RETRYABLE"
                assert failed.error_code == "SOURCE_MALWARE_SCANNER_UNAVAILABLE"
                assert failed.retryable is True
        assert extractor.calls == 0

        monkeypatch.setenv("PATIENT_SOURCE_CLAMD_PORT", live_port)
        with patch(
            "app.services.patient_external_record_extraction.get_medical_document_extractor",
            return_value=extractor,
        ):
            async with factory() as db:
                retried = await retry_patient_external_record(
                    db,
                    patient_id=str(patient_id),
                    import_id=import_id,
                )
                assert retried.status == "REVIEW_REQUIRED"
        assert extractor.calls == 1
    finally:
        await engine.dispose()

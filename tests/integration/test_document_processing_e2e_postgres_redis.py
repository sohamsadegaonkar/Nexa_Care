"""Canonical disposable HTTP document-processing qualification.

This is intentionally opt-in through the shared real PostgreSQL/Redis fixture.
Only the deterministic provider seam is synthetic; upload, storage, routing,
adjudication, clinical persistence, and audit boundaries remain production code.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_provider
from app.main import app
from app.models.patient_records import LabResult, TimelineEvent
from app.models.pipeline import (
    DocumentStorage,
    ExtractionCandidateRecord,
    ExtractionRoutingRecord,
)
from app.models.shards import NexaVault
from app.services.approved_access_capability import (
    issue_from_approved_request,
    invalidate_request,
)
from app.services.crypto_kms import get_encryption_provider
from app.services.document_storage import get_document_storage
from app.services.audit_outbox_processor import process_outbox_batch
from app.security.audit_context import (
    bind_trusted_audit_hospital,
    reset_trusted_audit_scope,
)

from tests.integration.test_full_loop_postgres_redis import (
    SyntheticTestExtractionProvider,
    _provider,
    _synthetic_extracted_document,
)
from tests.helpers.qualification_infra import seed_qualification_provider_trust

pytest_plugins = ("tests.integration.test_full_loop_postgres_redis",)
pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.redis]
_USER_AGENT = "NexaClinicalSecurityTest/1.0"


def _one_page_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = tempfile.SpooledTemporaryFile()
    writer.write(output)
    output.seek(0)
    return output.read()


async def _session_override(factory):
    async with factory() as session:
        yield session


async def _install_commit_failure(
    db: AsyncSession, patient_id: uuid.UUID
) -> tuple[str, str]:
    suffix = uuid.uuid4().hex
    function_name = f"nexa_qual_e2e_failure_fn_{suffix}"
    trigger_name = f"nexa_qual_e2e_failure_{suffix}"
    await db.execute(
        text(
            f"""
            CREATE FUNCTION public.{function_name}() RETURNS trigger
            LANGUAGE plpgsql
            AS $qualification$
            BEGIN
                IF NEW.patient_id = '{patient_id}'::uuid
                   AND NEW.event_type = 'ADJUDICATION_CLINICAL_COMMIT_COMPLETED' THEN
                    RAISE EXCEPTION 'qualification adjudication commit failure'
                        USING ERRCODE = 'P0001';
                END IF;
                RETURN NEW;
            END;
            $qualification$;
            """
        )
    )
    await db.execute(
        text(
            f"""
            CREATE CONSTRAINT TRIGGER {trigger_name}
            AFTER INSERT ON public.audit_outbox
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION public.{function_name}()
            """
        )
    )
    await db.commit()
    return trigger_name, function_name


async def _remove_commit_failure(
    db: AsyncSession, trigger_name: str, function_name: str
) -> None:
    await db.execute(
        text(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.audit_outbox")
    )
    await db.execute(text(f"DROP FUNCTION IF EXISTS public.{function_name}()"))
    await db.commit()


@pytest.mark.asyncio
async def test_document_processing_http_to_human_adjudicated_clinical_record(
    local_loop_services, monkeypatch
):
    """Qualify one complete synthetic document from HTTP upload to clinical truth."""

    factory, redis, redis_prefix = local_loop_services
    patient_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    provider = _provider(hospital_id, provider_id)
    audit_token = bind_trusted_audit_hospital(str(hospital_id))
    storage_root = tempfile.TemporaryDirectory(prefix="nexa-qual-e2e-")
    document_bytes = _one_page_pdf()
    run_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    request_id = str(uuid.uuid4())
    idempotency_key = f"e2e-upload-{run_id}"
    request_data = {
        "request_id": request_id,
        "provider_id": str(provider_id),
        "hospital_id": str(hospital_id),
        "patient_id": str(patient_id),
        "status": "approved",
        "access_expires_at": (now + timedelta(minutes=30)).isoformat(),
        "purpose": "document_processing",
        "scope": "documents",
        "approved_device_id": str(uuid.uuid4()),
        "approval_fingerprint": "synthetic-e2e-approval",
    }
    capability_key = f"consent_request:{request_id}"
    trigger_name = function_name = None

    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("DOCUMENT_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("DOCUMENT_STORAGE_LOCAL_ROOT", storage_root.name)
    monkeypatch.setenv(
        "DOCUMENT_STORAGE_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
    )
    monkeypatch.setenv("DOCUMENT_EXTRACTION_PROVIDER", "demo")
    monkeypatch.setenv("ENCRYPTION_BACKEND", "local")
    monkeypatch.setenv("KEK_ROOT_SECRET", f"nexa-qualification-{run_id}")

    extracted = _synthetic_extracted_document(
        patient_name="NEXA QUALIFICATION PATIENT", extracted_at=now, run_id=run_id
    )
    provider_seam = SyntheticTestExtractionProvider(extracted, document_bytes)
    monkeypatch.setattr(
        "app.services.pipeline_orchestrator.get_medical_document_extractor",
        lambda _config=None: provider_seam,
    )

    async def db_override():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_current_provider] = lambda: provider
    app.dependency_overrides[get_db_session] = db_override
    try:
        session_token = None
        async with factory() as db:
            await db.execute(
                text("INSERT INTO public.patients (patient_uuid) VALUES (:id)"),
                {"id": patient_id},
            )
            trust_info = await seed_qualification_provider_trust(
                db,
                provider_id=provider_id,
                hospital_id=hospital_id,
                facility_code=f"QUAL-E2E-{run_id[:10]}",
                roles=["clinician"],
                now=now,
                issue_session=True,
                user_agent=_USER_AGENT,
            )
            session_token = trust_info["token"]
            kms = get_encryption_provider()
            await kms.generate_dek(str(patient_id), db)
            encrypted_name = await kms.encrypt_field(
                str(patient_id), "patient_name", "NEXA QUALIFICATION PATIENT", db
            )
            db.add(
                NexaVault(
                    masked_internal_id=str(patient_id),
                    patient_name=encrypted_name.serialize(),
                )
            )
            await db.commit()

        await redis.set(capability_key, json.dumps(request_data), ex=1800)
        token, _ = await issue_from_approved_request(request_data=request_data)
        headers = {
            "X-Consent-Token": token,
            "Idempotency-Key": idempotency_key,
        }
        client_default_headers = {
            "Authorization": f"Bearer {session_token}",
            "X-Hospital-Id": str(hospital_id),
            "User-Agent": _USER_AGENT,
            "X-Consent-Token": token,
        }
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=client_default_headers,
        ) as client:
            upload = await client.post(
                "/api/v2/pipeline/documents/upload",
                data={
                    "patient_id": str(patient_id),
                    "source_system": "synthetic-local",
                },
                files={
                    "file": ("qualification.pdf", document_bytes, "application/pdf")
                },
                headers=headers,
            )
            assert upload.status_code == 202, upload.text
            upload_body = upload.json()
            job_id = uuid.UUID(upload_body["job_id"])
            assert upload_body["duplicate"] is False

            # BackgroundTasks executes the real orchestrator; poll only as a bounded
            # guard for alternate ASGI servers that schedule it after the response.
            status_body = None
            for _ in range(40):
                status = await client.get(
                    f"/api/v2/pipeline/jobs/{job_id}",
                    headers={"X-Consent-Token": token},
                )
                assert status.status_code == 200, status.text
                status_body = status.json()
                if status_body["status"] in {
                    "source_only",
                    "quarantined",
                    "extraction_failed_terminal",
                }:
                    break
                await asyncio.sleep(0.05)
            assert status_body is not None
            assert status_body["status"] == "source_only"
            assert status_body["routing_lane"] == "SOURCE_ONLY"
            assert status_body["auto_commit_enabled"] is False
            assert status_body["clinician_adjudication_required"] is True
            assert status_body["candidate_count"] == 1
            assert status_body["candidates"][0]["field_name"] == "hba1c"
            assert status_body["candidates"][0]["source_page"] == 0

            duplicate = await client.post(
                "/api/v2/pipeline/documents/upload",
                data={"patient_id": str(patient_id)},
                files={
                    "file": ("qualification.pdf", document_bytes, "application/pdf")
                },
                headers={**headers, "Idempotency-Key": f"e2e-replay-{run_id}"},
            )
            assert duplicate.status_code == 202, duplicate.text
            assert duplicate.json()["duplicate"] is True
            assert uuid.UUID(duplicate.json()["job_id"]) == job_id
            assert provider_seam.calls == 1

            collision_bytes = document_bytes + b"\n%collision"
            collision = await client.post(
                "/api/v2/pipeline/documents/upload",
                data={"patient_id": str(patient_id)},
                files={
                    "file": ("qualification.pdf", collision_bytes, "application/pdf")
                },
                headers=headers,
            )
            assert collision.status_code >= 400

            source = await client.get(
                f"/api/v2/pipeline/jobs/{job_id}/document",
                headers={"X-Consent-Token": token},
            )
            # Source retrieval is authorized and returns exact production bytes.
            assert source.status_code == 200, source.text
            assert source.content == document_bytes

            legacy_commit = await client.post(
                f"/api/v2/pipeline/jobs/{job_id}/commit",
                json={},
                headers={"X-Consent-Token": token},
            )
            assert legacy_commit.status_code == 409
            assert (
                legacy_commit.json()["detail"]["error_code"]
                == "SOURCE_ONLY_NOT_COMMITTABLE"
            )

            async with factory() as db:
                route = (
                    await db.execute(
                        select(ExtractionRoutingRecord).where(
                            ExtractionRoutingRecord.job_id == job_id
                        )
                    )
                ).scalar_one()
                stored = (
                    await db.execute(
                        select(DocumentStorage).where(
                            DocumentStorage.id == route.source_document_id
                        )
                    )
                ).scalar_one()
                assert stored.content_hash
                candidates = (
                    (
                        await db.execute(
                            select(ExtractionCandidateRecord).where(
                                ExtractionCandidateRecord.job_id == job_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(candidates) == 1
                assert candidates[0].encrypted_raw_value
                assert "7.2" not in candidates[0].encrypted_raw_value
                assert (
                    await db.execute(
                        select(LabResult).where(LabResult.patient_id == patient_id)
                    )
                ).scalars().all() == []
                assert (
                    await db.execute(
                        select(TimelineEvent).where(
                            TimelineEvent.patient_id == patient_id
                        )
                    )
                ).scalars().all() == []

            # Create and submit through the actual adjudication HTTP routes.
            review_session = f"review-{run_id}"
            case_response = await client.post(
                f"/api/v2/pipeline/routing/{route.id}/adjudication-cases",
                json={
                    "review_session_id": review_session,
                    "idempotency_key": f"case-{run_id}",
                },
            )
            assert case_response.status_code == 201, case_response.text
            case_id = case_response.json()["case_id"]
            effective_at = now.isoformat()
            submission_body = {
                "review_session_id": review_session,
                "idempotency_key": f"submission-{run_id}",
                "outcome": "ACCEPTED",
                "reason_codes": ["SOURCE_VERIFIED"],
                "fields": [
                    {
                        "kind": "LAB_RESULT",
                        "test_name": "HbA1c",
                        "reviewer_entered_value": 7.2,
                        "normalized_value": 7.2,
                        "unit": "%",
                        "reference_range": "4.0-5.6",
                        "is_abnormal": True,
                        "effective_at": effective_at,
                        "page_number": 0,
                        "provenance_type": "HUMAN_VERIFIED",
                    }
                ],
            }
            submission = await client.post(
                f"/api/v2/pipeline/adjudication-cases/{case_id}/submissions",
                json=submission_body,
            )
            assert submission.status_code == 201, submission.text
            submission_id = submission.json()["submission_id"]
            replay_submission = await client.post(
                f"/api/v2/pipeline/adjudication-cases/{case_id}/submissions",
                json=submission_body,
            )
            assert replay_submission.status_code == 201
            assert replay_submission.json()["submission_id"] == submission_id
            collision_submission = await client.post(
                f"/api/v2/pipeline/adjudication-cases/{case_id}/submissions",
                json={**submission_body, "reason_codes": ["MANUAL_TRANSCRIPTION"]},
            )
            assert collision_submission.status_code >= 400

            async with factory() as db:
                assert (
                    await db.execute(
                        select(LabResult).where(LabResult.patient_id == patient_id)
                    )
                ).scalars().all() == []
                assert (
                    await db.execute(
                        select(TimelineEvent).where(
                            TimelineEvent.patient_id == patient_id
                        )
                    )
                ).scalars().all() == []
                trigger_name, function_name = await _install_commit_failure(
                    db, patient_id
                )

            failed_commit = await client.post(
                f"/api/v2/pipeline/adjudication-submissions/{submission_id}/commit",
                headers={"X-Review-Session-ID": review_session},
            )
            assert failed_commit.status_code >= 500
            async with factory() as db:
                assert (
                    await db.execute(
                        select(LabResult).where(LabResult.patient_id == patient_id)
                    )
                ).scalars().all() == []
                assert (
                    await db.execute(
                        select(TimelineEvent).where(
                            TimelineEvent.patient_id == patient_id
                        )
                    )
                ).scalars().all() == []
                await _remove_commit_failure(db, trigger_name, function_name)
                trigger_name = function_name = None

            await invalidate_request(request_id)
            denied_commit = await client.post(
                f"/api/v2/pipeline/adjudication-submissions/{submission_id}/commit",
                headers={"X-Review-Session-ID": review_session},
            )
            assert denied_commit.status_code == 403
            token, _ = await issue_from_approved_request(request_data=request_data)

            committed = await client.post(
                f"/api/v2/pipeline/adjudication-submissions/{submission_id}/commit",
                headers={"X-Review-Session-ID": review_session},
            )
            assert committed.status_code == 200, committed.text
            replay_commit = await client.post(
                f"/api/v2/pipeline/adjudication-submissions/{submission_id}/commit",
                headers={"X-Review-Session-ID": review_session},
            )
            assert replay_commit.status_code == 200
            assert replay_commit.json()["provenance"] == "human_adjudicated"

        async with factory() as db:
            labs = (
                (
                    await db.execute(
                        select(LabResult).where(LabResult.patient_id == patient_id)
                    )
                )
                .scalars()
                .all()
            )
            events = (
                (
                    await db.execute(
                        select(TimelineEvent).where(
                            TimelineEvent.patient_id == patient_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(labs) == 1
            assert labs[0].test_name == "HbA1c"
            assert labs[0].value == "7.2"
            assert labs[0].source == "human_adjudicated"
            assert labs[0].confidence is None
            assert len(events) == 1
            assert events[0].source == "human_adjudicated"
            outbox_rows = (
                (
                    await db.execute(
                        text(
                            "SELECT event_type, payload FROM public.audit_outbox WHERE patient_id = :patient_id"
                        ),
                        {"patient_id": str(patient_id)},
                    )
                )
                .mappings()
                .all()
            )
            event_text = json.dumps([dict(row) for row in outbox_rows], sort_keys=True)
            assert "NEXA QUALIFICATION PATIENT" not in event_text
            assert '"7.2"' not in event_text
            assert any(
                row["event_type"] == "EXTRACTION_JOB_ROUTED" for row in outbox_rows
            )
            assert any(
                row["event_type"] == "ADJUDICATION_CLINICAL_COMMIT_COMPLETED"
                for row in outbox_rows
            )
            stored = (
                (
                    await db.execute(
                        select(DocumentStorage).where(
                            DocumentStorage.patient_id == patient_id
                        )
                    )
                )
                .scalars()
                .one()
            )
            raw = await get_document_storage().get_document_bytes(
                stored.storage_ref,
                tenant_id=str(hospital_id),
                patient_id=str(patient_id),
            )
            assert raw == document_bytes

        # Drain this run's durable audit records so the shared disposable
        # database remains clean for the next qualification invocation.
        async with factory() as db:
            for worker_attempt in range(4):
                processed = await process_outbox_batch(
                    db, worker_id=f"e2e-qualification-{worker_attempt}"
                )
                if processed["claimed"] == 0:
                    break

        files = [path for path in Path(storage_root.name).rglob("*") if path.is_file()]
        assert files
        assert all(
            b"NEXA QUALIFICATION PATIENT" not in path.read_bytes() for path in files
        )
        storage = get_document_storage()
        await storage.delete_document(
            stored.storage_ref, tenant_id=str(hospital_id), patient_id=str(patient_id)
        )
        assert not [
            path for path in Path(storage_root.name).rglob("*") if path.is_file()
        ]
    finally:
        if trigger_name and function_name:
            async with factory() as db:
                await _remove_commit_failure(db, trigger_name, function_name)
        await invalidate_request(request_id)
        await redis.delete(capability_key)
        app.dependency_overrides.pop(get_current_provider, None)
        app.dependency_overrides.pop(get_db_session, None)
        storage_root.cleanup()
        reset_trusted_audit_scope(audit_token)

"""Disposable real-PostgreSQL/Redis local full-loop qualification.

This test is deliberately opt-in.  It refuses non-loopback or non-qualified
database names and never selects a default local service implicitly.
"""

from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai.extractor import (
    DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
    DemoExtractionProvider,
    ExtractionProviderResult,
)
from app.core.database import get_async_engine, get_session_factory
from app.ai.identity_decision import IdentityDecisionState
from app.api.v2 import patient_record_routes
from app.api.v2.consent_routes import _resolve_signed_approval_atomic
from app.api.v2.patient_record_routes import (
    AppendMedicationRequest,
    AppendVitalsRequest,
    append_medications,
    append_vitals,
    get_patient_structured_record,
)
from app.models.ai_models import ExtractedMedicalDocument, ProviderFieldEvidence
from app.models.adjudication import AdjudicationOutcome, AdjudicationReasonCode
from app.models.extraction_decision import DecisionLane, DecisionReason
from app.models.field_evidence import (
    ClinicalRisk,
    ClinicalValueEvidence,
    ConfidenceProvenance,
    ExtractedFieldEvidence,
    IdentityBindingMethod,
    IdentityBindingStatus,
    IdentityEvidence,
    LifecycleEvidence,
    ModelEvidence,
    NormalizationStatus,
    NormalizedBoundingBox,
    PolicyEvidence,
    SnapshotState,
    VerifierOutcome,
    VisualCoverage,
    VisualEvidence,
)
from app.models.patient_device_keys import PatientDeviceKey
from app.models.patient_records import TimelineEvent, Vitals
from app.models.pipeline import (
    DocumentStorage,
    ExtractionCandidateRecord,
    ExtractionDecisionRecord,
    ExtractionJob,
    ExtractionRoutingRecord,
)
from app.models.shards import NexaVault
from app.models.provider import HospitalRegistry
from app.models.provider_context import (
    AffiliationContext,
    AffiliationType,
    HospitalContext,
    ProviderContext,
    ProviderIdentityContext,
)
from app.observability.audit_ledger import (
    append_audit_log,
    read_audit_events,
    read_patient_access_history_events,
)
from app.security.audit_context import (
    AuditContext,
    AuditDomain,
    bind_trusted_audit_hospital,
    reset_trusted_audit_scope,
)
from app.services.adjudication import commit_submission, create_case, submit_case
from app.services.audit_outbox import enqueue_audit_event
from app.services.audit_outbox_processor import process_outbox_batch
from app.services.approved_access_capability import (
    CAPABILITY_PREFIX,
    invalidate_request,
    issue_from_approved_request,
    token_hash,
    validate_document_processing_access,
)
from app.services.extraction_decision_engine import evaluate_extraction_evidence
from app.services.document_storage import get_document_storage
from app.services import pipeline_orchestrator
from app.services.pipeline_orchestrator import process_extraction_job
from app.services.crypto_kms import get_encryption_provider
from app.services.signed_approval_verifier import (
    SignedApprovalVerifier,
    canonical_signed_approval_payload,
)
from app.security.document_processing_policy import DocumentProcessingOperation


pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.redis]


class SyntheticTestExtractionProvider(DemoExtractionProvider):
    """Deterministic offline provider used only at the extractor boundary."""

    def __init__(
        self, document: ExtractedMedicalDocument, expected_bytes: bytes
    ) -> None:
        self.document = document
        self.expected_bytes = expected_bytes
        self.calls = 0

    async def extract_bytes(
        self, document_bytes: bytes, *, mime_type: str, request_id: str
    ) -> ExtractionProviderResult:
        if mime_type != "application/pdf" or not request_id:
            raise AssertionError("synthetic provider received invalid runtime inputs")
        if document_bytes != self.expected_bytes:
            raise AssertionError("runtime storage returned unexpected document bytes")
        self.calls += 1
        return ExtractionProviderResult(
            document=self.document,
            provider_adapter="demo",
            provider_contract_version=DEMO_MEDICAL_DOCUMENT_CONTRACT_VERSION,
            provider_model_version="synthetic-test-v1",
            response_complete=True,
            provider_attempt_traces=(),
        )


def _synthetic_extracted_document(
    *, patient_name: str, extracted_at: datetime, run_id: str
) -> ExtractedMedicalDocument:
    # Evidence hashes are globally unique in the production candidate table.
    # Keep each independent qualification workflow distinct while preserving
    # the provider-to-adapter lineage inside one run.
    identity_block = f"synthetic-identity-block:{run_id}"
    clinical_block = f"synthetic-clinical-block:{run_id}"
    evidence = [
        ProviderFieldEvidence(
            canonical_field_name="patient_name",
            raw_value=patient_name,
            source_text=patient_name,
            page_number=0,
            bounding_box=NormalizedBoundingBox(
                left=0.08, top=0.08, right=0.42, bottom=0.18
            ),
            field_confidence=0.99,
            provider_name="synthetic-test-provider",
            provider_api_version="synthetic-test-v1",
            extraction_timestamp=extracted_at,
            evidence_hash=hashlib.sha256(identity_block.encode()).hexdigest(),
            source_type="QUERY_RESULT",
            source_block_ids=(identity_block,),
        ),
        ProviderFieldEvidence(
            canonical_field_name="hba1c",
            raw_value="7.2 %",
            source_text="HbA1c: 7.2 %",
            page_number=0,
            bounding_box=NormalizedBoundingBox(
                left=0.12, top=0.30, right=0.38, bottom=0.40
            ),
            field_confidence=0.98,
            provider_name="synthetic-test-provider",
            provider_api_version="synthetic-test-v1",
            extraction_timestamp=extracted_at,
            evidence_hash=hashlib.sha256(clinical_block.encode()).hexdigest(),
            source_type="QUERY_RESULT",
            source_block_ids=(clinical_block,),
            normalized_value="7.2",
            raw_unit="%",
            normalized_unit="%",
        ),
    ]
    return ExtractedMedicalDocument(
        patient_name=patient_name,
        phone="",
        aadhaar_abha_id="",
        diagnoses=[],
        lab_results=[],
        prescriptions=[],
        extraction_confidence=0.99,
        field_evidence=evidence,
    )


async def _install_deferred_commit_failure(
    db, patient_id: uuid.UUID
) -> tuple[str, str]:
    suffix = uuid.uuid4().hex
    function_name = f"nexa_qual_commit_failure_fn_{suffix}"
    trigger_name = f"nexa_qual_commit_failure_{suffix}"
    await db.execute(
        text(
            f"""
            CREATE FUNCTION public.{function_name}() RETURNS trigger
            LANGUAGE plpgsql
            AS $qualification$
            BEGIN
                IF NEW.patient_id = '{patient_id}'::uuid
                   AND NEW.event_type = 'PATIENT_RECORD_APPEND_SUCCESS' THEN
                    RAISE EXCEPTION 'qualification commit boundary failure'
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
            FOR EACH ROW
            EXECUTE FUNCTION public.{function_name}()
            """
        )
    )
    await db.commit()
    return trigger_name, function_name


def _required_env() -> tuple[str, str, str]:
    database_url = os.getenv("TEST_DATABASE_URL")
    redis_url = os.getenv("TEST_REDIS_URL")
    redis_prefix = os.getenv("TEST_REDIS_PREFIX")
    if not database_url or not redis_url:
        pytest.skip("TEST_DATABASE_URL and TEST_REDIS_URL are required")
    if os.getenv("NEXA_ALLOW_DISPOSABLE_TEST_DB") != "1":
        pytest.skip("NEXA_ALLOW_DISPOSABLE_TEST_DB=1 is required")
    db_parts = urlsplit(database_url)
    redis_parts = urlsplit(redis_url)
    if db_parts.hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("TEST_DATABASE_URL must be loopback-only")
    if redis_parts.hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("TEST_REDIS_URL must be loopback-only")
    if not (db_parts.path or "").lstrip("/").startswith("nexa_qual_"):
        pytest.fail("TEST_DATABASE_URL must name a nexa_qual_ disposable database")
    if not redis_prefix or not redis_prefix.startswith("nexa-qual-"):
        pytest.fail("TEST_REDIS_PREFIX must be a dedicated nexa-qual- prefix")
    return database_url, redis_url, redis_prefix


def _provider(hospital_id: uuid.UUID, provider_id: uuid.UUID) -> ProviderContext:
    return ProviderContext(
        provider=ProviderIdentityContext(
            provider_id=provider_id,
            display_name="Synthetic qualification clinician",
            contact_email="qualification@example.test",
        ),
        hospital=HospitalContext(
            hospital_id=hospital_id,
            facility_code="QUAL-LOCAL",
            display_name="Synthetic qualification facility",
        ),
        affiliation=AffiliationContext(
            affiliation_id=uuid.uuid4(),
            affiliation_type=AffiliationType.PERMANENT,
            roles=["clinician"],
            is_primary=True,
        ),
    )


def _identity_mismatch_decision() -> tuple[DecisionLane, tuple[DecisionReason, ...]]:
    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    patient_id = "00000000-0000-0000-0000-000000000001"
    tenant_id = "00000000-0000-0000-0000-000000000002"
    document_id = "00000000-0000-0000-0000-000000000003"
    job_id = "00000000-0000-0000-0000-000000000004"
    evidence = ExtractedFieldEvidence(
        evidence_id="synthetic-mismatch-evidence",
        identity=IdentityEvidence(
            patient_id="00000000-0000-0000-0000-000000000099",
            tenant_id=tenant_id,
            organization_id="00000000-0000-0000-0000-000000000005",
            source_document_id=document_id,
            source_document_hash="a" * 64,
            ingestion_id="synthetic-ingestion",
            binding_status=IdentityBindingStatus.MISMATCH,
            binding_method=IdentityBindingMethod.SERVER_JOB_AND_DOCUMENT,
        ),
        clinical_value=ClinicalValueEvidence(
            field_name="synthetic_low_risk_field",
            raw_value="synthetic-value",
            normalized_value="synthetic-value",
            clinical_risk=ClinicalRisk.LOW_RISK,
            normalization_status=NormalizationStatus.NORMALIZED,
        ),
        visual=VisualEvidence(
            page_number=0,
            bounding_box=NormalizedBoundingBox(
                left=0.1, top=0.1, right=0.2, bottom=0.2
            ),
            source_text="synthetic-value",
            source_span_start=0,
            source_span_end=15,
            coverage=VisualCoverage.COMPLETE,
        ),
        model=ModelEvidence(
            provider_name="synthetic-provider",
            model_name="synthetic-model",
            model_version="1",
            extracted_at=now,
            document_confidence=0.99,
            field_confidence=0.99,
            field_confidence_source=ConfidenceProvenance.PROVIDER_FIELD,
            verifier_outcome=VerifierOutcome.AGREED,
            verifier_provider="synthetic-verifier",
            verifier_model="synthetic-verifier-model",
            verifier_version="1",
            provider_evidence_hash="b" * 64,
        ),
        policy=PolicyEvidence(auto_commit_enabled=False),
        lifecycle=LifecycleEvidence(
            job_id=job_id,
            workflow_id="synthetic-workflow",
            request_id="synthetic-request",
            attempt_number=1,
            attempt_id="synthetic-attempt",
            created_at=now,
            extracted_at=now,
            source_received_at=now,
            consent_state=SnapshotState.ACTIVE,
            erasure_state=SnapshotState.NOT_REQUESTED,
        ),
    )
    from app.models.extraction_decision import ExtractionDecisionPolicy

    policy = ExtractionDecisionPolicy(
        patient_id=patient_id,
        tenant_id=tenant_id,
        organization_id="00000000-0000-0000-0000-000000000005",
        source_document_id=document_id,
        evidence_id="synthetic-mismatch-evidence",
        job_id=job_id,
        workflow_id="synthetic-workflow",
        request_id="synthetic-request",
        attempt_id="synthetic-attempt",
    )
    decision = evaluate_extraction_evidence(
        evidence=evidence,
        policy=policy,
        decision_id_factory=lambda: "synthetic-decision",
        evaluated_at=now,
    )
    return decision.lane, decision.reasons


@pytest_asyncio.fixture
async def local_loop_services(monkeypatch):
    database_url, redis_url, redis_prefix = _required_env()
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(redis_url, decode_responses=True)
    previous_runtime_database = os.environ.get("DATABASE_URL")
    previous_runtime_redis = os.environ.get("UPSTASH_REDIS_URL")
    os.environ["DATABASE_URL"] = database_url
    os.environ["UPSTASH_REDIS_URL"] = redis_url
    # Earlier suite tests can initialize the cached application engine before
    # this opt-in fixture installs its isolated disposable runtime URL. Reset
    # only those cache entries so the real outbox processor shares this test's
    # explicitly configured loopback database rather than a stale engine.
    get_session_factory.cache_clear()
    get_async_engine.cache_clear()
    # Reuse one real client so the test owns and closes every Redis connection
    # created by the capability service.
    monkeypatch.setattr(
        "app.services.approved_access_capability.get_async_redis_client",
        lambda: redis,
    )
    await redis.ping()
    try:
        yield factory, redis, redis_prefix
    finally:
        close = getattr(redis, "aclose", None) or getattr(redis, "close")
        result = close()
        if hasattr(result, "__await__"):
            await result
        pool_close = getattr(redis.connection_pool, "aclose", None)
        if pool_close is not None:
            await pool_close()
        else:
            await redis.connection_pool.disconnect(inuse_connections=True)
        await asyncio.sleep(0.1)
        await engine.dispose()
        if previous_runtime_database is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_runtime_database
        if previous_runtime_redis is None:
            os.environ.pop("UPSTASH_REDIS_URL", None)
        else:
            os.environ["UPSTASH_REDIS_URL"] = previous_runtime_redis
        get_session_factory.cache_clear()
        get_async_engine.cache_clear()


@pytest.mark.asyncio
async def test_local_postgres_redis_full_loop(local_loop_services, monkeypatch):
    """Run the maximum local clinical workflow without cloud dependencies."""
    factory, redis, prefix = local_loop_services
    run_id = uuid.uuid4()
    patient_id = uuid.uuid4()
    hospital_a = uuid.uuid4()
    hospital_b = uuid.uuid4()
    provider_id = uuid.uuid4()
    document_id = uuid.uuid4()
    job_id = uuid.uuid4()
    device_id = uuid.uuid4()
    provider = _provider(hospital_a, provider_id)
    audit_token = bind_trusted_audit_hospital(str(hospital_a))
    capability_request = f"loop-capability-{uuid.uuid4().hex}"
    challenge_request = f"{prefix}{uuid.uuid4()}"
    challenge_nonce = f"{prefix}{uuid.uuid4().hex}"
    storage_root = tempfile.TemporaryDirectory(prefix="nexa-qual-doc-")
    document_bytes = b"%PDF-qualification-synthetic-document%"
    storage = None
    stored_document = None

    try:
        # Real Redis challenge consumption and expiry/replay controls.
        challenge_key = f"consent_request:{challenge_request}"
        nonce_key = f"biometric_nonce:{challenge_nonce}:used"
        await redis.set(
            challenge_key,
            json.dumps({"status": "pending", "challenge_nonce": challenge_nonce}),
            ex=30,
        )
        first = await _resolve_signed_approval_atomic(
            redis,
            challenge_request,
            challenge_nonce,
            {"status": "approved", "challenge_nonce": challenge_nonce},
            30,
        )
        replay = await _resolve_signed_approval_atomic(
            redis,
            challenge_request,
            challenge_nonce,
            {"status": "approved", "challenge_nonce": challenge_nonce},
            30,
        )
        assert first is True and replay is False
        assert await redis.exists(nonce_key) == 1

        expired_request = f"{prefix}{uuid.uuid4()}"
        expired_nonce = f"{prefix}{uuid.uuid4().hex}"
        expired_key = f"consent_request:{expired_request}"
        await redis.set(
            expired_key,
            json.dumps({"status": "pending", "challenge_nonce": expired_nonce}),
            ex=1,
        )
        await asyncio.sleep(1.2)
        assert (
            await _resolve_signed_approval_atomic(
                redis, expired_request, expired_nonce, {"status": "approved"}, 1
            )
            is False
        )

        # Ephemeral software P-256 signing through the production verifier.
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        now = datetime.now(timezone.utc)
        monkeypatch.setenv("ENVIRONMENT", "test")
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.setenv("DOCUMENT_STORAGE_PROVIDER", "local")
        monkeypatch.setenv("DOCUMENT_STORAGE_LOCAL_ROOT", storage_root.name)
        monkeypatch.setenv(
            "DOCUMENT_STORAGE_ENCRYPTION_KEY",
            base64.urlsafe_b64encode(bytes(range(32))).decode("ascii"),
        )
        monkeypatch.setenv("DOCUMENT_EXTRACTION_PROVIDER", "demo")
        monkeypatch.setenv("ENCRYPTION_BACKEND", "local")
        monkeypatch.setenv("KEK_ROOT_SECRET", "nexa-qualification-local-kek")
        extracted_document = _synthetic_extracted_document(
            patient_name="Synthetic Qualification Patient",
            extracted_at=now,
            run_id=str(run_id),
        )
        extraction_provider = SyntheticTestExtractionProvider(
            extracted_document, document_bytes
        )
        monkeypatch.setattr(
            pipeline_orchestrator,
            "get_medical_document_extractor",
            lambda _config=None: extraction_provider,
        )
        storage = get_document_storage()
        stored_document = await storage.put_document(
            document_bytes,
            tenant_id=str(hospital_a),
            patient_id=str(patient_id),
            mime_type="application/pdf",
        )
        issued_at = now.isoformat()
        expires_at = (now + timedelta(minutes=5)).isoformat()
        signed_payload = canonical_signed_approval_payload(
            request_id=challenge_request,
            patient_id=str(patient_id),
            provider_id=str(provider_id),
            challenge_nonce=challenge_nonce,
            decision="approved",
            purpose="routine_checkup",
            scope="full",
            issued_at=issued_at,
            expires_at=expires_at,
            access_duration=300,
            device_id=str(device_id),
        )
        signature = private_key.sign(signed_payload, ec.ECDSA(hashes.SHA256()))

        async with factory() as db:
            await db.execute(
                text("INSERT INTO public.patients (patient_uuid) VALUES (:id)"),
                {"id": patient_id},
            )
            db.add(
                HospitalRegistry(
                    id=hospital_a,
                    facility_code=f"QUAL-A-{uuid.uuid4().hex[:10]}",
                    legal_name="Synthetic qualification hospital A",
                    display_name="Synthetic qualification hospital A",
                    country_code="IN",
                    is_active=True,
                )
            )
            db.add(
                HospitalRegistry(
                    id=hospital_b,
                    facility_code=f"QUAL-B-{uuid.uuid4().hex[:10]}",
                    legal_name="Synthetic qualification hospital B",
                    display_name="Synthetic qualification hospital B",
                    country_code="IN",
                    is_active=True,
                )
            )
            await db.flush()
            kms = get_encryption_provider()
            await kms.generate_dek(str(patient_id), db)
            encrypted_name = await kms.encrypt_field(
                str(patient_id), "patient_name", "Synthetic Qualification Patient", db
            )
            db.add(
                NexaVault(
                    masked_internal_id=str(patient_id),
                    patient_name=encrypted_name.serialize(),
                )
            )
            db.add(
                PatientDeviceKey(
                    id=device_id,
                    patient_id=patient_id,
                    device_public_key=public_key,
                    device_label="synthetic software key",
                    platform="test",
                    key_algorithm="ECDSA-P256",
                    status="active",
                    enrolled_at=now,
                )
            )
            db.add(
                DocumentStorage(
                    id=document_id,
                    patient_id=patient_id,
                    tenant_id=hospital_a,
                    uploader_id=str(provider_id),
                    storage_ref=stored_document.storage_ref,
                    content_type="application/pdf",
                    size=stored_document.size,
                    content_hash=stored_document.content_hash,
                    original_filename=None,
                    upload_purpose="synthetic qualification",
                    consent_session_id=capability_request,
                    source_system="synthetic-local",
                    uploaded_at=now,
                )
            )
            db.add(
                ExtractionJob(
                    id=job_id,
                    patient_id=patient_id,
                    tenant_id=hospital_a,
                    uploader_id=str(provider_id),
                    authorization_provider_id=str(provider_id),
                    consent_request_id=capability_request,
                    document_id=document_id,
                    document_type="synthetic",
                    status="queued",
                    request_id=f"job-{uuid.uuid4().hex}",
                    attempt_count=0,
                    retryable=False,
                    version=1,
                    created_at=now,
                )
            )
            await db.flush()
            signed_result = await SignedApprovalVerifier().verify_signed_approval(
                db,
                str(patient_id),
                challenge_request,
                challenge_nonce,
                "approved",
                base64.b64encode(signature).decode("ascii"),
                expires_at,
                issued_at,
                provider_id=str(provider_id),
                scope="full",
                purpose="routine_checkup",
                access_duration=300,
                device_id=str(device_id),
            )
            assert signed_result.verified is True
            await db.commit()

        # Test-auth seam: the patient approval envelope is seeded locally; the
        # capability and all subsequent validation are real Redis operations.
        access_expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=20)
        ).isoformat()
        request_data = {
            "request_id": capability_request,
            "provider_id": str(provider_id),
            "hospital_id": str(hospital_a),
            "patient_id": str(patient_id),
            "status": "approved",
            "access_expires_at": access_expires_at,
            "purpose": "document_processing",
            "scope": "documents",
            "approved_device_id": str(device_id),
            "approval_fingerprint": "synthetic-approval-fingerprint",
        }
        capability_request_key = f"consent_request:{capability_request}"
        await redis.set(capability_request_key, json.dumps(request_data), ex=1200)
        token, _ = await issue_from_approved_request(request_data=request_data)
        digest = token_hash(token)
        assert await redis.ttl(f"{CAPABILITY_PREFIX}{digest}") > 0
        assert (
            await validate_document_processing_access(
                token=token,
                patient_id=str(patient_id),
                provider_id=str(provider_id),
                hospital_id=str(hospital_a),
                required_operation=DocumentProcessingOperation.REVIEW_EXTRACTED_FIELDS,
                expected_request_id=capability_request,
            )
        ) is not None

        # The production orchestrator now owns extraction, identity assessment,
        # evidence adaptation, routing, candidate persistence, and job status.
        async with factory() as db:
            job_before = (
                await db.execute(
                    select(ExtractionJob).where(ExtractionJob.id == job_id)
                )
            ).scalar_one()
            patient_binding_before = job_before.patient_id
            job_before_status = job_before.status
            source_only_preseeded = job_before.status == "source_only"
            assert source_only_preseeded is False
            assert job_before_status == "queued"
            orchestrator_result = await process_extraction_job(str(job_id), db)
            job_after = (
                await db.execute(
                    select(ExtractionJob).where(ExtractionJob.id == job_id)
                )
            ).scalar_one()
            patient_binding_after = job_after.patient_id
            assert orchestrator_result["status"] == "source_only"
            assert job_after.status == "source_only"
            source_only_computed_by_runtime = job_after.status == "source_only"
            assert source_only_computed_by_runtime is True
            assert patient_binding_before == patient_binding_after == patient_id
            assert extraction_provider.calls == 1

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
            orchestrator_candidate = candidates[0]
            assert orchestrator_candidate.field_name == "hba1c"
            assert orchestrator_candidate.source_page == 0
            assert orchestrator_candidate.source_bbox == [0.12, 0.30, 0.38, 0.40]
            assert orchestrator_candidate.field_confidence == 0.98
            assert orchestrator_candidate.provider_name == "synthetic-test-provider"
            assert orchestrator_candidate.provider_version == "synthetic-test-v1"
            assert orchestrator_candidate.lane == DecisionLane.SOURCE_ONLY.value
            assert orchestrator_candidate.routing_eligible is True

            route = (
                await db.execute(
                    select(ExtractionRoutingRecord).where(
                        ExtractionRoutingRecord.job_id == job_id
                    )
                )
            ).scalar_one()
            decision = (
                await db.execute(
                    select(ExtractionDecisionRecord).where(
                        ExtractionDecisionRecord.id == route.decision_id
                    )
                )
            ).scalar_one()
            assert route.lane == DecisionLane.SOURCE_ONLY.value
            assert route.status == "SOURCE_RETAINED"
            assert decision.lane == DecisionLane.SOURCE_ONLY.value
            assert str(decision.evidence_id) == str(orchestrator_candidate.evidence_id)
            routing_audit = (
                await db.execute(
                    text(
                        "SELECT payload FROM public.audit_outbox "
                        "WHERE event_type = 'EXTRACTION_JOB_ROUTED' "
                        "AND idempotency_key = :idempotency_key "
                        "AND patient_id = :patient_id"
                    ),
                    {
                        "idempotency_key": (
                            f"extraction:{job_id}:{job_after.attempt_count}:routed"
                        ),
                        "patient_id": str(patient_id),
                    },
                )
            ).scalar_one()
            assert routing_audit["target_id"] == str(job_id)
            assert (
                routing_audit["metadata"]["identity_state"]
                == IdentityDecisionState.IDENTITY_CONFIRMED.value
            )

        assert job_before_status == "queued"
        assert orchestrator_candidate.id is not None
        assert route.id is not None

        # Actual SOURCE_ONLY adjudication through the orchestrator-created route.
        async with factory() as db:
            case = await create_case(
                db,
                provider=provider,
                idempotency_key=f"case-{uuid.uuid4().hex}",
                review_session_id=f"review-{uuid.uuid4().hex}",
                routing_id=route.id,
            )
            assert case.routing_id == route.id
            assert case.decision_id == decision.id
            assert case.routing_id is not None and case.decision_id is not None
            await db.commit()
        async with factory() as db:
            submission = await submit_case(
                db,
                case_id=case.id,
                provider=provider,
                review_session_id=case.review_session_id,
                outcome=AdjudicationOutcome.ACCEPTED,
                fields=[
                    {
                        "kind": "VITAL",
                        "vital_type": "HEART_RATE",
                        "reviewer_entered_value": 72.0,
                        "normalized_value": 72.0,
                        "unit": "beats/min",
                        "effective_at": now,
                        "page_number": 0,
                        "provenance_type": "HUMAN_VERIFIED",
                    }
                ],
                reason_codes=[AdjudicationReasonCode.SOURCE_VERIFIED],
                idempotency_key=f"submission-{uuid.uuid4().hex}",
            )
            committed_case = await commit_submission(
                db,
                submission_id=submission.id,
                provider=provider,
                review_session_id=case.review_session_id,
                before_clinical_mutation=AsyncMock(return_value=provider),
            )
            assert committed_case.clinical_committed_at is not None
            await db.commit()

        # Exercise the repaired clinical-write transaction and read it back via
        # the application service boundary, not only direct SQL.
        async with factory() as db:
            result = await append_vitals(
                str(patient_id),
                AppendVitalsRequest(
                    systolic_bp=130,
                    diastolic_bp=80,
                    heart_rate=72,
                    temperature_celsius=36.7,
                    sp_o2_percentage=98,
                    recorded_at=now,
                    source="human_adjudicated",
                    source_document_id=document_id,
                ),
                provider=provider,
                capability=object(),
                db=db,
            )
            assert result["status"] == "committed"
            success_key = f"patient-record-append:vitals:{result['record_id']}"
            success_row = (
                (
                    await db.execute(
                        text(
                            "SELECT event_type, patient_id, chain_partition, payload "
                            "FROM public.audit_outbox WHERE idempotency_key = :key"
                        ),
                        {"key": success_key},
                    )
                )
                .mappings()
                .one()
            )
            assert success_row["event_type"] == "PATIENT_RECORD_APPEND_SUCCESS"
            assert str(success_row["patient_id"]) == str(patient_id)
            assert success_row["chain_partition"].startswith("hospital:")
            assert success_row["payload"]["metadata"] == {
                "record_id": result["record_id"],
                "type": "vitals",
            }
        async with factory() as db:
            readback = await get_patient_structured_record(
                str(patient_id), provider=provider, capability=object(), db=db
            )
            assert any(row["value"] == "130/80" for row in readback["vitals"])
            assert any(
                row["source"] == "human_adjudicated" for row in readback["vitals"]
            )

        # Real audit-outbox failure after flush must roll the clinical rows back.
        async def fail_enqueue(*args, **kwargs):
            raise RuntimeError("synthetic outbox staging failure")

        monkeypatch.setattr(patient_record_routes, "enqueue_audit_event", fail_enqueue)
        failed_patient = uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text("INSERT INTO public.patients (patient_uuid) VALUES (:id)"),
                {"id": failed_patient},
            )
            await db.commit()
        try:
            async with factory() as db:
                with pytest.raises(HTTPException) as failure:
                    await append_medications(
                        str(failed_patient),
                        AppendMedicationRequest(
                            name="synthetic medicine",
                            strength="10 mg",
                            frequency="once daily",
                            prescribed_at=now,
                            source="human_adjudicated",
                        ),
                        provider=provider,
                        capability=object(),
                        db=db,
                    )
                assert failure.value.status_code == 503
                assert (
                    failure.value.detail["error_code"] == "AUDIT_DURABILITY_UNAVAILABLE"
                )
        finally:
            monkeypatch.setattr(
                patient_record_routes,
                "enqueue_audit_event",
                enqueue_audit_event,
            )
        async with factory() as db:
            assert (
                await db.execute(
                    select(Vitals).where(Vitals.patient_id == failed_patient)
                )
            ).scalars().all() == []
            assert (
                await db.execute(
                    select(TimelineEvent).where(
                        TimelineEvent.patient_id == failed_patient
                    )
                )
            ).scalars().all() == []
            assert (
                await db.execute(
                    text(
                        "SELECT count(*) FROM public.audit_outbox "
                        "WHERE patient_id = :patient_id "
                        "AND event_type = 'PATIENT_RECORD_APPEND_SUCCESS'"
                    ),
                    {"patient_id": str(failed_patient)},
                )
            ).scalar_one() == 0

        # Process all durable events through the actual outbox worker.
        async with factory() as db:
            processed = await process_outbox_batch(db, worker_id="local-qualification")
            assert processed["claimed"] >= 1
            assert processed["processed"] == processed["claimed"]
            replay = await process_outbox_batch(
                db, worker_id="local-qualification-replay"
            )
            assert replay["claimed"] == 0

        # Same patient UUID in two hospital partitions remains isolated.
        event_keys = {}
        for hospital_id, label in ((hospital_a, "A"), (hospital_b, "B")):
            key = f"tenant-isolation-{label}-{uuid.uuid4().hex}"
            event_keys[label] = key
            async with factory() as db:
                await enqueue_audit_event(
                    db,
                    audit_context=AuditContext.for_hospital(
                        hospital_id=str(hospital_id), domain=AuditDomain.PATIENT_RECORD
                    ),
                    idempotency_key=key,
                    actor_id=str(provider_id),
                    event_type=f"QUALIFICATION_HOSPITAL_{label}",
                    target_id=str(patient_id),
                    patient_id=str(patient_id),
                    metadata={"synthetic": True, "hospital": label},
                )
                await db.commit()
        async with factory() as db:
            processed = await process_outbox_batch(
                db, worker_id="local-qualification-isolation"
            )
            assert processed["processed"] >= 2
        events_a = await read_audit_events(
            str(patient_id),
            audit_context=AuditContext.for_hospital(
                hospital_id=str(hospital_a), domain=AuditDomain.PATIENT_RECORD
            ),
        )
        events_b = await read_audit_events(
            str(patient_id),
            audit_context=AuditContext.for_hospital(
                hospital_id=str(hospital_b), domain=AuditDomain.PATIENT_RECORD
            ),
        )
        assert any(row["event_type"] == "QUALIFICATION_HOSPITAL_A" for row in events_a)
        assert not any(
            row["event_type"] == "QUALIFICATION_HOSPITAL_B" for row in events_a
        )
        assert any(row["event_type"] == "QUALIFICATION_HOSPITAL_B" for row in events_b)
        assert not any(
            row["event_type"] == "QUALIFICATION_HOSPITAL_A" for row in events_b
        )

        # Patient-facing access history is a projection, not raw ledger rows.
        await append_audit_log(
            audit_context=AuditContext.for_hospital(
                hospital_id=str(hospital_a), domain=AuditDomain.PATIENT_RECORD
            ),
            actor_uid=str(provider_id),
            event_type="PATIENT_RECORD_READ_SUCCESS",
            target_id=str(patient_id),
            status="SUCCESS",
            metadata={
                "access_type": "provider",
                "audit_transaction_id": f"read-{uuid.uuid4().hex}",
            },
        )
        async with factory() as db:
            history = await read_patient_access_history_events(
                db, str(patient_id), limit=20
            )
            assert any(
                row["event_type"] == "PATIENT_RECORD_READ_SUCCESS" for row in history
            )

        # Identity mismatch remains quarantine-only and cannot become SOURCE_ONLY.
        lane, reasons = _identity_mismatch_decision()
        assert lane is DecisionLane.QUARANTINE
        assert DecisionReason.IDENTITY_MISMATCH in reasons

        # Patient-owned revocation primitive removes Redis authority and denies
        # the next check.  The external OTP/authentication seam is intentionally
        # not called in this local qualification.
        await invalidate_request(capability_request)
        await redis.delete(capability_request_key)
        assert (
            await validate_document_processing_access(
                token=token,
                patient_id=str(patient_id),
                provider_id=str(provider_id),
                hospital_id=str(hospital_a),
                required_operation=DocumentProcessingOperation.REVIEW_EXTRACTED_FIELDS,
                expected_request_id=capability_request,
            )
        ) is None
    finally:
        await redis.delete(
            f"consent_request:{challenge_request}",
            f"biometric_nonce:{challenge_nonce}:used",
            f"consent_request:{capability_request}",
        )
        if storage is not None and stored_document is not None:
            await storage.delete_document(
                stored_document.storage_ref,
                tenant_id=str(hospital_a),
                patient_id=str(patient_id),
            )
        storage_root.cleanup()
        reset_trusted_audit_scope(audit_token)


@pytest.mark.asyncio
async def test_local_postgres_commit_failure_rollback(local_loop_services):
    """A deferred PostgreSQL fault rolls back clinical, timeline, and outbox rows."""
    factory, _redis, _prefix = local_loop_services
    patient_id = uuid.uuid4()
    hospital_id = uuid.uuid4()
    provider_id = uuid.uuid4()
    provider = _provider(hospital_id, provider_id)
    audit_token = bind_trusted_audit_hospital(str(hospital_id))
    trigger_name = function_name = None
    try:
        async with factory() as db:
            await db.execute(
                text("INSERT INTO public.patients (patient_uuid) VALUES (:id)"),
                {"id": patient_id},
            )
            db.add(
                HospitalRegistry(
                    id=hospital_id,
                    facility_code=f"QUAL-COMMIT-{uuid.uuid4().hex[:10]}",
                    legal_name="Synthetic commit-failure hospital",
                    display_name="Synthetic commit-failure hospital",
                    country_code="IN",
                    is_active=True,
                )
            )
            await db.commit()
            trigger_name, function_name = await _install_deferred_commit_failure(
                db, patient_id
            )
            trigger_metadata = (
                (
                    await db.execute(
                        text(
                            "SELECT t.tgdeferrable, t.tginitdeferred "
                            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                            "WHERE c.relname = 'audit_outbox' AND t.tgname = :name"
                        ),
                        {"name": trigger_name},
                    )
                )
                .mappings()
                .one()
            )
            assert trigger_metadata["tgdeferrable"] is True
            assert trigger_metadata["tginitdeferred"] is True

        flushed = set()

        async with factory() as db:
            sync_bind = db.sync_session.bind

            def capture_sql(_conn, _cursor, statement, _parameters, _context, _many):
                sql = str(statement).lower()
                if "insert into patient_vitals" in sql:
                    flushed.add("clinical")
                if "insert into timeline_events" in sql:
                    flushed.add("timeline")
                if "insert into public.audit_outbox" in sql:
                    flushed.add("outbox")

            event.listen(sync_bind, "before_cursor_execute", capture_sql)
            try:
                with pytest.raises(HTTPException) as failure:
                    await append_vitals(
                        str(patient_id),
                        AppendVitalsRequest(
                            systolic_bp=124,
                            diastolic_bp=78,
                            heart_rate=70,
                            temperature_celsius=36.6,
                            sp_o2_percentage=98,
                            recorded_at=datetime.now(timezone.utc),
                            source="human_adjudicated",
                        ),
                        provider=provider,
                        capability=object(),
                        db=db,
                    )
            finally:
                event.remove(sync_bind, "before_cursor_execute", capture_sql)
            assert failure.value.status_code == 503
            assert (
                failure.value.detail["error_code"] == "PATIENT_RECORD_WRITE_UNAVAILABLE"
            )

        assert flushed == {"clinical", "timeline", "outbox"}

        async with factory() as db:
            assert (
                await db.execute(select(Vitals).where(Vitals.patient_id == patient_id))
            ).scalars().all() == []
            assert (
                await db.execute(
                    select(TimelineEvent).where(TimelineEvent.patient_id == patient_id)
                )
            ).scalars().all() == []
            assert (
                await db.execute(
                    text(
                        "SELECT count(*) FROM public.audit_outbox "
                        "WHERE patient_id = :patient_id "
                        "AND event_type = 'PATIENT_RECORD_APPEND_SUCCESS'"
                    ),
                    {"patient_id": str(patient_id)},
                )
            ).scalar_one() == 0
    finally:
        if trigger_name is not None and function_name is not None:
            async with factory() as db:
                await db.execute(
                    text(
                        f"DROP TRIGGER IF EXISTS {trigger_name} "
                        "ON public.audit_outbox"
                    )
                )
                await db.execute(
                    text(f"DROP FUNCTION IF EXISTS public.{function_name}()")
                )
                await db.commit()
        reset_trusted_audit_scope(audit_token)

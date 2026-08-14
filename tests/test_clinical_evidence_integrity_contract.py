"""Unit contracts for conservative clinical-fact and source provenance logic."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.ai_models import ProviderFieldEvidence
from app.services.clinical_evidence_integrity import (
    ClinicalEvidenceIntegrityError,
    SourceRelationType,
    clinical_fact_key,
    create_source_relationship,
)
from app.models.field_evidence import EvidenceIssue
from app.models.field_evidence import ClinicalValueEvidence
from app.services import pipeline_orchestrator


def test_clinical_fact_key_requires_explicit_identity_and_excludes_value() -> None:
    assert clinical_fact_key("glucose", None) is None
    key = clinical_fact_key(" glucose ", "collection-2026-08-14-fasting")
    assert key == clinical_fact_key("GLUCOSE", "collection-2026-08-14-fasting")
    assert key != clinical_fact_key("glucose", "collection-2026-08-14-random")


def test_provider_json_cannot_supply_trusted_clinical_fact_identity() -> None:
    with pytest.raises(ValidationError, match="clinical_fact_id"):
        ProviderFieldEvidence.model_validate(
            {
                "canonical_field_name": "blood_glucose",
                "raw_value": "101",
                "provider_name": "aws_textract",
                "provider_api_version": "qualification/1.0",
                "extraction_timestamp": datetime.now(timezone.utc),
                "clinical_fact_id": "provider-controlled-identity",
            }
        )

    evidence = ProviderFieldEvidence(
        canonical_field_name="blood_glucose",
        raw_value="101",
        provider_name="aws_textract",
        provider_api_version="qualification/1.0",
        extraction_timestamp=datetime.now(timezone.utc),
        source_type="QUERY_RESULT",
    )
    assert evidence.trusted_clinical_fact_id is None


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def all(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _DB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.added = []

    async def execute(self, _statement):
        return _Scalar(self.responses.pop(0))

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_source_relationship_rejects_cross_patient() -> None:
    tenant, patient, other_patient, source, related = (uuid4() for _ in range(5))
    db = _DB(
        [
            [
                SimpleNamespace(
                    id=source,
                    tenant_id=tenant,
                    patient_id=patient,
                    uploader_id="reviewer-qualification",
                ),
                SimpleNamespace(
                    id=related,
                    tenant_id=tenant,
                    patient_id=other_patient,
                    uploader_id="reviewer-qualification",
                ),
            ]
        ]
    )
    with pytest.raises(ClinicalEvidenceIntegrityError, match="PATIENT_MISMATCH"):
        await create_source_relationship(
            db,
            source_document_id=source,
            related_document_id=related,
            tenant_id=tenant,
            patient_id=patient,
            relation_type=SourceRelationType.SUPERSEDES,
            workflow_id="workflow-qualification",
            created_by="reviewer-qualification",
            authorization_provider_id="reviewer-qualification",
            authorization_hospital_id=tenant,
            consent_request_id="workflow-qualification",
            created_at=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_source_relationship_rejects_two_node_cycle() -> None:
    tenant, patient, source, related = (uuid4() for _ in range(4))
    documents = [
        SimpleNamespace(
            id=source,
            tenant_id=tenant,
            patient_id=patient,
            uploader_id="reviewer-qualification",
        ),
        SimpleNamespace(
            id=related,
            tenant_id=tenant,
            patient_id=patient,
            uploader_id="reviewer-qualification",
        ),
    ]
    reverse = SimpleNamespace(
        tenant_id=tenant,
        patient_id=patient,
        source_document_id=related,
        related_document_id=source,
    )
    db = _DB([documents, SimpleNamespace(), [SimpleNamespace()], None, reverse])
    capability = SimpleNamespace(allowed_operations={"upload_document"})
    with (
        patch(
            "app.services.clinical_evidence_integrity."
            "validate_live_document_processing_request",
            AsyncMock(return_value=capability),
        ),
        patch(
            "app.services.clinical_evidence_integrity.check_erasure_registry",
            AsyncMock(),
        ),
        pytest.raises(ClinicalEvidenceIntegrityError, match="SOURCE_RELATION_CYCLE"),
    ):
        await create_source_relationship(
            db,
            source_document_id=source,
            related_document_id=related,
            tenant_id=tenant,
            patient_id=patient,
            relation_type=SourceRelationType.ADDENDUM_TO,
            workflow_id="workflow-qualification",
            created_by="reviewer-qualification",
            authorization_provider_id="reviewer-qualification",
            authorization_hospital_id=tenant,
            consent_request_id="workflow-qualification",
            created_at=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_orchestrator_supersession_propagates_exact_immutable_link() -> None:
    tenant, patient, current_document, prior_document, job_id = (
        uuid4() for _ in range(5)
    )
    evidence_id, earlier_decision_id = uuid4(), uuid4()
    fact_key = clinical_fact_key("hba1c", "same-fact")
    assert fact_key is not None
    relation = SimpleNamespace(
        tenant_id=tenant,
        patient_id=patient,
        related_document_id=prior_document,
        relation_type="SUPERSEDES",
    )
    prior = SimpleNamespace(evidence_id=evidence_id)
    decision = SimpleNamespace(id=earlier_decision_id)
    db = _DB([relation, [(prior, decision)]])
    db.begin_nested = object()
    job = SimpleNamespace(
        id=job_id,
        tenant_id=tenant,
        patient_id=patient,
        document_id=current_document,
    )
    result = await pipeline_orchestrator._resolve_source_predecessors(
        db,
        job=job,
        candidates=[{"clinical_fact_key": fact_key}],
    )
    assert result == [
        (str(evidence_id), None, str(earlier_decision_id), frozenset(), prior)
    ]


@pytest.mark.asyncio
async def test_orchestrator_addendum_and_ambiguity_are_explicit() -> None:
    tenant, patient, current_document, prior_document = (uuid4() for _ in range(4))
    key = clinical_fact_key("hba1c", "same-fact")
    assert key is not None
    prior = SimpleNamespace(evidence_id=uuid4())
    relation = SimpleNamespace(
        tenant_id=tenant,
        patient_id=patient,
        related_document_id=prior_document,
        relation_type="ADDENDUM_TO",
    )
    job = SimpleNamespace(
        tenant_id=tenant, patient_id=patient, document_id=current_document
    )
    decision = SimpleNamespace(id=uuid4())
    db = _DB([relation, [(prior, decision)]])
    db.begin_nested = object()
    exact = await pipeline_orchestrator._resolve_source_predecessors(
        db, job=job, candidates=[{"clinical_fact_key": key}]
    )
    assert exact == [(None, str(prior.evidence_id), None, frozenset(), prior)]

    ambiguous_db = _DB(
        [
            relation,
            [
                (prior, decision),
                (SimpleNamespace(evidence_id=uuid4()), SimpleNamespace(id=uuid4())),
            ],
        ]
    )
    ambiguous_db.begin_nested = object()
    ambiguous = await pipeline_orchestrator._resolve_source_predecessors(
        ambiguous_db, job=job, candidates=[{"clinical_fact_key": key}]
    )
    assert ambiguous == [
        (
            None,
            None,
            None,
            frozenset({EvidenceIssue.SUPERSESSION_UNRESOLVED}),
            None,
        )
    ]


def test_same_field_without_fact_identity_is_ambiguity_not_conflict() -> None:
    candidates = [
        {
            "field_name": "blood_glucose",
            "raw_value": "101",
            "clinical_fact_key": None,
        },
        {
            "field_name": "blood_glucose",
            "raw_value": "145",
            "clinical_fact_key": None,
        },
    ]
    evidence = [
        SimpleNamespace(
            clinical_value=ClinicalValueEvidence(
                field_name="blood_glucose", raw_value=item["raw_value"]
            ),
            model_copy=lambda update, item=item: SimpleNamespace(**update),
        )
        for item in candidates
    ]
    conflicts = pipeline_orchestrator._mark_conflicting_evidence(candidates, evidence)
    assert conflicts == {}
    assert all(
        EvidenceIssue.CLINICAL_VALUE_AMBIGUOUS in item.clinical_value.issues
        for item in evidence
    )

"""Canonical adversarial specification for medical-document extraction.

Catalog membership records required security coverage. It does not imply that
an executable runtime regression exists or that a production control is done.
"""

from __future__ import annotations

from dataclasses import dataclass
from app.models.field_evidence import EvidenceGroup


@dataclass(frozen=True, slots=True)
class AdversarialScenario:
    scenario_id: int
    slug: str
    title: str
    evidence_groups: frozenset[EvidenceGroup]
    failure_condition: str
    required_behavior: str
    runtime_tested: bool = False
    test_reference: str | None = None


RUNTIME_AUTO_COMMIT_ENABLED = False
RUNTIME_AUTO_COMMIT_APPROVED = False


def _scenario(
    scenario_id: int,
    slug: str,
    title: str,
    groups: set[EvidenceGroup],
    failure_condition: str,
    required_behavior: str,
    *,
    runtime_tested: bool = False,
    test_reference: str | None = None,
) -> AdversarialScenario:
    return AdversarialScenario(
        scenario_id=scenario_id,
        slug=slug,
        title=title,
        evidence_groups=frozenset(groups),
        failure_condition=failure_condition,
        required_behavior=required_behavior,
        runtime_tested=runtime_tested,
        test_reference=test_reference,
    )


SCENARIOS: tuple[AdversarialScenario, ...] = (
    _scenario(
        1,
        "incorrect-ocr-convincing-document-confidence",
        "Incorrect OCR value with convincing document-level confidence",
        {EvidenceGroup.MODEL_EVIDENCE},
        "OCR produces an incorrect field value while the provider reports convincing confidence for the document as a whole.",
        "Reject the value from clinical truth and require genuine field-level evidence and human adjudication; document confidence must never be copied into field confidence.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_field_evidence_contract.py::test_scenario_1_document_confidence_never_becomes_field_confidence",
    ),
    _scenario(
        2,
        "field-extracted-from-wrong-page",
        "Field extracted from the wrong page",
        {EvidenceGroup.VISUAL_EVIDENCE},
        "The extracted field cites a page that does not contain its claimed source.",
        "Treat the field as unverifiable, prevent clinical commitment, and preserve the page mismatch for review.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_field_evidence_contract.py::test_scenario_2_missing_page_is_not_complete_visual_evidence",
    ),
    _scenario(
        3,
        "value-bounding-box-text-mismatch",
        "Value and bounding box point to different text",
        {EvidenceGroup.VISUAL_EVIDENCE, EvidenceGroup.MODEL_EVIDENCE},
        "The normalized value disagrees with the text anchored by its bounding box.",
        "Fail closed for clinical commitment; conflicting visual and model evidence cannot qualify for automatic commitment.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_field_evidence_contract.py::test_scenario_3_value_and_visual_source_mismatch_is_explicit",
    ),
    _scenario(
        4,
        "missing-or-malformed-bounding-box",
        "Missing or malformed bounding box",
        {EvidenceGroup.VISUAL_EVIDENCE},
        "A field lacks a usable bounding box or supplies coordinates outside the source page.",
        "Mark the visual evidence unverifiable and require review; missing or malformed evidence cannot qualify for automatic commitment.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_field_evidence_contract.py::test_scenario_4_missing_or_malformed_bbox_is_unavailable_or_invalid",
    ),
    _scenario(
        5,
        "document-confidence-without-field-confidence",
        "Provider returns only document-level confidence with no field-level confidence",
        {EvidenceGroup.MODEL_EVIDENCE},
        "The provider supplies aggregate confidence but omits confidence for each extracted field.",
        "Do not infer or copy field confidence, do not commit the fields to clinical truth, and require explicit field-level evidence.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_field_evidence_contract.py::test_scenario_5_document_only_provider_has_unavailable_field_confidence",
    ),
    _scenario(
        6,
        "provider-timeout-mid-document",
        "Provider timeout halfway through a multi-page document",
        {EvidenceGroup.LIFECYCLE, EvidenceGroup.MODEL_EVIDENCE},
        "Extraction times out after only a subset of pages has been processed.",
        "Keep partial output out of clinical truth, record a safe incomplete failure state, and permit only bounded idempotent recovery.",
    ),
    _scenario(
        7,
        "partial-provider-response-followed-by-retry",
        "Partial provider response followed by retry",
        {EvidenceGroup.LIFECYCLE, EvidenceGroup.MODEL_EVIDENCE},
        "A partial response is followed by a retry that may repeat or disagree with earlier fields.",
        "Discard partial results as clinical candidates and reconcile the complete retry idempotently without duplicate or mixed-attempt records.",
    ),
    _scenario(
        8,
        "patient-identity-mismatch-source-only-document",
        "Patient identity mismatch inside an otherwise source-only document",
        {EvidenceGroup.IDENTITY},
        "Document identity evidence conflicts with the patient bound to the job.",
        "Quarantine or reject the job and block all patient-record commitment; identity ambiguity must fail closed.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_identity_discrepancy_quarantine.py::test_scenario_8_identity_discrepancy_is_encrypted_quarantined_and_idempotent",
    ),
    _scenario(
        9,
        "conflicting-values-across-pages",
        "Conflicting values across pages",
        {EvidenceGroup.CLINICAL_VALUE, EvidenceGroup.LIFECYCLE},
        "Different pages assert incompatible values for the same clinical fact.",
        "Preserve both sources as conflicting candidates, prevent silent selection, and require explicit human resolution before clinical commitment.",
        runtime_tested=True,
        test_reference="tests/integration/test_clinical_conflict_supersession_postgres.py::test_scenario_9_production_orchestrator_persists_exact_conflict",
    ),
    _scenario(
        10,
        "duplicate-document-or-repeated-extraction",
        "Duplicate document or repeated extraction",
        {EvidenceGroup.LIFECYCLE, EvidenceGroup.IDENTITY},
        "The same patient document is uploaded or extracted more than once.",
        "Use patient-and-tenant-bound idempotency to prevent duplicate clinical records while retaining safe provenance for the repeated attempt.",
    ),
    _scenario(
        11,
        "unsupported-corrupt-or-password-protected-document",
        "Unsupported, corrupted, or password-protected document",
        {EvidenceGroup.POLICY_EVIDENCE, EvidenceGroup.LIFECYCLE},
        "The source cannot be safely decoded under the approved document policy.",
        "Reject processing with a stable non-sensitive failure state; never fabricate, partially parse, or commit clinical output.",
    ),
    _scenario(
        12,
        "tampered-provider-output",
        "Tampered provider output",
        {EvidenceGroup.MODEL_EVIDENCE, EvidenceGroup.VISUAL_EVIDENCE},
        "Provider output or its evidence bindings are altered after extraction.",
        "Reject unverifiable output, preserve safe tamper evidence, and prevent every affected field from entering clinical truth.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_field_evidence_contract.py::test_scenario_12_tampered_evidence_is_representable_and_blocking",
    ),
    _scenario(
        13,
        "unit-date-decimal-or-reference-range-ambiguity",
        "Unit, date, decimal, or reference-range ambiguity",
        {EvidenceGroup.CLINICAL_VALUE},
        "A clinical value has an ambiguous unit, date, decimal separator, or reference range.",
        "Do not normalize by guesswork; retain the source representation and require explicit adjudication before commitment.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_field_evidence_contract.py::test_scenario_13_ambiguous_clinical_normalization_remains_unresolved",
    ),
    _scenario(
        14,
        "provider-or-model-version-change",
        "Provider or model-version change",
        {EvidenceGroup.MODEL_EVIDENCE, EvidenceGroup.LIFECYCLE},
        "One job or retry spans a provider or model-version transition.",
        "Pin and preserve the version used for each attempt, prevent mixed-version evidence in one result, and require a new traceable evaluation when versions change.",
    ),
    _scenario(
        15,
        "quarantine-expiry-repeated-failure",
        "Quarantine expiry and repeated processing failure",
        {EvidenceGroup.LIFECYCLE, EvidenceGroup.POLICY_EVIDENCE},
        "A quarantined job reaches expiry after repeated processing failures.",
        "Escalate for approved manual disposition and retain the failure history; expiry must never cause automatic clinical commitment.",
    ),
    _scenario(
        16,
        "consent-expires-or-revoked-in-flight",
        "Consent expires or is revoked while a job is in flight",
        {EvidenceGroup.POLICY_EVIDENCE, EvidenceGroup.LIFECYCLE},
        "The authorization that permitted protected processing ceases to be valid before the job completes.",
        "Stop further protected processing, deny commit and source access, and preserve only the minimum audit evidence permitted by policy.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_extraction_decision_engine.py::test_inactive_consent_and_erasure_in_progress_quarantine",
    ),
    _scenario(
        17,
        "audit-outbox-failure-during-clinical-commit",
        "Audit-outbox write failure during a clinical commit",
        {EvidenceGroup.LIFECYCLE, EvidenceGroup.POLICY_EVIDENCE},
        "A required durable audit-outbox insertion fails after clinical mutations have been staged.",
        "Fail the request closed and roll back clinical fields, timeline entries, job state, commit markers, and partial audit state as one transaction.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_lifecycle.py::test_scenario_17_outbox_failure_rolls_back_clinical_commit_and_retry_is_safe",
    ),
    _scenario(
        18,
        "erasure-requested-in-flight",
        "Erasure or deletion requested while a job is in flight",
        {EvidenceGroup.LIFECYCLE, EvidenceGroup.POLICY_EVIDENCE},
        "An erasure or deletion state becomes authoritative while extraction is processing.",
        "Give erasure state precedence, stop further processing and commitment, and retain only legally or policy-required minimal audit evidence.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_extraction_decision_engine.py::test_inactive_consent_and_erasure_in_progress_quarantine",
    ),
    _scenario(
        19,
        "cross-patient-concurrent-upload-race",
        "Concurrent uploads for different patients race into the same encounter or tenant context",
        {EvidenceGroup.IDENTITY, EvidenceGroup.LIFECYCLE},
        "Concurrent jobs risk sharing encounter, patient, or tenant bindings.",
        "Keep every mutation and object bound to its authenticated patient and tenant; any ambiguous or crossed binding must fail closed.",
    ),
    _scenario(
        20,
        "cross-tenant-object-key-collision",
        "Cross-tenant object-key collision or leak attempt",
        {EvidenceGroup.IDENTITY, EvidenceGroup.POLICY_EVIDENCE},
        "An object reference collides with or attempts to resolve into another tenant.",
        "Deny access and processing before source retrieval, preserve tenant isolation, and emit only safe audit evidence.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_field_evidence_contract.py::test_scenario_20_cross_tenant_binding_mismatch_is_explicit",
    ),
    _scenario(
        21,
        "bounding-box-covers-partial-clinical-value",
        "Bounding box covers only part of the extracted clinical value",
        {EvidenceGroup.VISUAL_EVIDENCE, EvidenceGroup.CLINICAL_VALUE},
        'The evidence box anchors only part of a value, such as "150" while the extracted value is "150 mg".',
        "Treat the field as incompletely evidenced, preserve the exact source span, and require correction or review before clinical commitment.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_field_evidence_contract.py::test_scenario_21_partial_visual_coverage_is_not_complete",
    ),
    _scenario(
        22,
        "independent-verifier-abstains",
        "Independent verifier abstains instead of agreeing or disagreeing",
        {EvidenceGroup.MODEL_EVIDENCE, EvidenceGroup.POLICY_EVIDENCE},
        "The verifier cannot reach a supported agreement or disagreement result.",
        "Treat abstention as unavailable evidence, never as agreement, and require the minimum safe review outcome without inventing a final routing policy.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_field_evidence_contract.py::test_scenario_22_verifier_abstention_is_not_agreement",
    ),
    _scenario(
        23,
        "corrected-addendum-supersedes-earlier-value",
        "A later page or document supersedes an earlier value through a corrected addendum",
        {EvidenceGroup.CLINICAL_VALUE, EvidenceGroup.LIFECYCLE},
        "A corrected addendum changes a clinical value previously extracted from an earlier source.",
        "Preserve both immutable versions and explicit supersession provenance; never silently overwrite the earlier value.",
        runtime_tested=True,
        test_reference="tests/integration/test_clinical_conflict_supersession_postgres.py::test_scenario_23_production_upload_revalidates_related_source",
    ),
    _scenario(
        24,
        "policy-version-skew-during-evaluation",
        "Policy-version skew during evaluation or migration",
        {EvidenceGroup.POLICY_EVIDENCE, EvidenceGroup.LIFECYCLE},
        "Evaluation observes different policy versions during one decision or migration.",
        "Pin one immutable policy version for the entire decision; re-evaluation under a newer policy must create a new immutable decision and never mutate or mix the original.",
        runtime_tested=True,
        test_reference="tests/ai_extraction/adversarial/test_field_evidence_contract.py::test_scenario_24_policy_record_pins_one_immutable_version",
    ),
)


SCENARIOS_BY_ID = {scenario.scenario_id: scenario for scenario in SCENARIOS}

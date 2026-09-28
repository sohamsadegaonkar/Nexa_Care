from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "nexa-client" / "packages" / "app"


def read(relative: str) -> str:
    return (APP / relative).read_text(encoding="utf-8")


def test_emergency_uses_discovered_break_glass_without_uuid_entry() -> None:
    screen = read("features/doctor/EmergencyAccessScreen.tsx")
    client = read("utils/apiClient.ts")
    assert "breakGlassDiscoveredIssue" in screen
    assert "NexaApiClient.breakGlassIssue(" not in screen
    assert "Canonical patient UUID" not in screen
    assert "patient_id:" not in screen
    assert "/api/v2/consent/break-glass/discovered/issue" in client
    assert "discoverPatientExact" in screen
    assert "resolveNfcCard" in screen


def test_patient_search_uses_clinical_language_and_distinct_failures() -> None:
    screen = read("features/doctor/PatientSearchScreen.tsx")
    for forbidden in (
        "Opaque Resolution",
        "Minimum-Disclosure Resolution",
        "Exact Verified Phone Lookup",
        "Recent MFA Required",
        "24 hexadecimal characters",
    ):
        assert forbidden not in screen
    for code in (
        "CLINICAL_ELIGIBILITY_DENIED",
        "DISCOVERY_RECENT_MFA_REQUIRED",
        "DISCOVERY_NO_MATCH",
        "DISCOVERY_RATE_LIMITED",
        "DISCOVERY_UNAVAILABLE",
    ):
        assert code in screen


def test_documents_navigation_opens_workspace_before_patient_selection() -> None:
    shell = read("features/doctor/ProviderShell.tsx")
    dashboard = read("features/doctor/DoctorDashboardScreen.tsx")
    workspace = read("features/doctor/DocumentsWorkspaceScreen.tsx")
    assert "path: '/doctor/documents'" in shell
    assert "route: '/doctor/documents'" in dashboard
    assert "/doctor/patient-search?intent=document_upload" not in shell
    for label in ("All", "Needs Review", "Processing", "Completed"):
        assert f"label: '{label}'" in workspace
    assert "NexaApiClient.listAdjudicationCases()" in workspace
    assert "Review Document" in workspace


def test_review_ui_removes_internal_clinician_jargon() -> None:
    shell = read("features/doctor/ProviderShell.tsx")
    queue = read("features/adjudication/AdjudicationQueueScreen.tsx")
    review = read("features/adjudication/AdjudicationReviewScreen.tsx")
    result = read("features/adjudication/AdjudicationResultScreen.tsx")
    assert "label: 'Adjudication'" not in shell
    for forbidden in (
        "Source adjudication",
        "SOURCE_ONLY",
        "routing reference",
        "zero-candidate job",
        "field-linked case",
        "document-level case",
    ):
        assert forbidden.lower() not in queue.lower()
    for expected in (
        "Review Imported Records",
        "Needs Clinical Verification",
        "Review Document",
    ):
        assert expected in queue
    assert "Verify or Correct" in review
    assert "Add to Patient Record" in result

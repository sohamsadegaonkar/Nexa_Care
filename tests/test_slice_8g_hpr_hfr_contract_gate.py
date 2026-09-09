import json
from pathlib import Path

from app.services.provider_verification_worker import ProviderVerificationWorkerService


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "docs" / "governance" / "ABDM_HPR_HFR_MACHINE_CONTRACT_GATE.json"


def _gate() -> dict:
    return json.loads(GATE.read_text(encoding="utf-8"))


def test_hpr_hfr_contract_gate_is_explicitly_blocked_and_disabled() -> None:
    gate = _gate()
    assert gate["schema"] == "nexa-abdm-hpr-hfr-machine-contract-gate-v1"
    assert gate["status"] == "BLOCKED_EXTERNAL_CONTRACT"
    assert gate["external_adapter_enabled"] is False
    assert gate["published_fhir_interoperability_is_not_registry_transport_contract"] is True


def test_hpr_hfr_gate_requires_complete_machine_contract_before_ready() -> None:
    required = set(_gate()["required_before_ready"])
    assert required == {
        "authoritative_source_url",
        "contract_version_or_publication_identity",
        "server_to_server_authentication_lifecycle",
        "professional_registry_endpoint_and_method",
        "facility_registry_endpoint_and_method",
        "request_schemas",
        "response_schemas",
        "identity_binding_semantics",
        "status_and_error_dispositions",
        "retry_semantics",
        "rate_limit_semantics",
        "sandbox_or_qualification_target",
        "official_change_or_versioning_policy",
    }


def test_provider_verification_worker_defaults_external_automation_off() -> None:
    worker = ProviderVerificationWorkerService(object())
    assert worker.adapters == {}
    assert worker.is_automation_enabled() is False


def test_no_hpr_hfr_transport_adapter_is_committed_while_gate_is_blocked() -> None:
    services = ROOT / "app" / "services"
    transport_candidates = sorted(
        path.name
        for path in services.glob("*.py")
        if "hpr" in path.name.lower() or "hfr" in path.name.lower()
    )
    assert transport_candidates == []

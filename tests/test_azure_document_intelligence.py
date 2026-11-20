"""Synthetic SDK doubles; no Azure network."""

from io import BytesIO
from types import SimpleNamespace as NS

import pytest
from pypdf import PdfWriter

from app.ai import azure_document_intelligence as adapter
from app.ai.extractor import InvalidDocumentError, ProviderResponseError, get_medical_document_extractor
from app.core.config import ConfigError, get_document_extraction_config


def _pdf(pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _result():
    text = "Patient name\nSynthetic Patient\nHbA1c\n7.1 %\n"
    def element(value):
        return NS(content=value, spans=[NS(offset=text.index(value), length=len(value))],
                  bounding_regions=[NS(page_number=1, polygon=[1, 1, 10, 1, 10, 5, 1, 5])])
    return NS(api_version="2024-11-30", model_id="prebuilt-layout",
              pages=[NS(page_number=1, width=100.0, height=100.0)], content=text,
              key_value_pairs=[
                  NS(key=element("Patient name"), value=element("Synthetic Patient"), confidence=0.9),
                  NS(key=element("HbA1c"), value=element("7.1 %"), confidence=0.99),
              ])


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setenv("DOCUMENT_EXTRACTION_PROVIDER", "azure_document_intelligence")
    monkeypatch.setenv("AZURE_DOCUMENT_AI_SYNTHETIC_ONLY", "YES")
    monkeypatch.setenv("AZURE_DOCUMENT_AI_ENDPOINT", "https://nexa-demo-test.cognitiveservices.azure.com")
    monkeypatch.setenv("AZURE_DOCUMENT_AI_MANAGED_IDENTITY_CLIENT_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setenv("DOCUMENT_AI_ASYNC_MULTIPAGE_ENABLED", "false")
    monkeypatch.setenv("DOCUMENT_AI_TIMEOUT_SECONDS", "30")
    return get_document_extraction_config()


@pytest.mark.asyncio
async def test_factory_real_adapter_with_sdk_double(configured, monkeypatch):
    instance = get_medical_document_extractor(configured)
    def analyze(data):
        assert data.startswith(b"%PDF")
        return _result()
    monkeypatch.setattr(instance, "_analyze", analyze)
    result = await instance.extract_bytes(_pdf(), mime_type="application/pdf", request_id="synthetic")
    assert result.provider_adapter == "azure_document_intelligence"
    assert result.document.patient_name == "Synthetic Patient"
    assert result.document.extraction_confidence is None
    identity, lab = result.document.field_evidence
    assert identity.evidence_hash is None
    assert lab.canonical_field_name == "hba1c"
    assert lab.raw_value == lab.source_text == "7.1 %"
    assert lab.page_number == 0
    assert lab.bounding_box is not None
    assert lab.field_confidence is None and lab.incomplete
    assert not result.document.lab_results
    with pytest.raises(InvalidDocumentError):
        await instance.extract_bytes(_pdf(2), mime_type="application/pdf", request_id="synthetic")


def test_conflicting_span_and_wrong_model_refused():
    response = _result()
    response.key_value_pairs[1].value.content = "invented"
    with pytest.raises(ProviderResponseError, match="match source span"):
        adapter._parse(response)
    response = _result()
    response.model_id = "prebuilt-invoice"
    with pytest.raises(ProviderResponseError, match="schema"):
        adapter._parse(response)


def test_production_opt_in_and_wrong_endpoint_denied(configured, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(ConfigError, match="not qualified"):
        get_document_extraction_config()
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AZURE_DOCUMENT_AI_ENDPOINT", "https://campuspool.cognitiveservices.azure.com")
    with pytest.raises(ConfigError, match="dedicated"):
        get_document_extraction_config()
    monkeypatch.setenv("AZURE_DOCUMENT_AI_ENDPOINT", "https://nexa-demo-test.cognitiveservices.azure.com")
    monkeypatch.delenv("AZURE_DOCUMENT_AI_SYNTHETIC_ONLY")
    with pytest.raises(ConfigError, match="synthetic-only"):
        get_document_extraction_config()

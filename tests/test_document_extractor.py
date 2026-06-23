"""Tests for the remote medical document extractor wrapper."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.ai.extractor import DocumentExtractionError, MedicalDocumentExtractor
from app.models.ai_models import ExtractedMedicalDocument


def run(coro):
    return asyncio.run(coro)


class TestExtractedMedicalDocument(unittest.TestCase):
    def test_strict_types_reject_bad_model_output(self) -> None:
        with self.assertRaises(ValidationError):
            ExtractedMedicalDocument(
                patient_name="Jane Example",
                aadhaar_abha_id="1234-5678-9012",
                phone="9876543210",
                diagnoses="asthma",
                lab_results=[],
                prescriptions=[],
                extraction_confidence=0.96,
            )

    def test_confidence_must_be_between_zero_and_one(self) -> None:
        with self.assertRaises(ValidationError):
            ExtractedMedicalDocument(
                patient_name="Jane Example",
                aadhaar_abha_id="1234-5678-9012",
                phone="9876543210",
                diagnoses=["asthma"],
                lab_results=[],
                prescriptions=[],
                extraction_confidence=1.5,
            )

    def test_extra_fields_are_preserved_for_fail_safe_sharding(self) -> None:
        doc = ExtractedMedicalDocument(
            patient_name="Jane Example",
            aadhaar_abha_id="1234-5678-9012",
            phone="9876543210",
            diagnoses=["asthma"],
            lab_results=[],
            prescriptions=[],
            extraction_confidence=0.96,
            unexpected_identifier="vault me",
        )

        dumped = doc.model_dump()
        self.assertEqual(dumped["unexpected_identifier"], "vault me")


class TestMedicalDocumentExtractor(unittest.TestCase):
    def test_initialization_does_not_load_local_ml(self) -> None:
        extractor = MedicalDocumentExtractor(api_key=None, api_url=None)

        self.assertIsNone(extractor.api_key)
        self.assertIsNone(extractor.api_url)

    @patch("app.ai.extractor.asyncio.sleep", new_callable=AsyncMock)
    def test_missing_api_key_returns_mock_after_delay(self, mock_sleep) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"fake medical document")
            path = tmp.name

        extractor = MedicalDocumentExtractor(api_key=None, api_url=None)
        try:
            document = run(extractor.extract_data(path))
        finally:
            Path(path).unlink(missing_ok=True)

        mock_sleep.assert_awaited_once_with(2)
        self.assertIsInstance(document, ExtractedMedicalDocument)
        self.assertGreaterEqual(document.extraction_confidence, 0.95)
        self.assertEqual(document.patient_name, "Asha Raman")

    def test_api_key_path_validates_remote_payload(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"fake medical document")
            path = tmp.name

        extractor = MedicalDocumentExtractor(api_key="test-key", api_url="https://vlm.example")
        extractor._call_remote_vlm_api = AsyncMock(return_value={
            "patient_name": "Jane Example",
            "aadhaar_abha_id": "1234-5678-9012",
            "phone": "9876543210",
            "diagnoses": ["asthma"],
            "lab_results": ["CBC normal"],
            "prescriptions": ["Salbutamol"],
            "extraction_confidence": 0.91,
        })

        try:
            document = run(extractor.extract_data(path))
        finally:
            Path(path).unlink(missing_ok=True)

        self.assertEqual(document.extraction_confidence, 0.91)
        extractor._call_remote_vlm_api.assert_awaited_once()

    def test_file_read_error_raises_extraction_error(self) -> None:
        extractor = MedicalDocumentExtractor(api_key=None, api_url=None)

        with self.assertRaises(DocumentExtractionError):
            run(extractor.extract_data("/tmp/does-not-exist-nexa-care"))

    def test_no_torch_or_transformer_modules_are_imported_by_extractor(self) -> None:
        import app.ai.extractor as extractor_module

        module_globals = set(extractor_module.__dict__)
        self.assertNotIn("torch", module_globals)
        self.assertNotIn("pipeline", module_globals)


if __name__ == "__main__":
    unittest.main()

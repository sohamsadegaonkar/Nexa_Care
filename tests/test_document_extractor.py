"""Tests for the medical document extractor wrapper."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from pydantic import ValidationError

from app.ai.extractor import MedicalDocumentExtractor
from app.models.ai_models import ExtractedMedicalDocument


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
            )

    def test_extra_fields_are_preserved_for_fail_safe_sharding(self) -> None:
        doc = ExtractedMedicalDocument(
            patient_name="Jane Example",
            aadhaar_abha_id="1234-5678-9012",
            phone="9876543210",
            diagnoses=["asthma"],
            lab_results=[],
            prescriptions=[],
            unexpected_identifier="vault me",
        )

        dumped = doc.model_dump()
        self.assertEqual(dumped["unexpected_identifier"], "vault me")


class TestMedicalDocumentExtractor(unittest.TestCase):
    @patch("app.ai.extractor.pipeline")
    @patch("app.ai.extractor.torch.cuda.is_available")
    def test_initialization_uses_cpu_when_cuda_unavailable(self, mock_cuda, mock_pipeline) -> None:
        mock_cuda.return_value = False

        extractor = MedicalDocumentExtractor(model_name="test/model")

        self.assertEqual(extractor.device, -1)
        self.assertEqual(extractor.device_label, "cpu")
        mock_pipeline.assert_called_once_with(
            task="image-to-text",
            model="test/model",
            device=-1,
        )

    @patch("app.ai.extractor.pipeline")
    @patch("app.ai.extractor.torch.cuda.is_available")
    def test_initialization_uses_gpu_when_cuda_available(self, mock_cuda, mock_pipeline) -> None:
        mock_cuda.return_value = True

        extractor = MedicalDocumentExtractor(model_name="test/model")

        self.assertEqual(extractor.device, 0)
        self.assertEqual(extractor.device_label, "cuda")
        mock_pipeline.assert_called_once_with(
            task="image-to-text",
            model="test/model",
            device=0,
        )

    def test_heuristic_parser_maps_standard_medical_fields(self) -> None:
        parsed = MedicalDocumentExtractor._parse_medical_text(
            "Patient Name: Jane Example ABHA ID: 12-3456-7890-1234 "
            "Phone: +91 9876543210 Diagnosis: Asthma; Hypertension "
            "Lab Results: HbA1c 7.2%, CBC normal Prescription: Metformin 500mg; Salbutamol"
        )

        self.assertEqual(parsed["patient_name"], "Jane Example")
        self.assertEqual(parsed["aadhaar_abha_id"], "12-3456-7890-1234")
        self.assertEqual(parsed["phone"], "+919876543210")
        self.assertIn("Asthma", parsed["diagnoses"])
        self.assertIn("Metformin 500mg", parsed["prescriptions"])

    def test_extract_data_opens_image_and_returns_typed_document(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            image_path = tmp.name
        Image.new("RGB", (16, 16), color="white").save(image_path)

        extractor = MedicalDocumentExtractor.__new__(MedicalDocumentExtractor)
        extractor.device_label = "cpu"
        extractor._pipeline = Mock(return_value=[{
            "generated_text": (
                "Patient Name: Jane Example ABHA ID: 12-3456-7890-1234 "
                "Phone: 9876543210 Diagnosis: Asthma Lab Results: CBC normal "
                "Prescription: Metformin"
            )
        }])

        try:
            document = extractor.extract_data(image_path)
        finally:
            Path(image_path).unlink(missing_ok=True)

        self.assertIsInstance(document, ExtractedMedicalDocument)
        self.assertEqual(document.phone, "9876543210")
        self.assertIn("Asthma", document.diagnoses[0])
        extractor._pipeline.assert_called_once()

    @patch("app.ai.extractor.convert_from_path")
    def test_pdf_uses_first_page_conversion(self, mock_convert) -> None:
        mock_page = Mock()
        mock_converted = Mock()
        mock_page.convert.return_value = mock_converted
        mock_convert.return_value = [mock_page]
        extractor = MedicalDocumentExtractor.__new__(MedicalDocumentExtractor)

        result = extractor._load_first_page_image("/tmp/document.pdf")

        self.assertEqual(result, mock_converted)
        mock_convert.assert_called_once_with("/tmp/document.pdf", first_page=1, last_page=1)

    def test_oom_detection_handles_runtime_error_message(self) -> None:
        self.assertTrue(MedicalDocumentExtractor._is_oom_error(RuntimeError("CUDA out of memory")))
        self.assertFalse(MedicalDocumentExtractor._is_oom_error(RuntimeError("other failure")))


if __name__ == "__main__":
    unittest.main()

"""Splits OCR/extraction model output into the PII vault shard and the
"de-identified" clinical shard.

This is the one place that decision gets made, which is what makes it
worth testing in isolation (see tests/test_sharding.py) -- it used to be
inlined directly in the FastAPI handler in app/main.py, where it could
only be exercised by running the whole app.

The field-name sets below depend entirely on whatever extraction model is
configured in document_processor.py actually emitting these exact key
names. Donut's CORD-v2 checkpoint, in particular, was fine-tuned on
receipts, not medical documents, and there is no guarantee it produces
keys like "patient_name" or "aadhaar_abha_id" at all. Before trusting this
in production:
  1. Run scripts/validate_extraction_schema.py against a real, labeled set
     of medical documents (prescriptions, lab reports, ID pages, etc.).
  2. For every key it reports as "unrecognized", get a clinician or
     compliance reviewer to confirm whether it's PII before adding it to
     either set below -- don't guess.
  3. If the model doesn't produce the fields this logic depends on at all,
     the fix is fine-tuning/swapping the model, not loosening this file.
"""
from __future__ import annotations

from app.observability.redactor import SENSITIVE_FIELDS as PII_FIELD_NAMES

# Fields known to be safe to file under the "anonymized" clinical shard.
CLINICAL_FIELD_NAMES = {"diagnoses", "lab_results", "prescriptions"}


def split_pii_and_clinical_fields(
    extracted: dict,
) -> tuple[dict, dict, dict]:
    """Splits `extracted` (raw OCR output) into three dicts:
    (vault_payload, clinical_payload, unrecognized_payload).

    Unrecognized keys are deliberately NOT guessed into either shard here.
    A key this function doesn't recognize might be undocumented PII --
    that's exactly how aadhaar_abha_id almost ended up unprotected, just
    under a different name than the old check expected. The caller decides
    how to handle the unrecognized bucket; app/main.py currently fails
    safe by routing it into the vault and logging a warning.
    """
    vault_payload: dict = {}
    clinical_payload: dict = {}
    unrecognized_payload: dict = {}

    for key, value in extracted.items():
        normalized_key = key.lower() if isinstance(key, str) else key

        if normalized_key in PII_FIELD_NAMES:
            vault_payload[key] = value
        elif normalized_key in CLINICAL_FIELD_NAMES:
            clinical_payload[key] = value
        else:
            unrecognized_payload[key] = value

    return vault_payload, clinical_payload, unrecognized_payload
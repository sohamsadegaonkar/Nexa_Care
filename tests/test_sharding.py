from app.services.sharding import split_pii_and_clinical_fields


def test_aadhaar_abha_id_routes_to_vault_not_clinical():
    """
    Regression test for the bug where OCR output keyed as
    'aadhaar_abha_id' (matching models/schemas.py, which is what the rest
    of the system actually calls this field) fell through the old
    pii_keys = {'aadhaar', ...} check in main.py and was filed as
    "anonymized" clinical data instead of PII.
    """
    extracted = {
        "patient_name": "Jane Doe",
        "phone": "9999999999",
        "aadhaar_abha_id": "1234-5678-9012",
        "email": "jane@example.com",
        "diagnoses": ["hypertension"],
        "lab_results": ["bp_140_90"],
    }

    vault, clinical, unrecognized = split_pii_and_clinical_fields(extracted)

    assert vault == {
        "patient_name": "Jane Doe",
        "phone": "9999999999",
        "aadhaar_abha_id": "1234-5678-9012",
        "email": "jane@example.com",
    }
    assert clinical == {
        "diagnoses": ["hypertension"],
        "lab_results": ["bp_140_90"],
    }
    assert unrecognized == {}


def test_unrecognized_keys_are_quarantined_not_guessed():
    """
    A key this function doesn't recognize might be undocumented PII --
    that's exactly how aadhaar_abha_id almost leaked, just under a name
    the old check didn't expect. It must come back as 'unrecognized', not
    get silently merged into either shard here.
    """
    extracted = {"some_field_the_model_invented": "value"}

    vault, clinical, unrecognized = split_pii_and_clinical_fields(extracted)

    assert vault == {}
    assert clinical == {}
    assert unrecognized == {"some_field_the_model_invented": "value"}


def test_key_matching_is_case_insensitive():
    extracted = {"PATIENT_NAME": "Jane Doe", "Diagnoses": ["flu"]}

    vault, clinical, unrecognized = split_pii_and_clinical_fields(extracted)

    assert vault == {"PATIENT_NAME": "Jane Doe"}
    assert clinical == {"Diagnoses": ["flu"]}
    assert unrecognized == {}


def test_empty_input_returns_three_empty_dicts():
    assert split_pii_and_clinical_fields({}) == ({}, {}, {})

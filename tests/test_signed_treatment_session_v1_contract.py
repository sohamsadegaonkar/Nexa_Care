"""Adversarial contract tests for patient-signed treatment-session V1 bytes.

These tests deliberately lock the cryptographic contract before any clinical
write route consumes it. Signed Consent V3 must remain read-only; the new
protocol must bind an exact, closed treatment-operation set and the exact
initiating provider session so authority cannot be widened or rebound after
patient signature.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    ClinicalAccessOperation,
)
from app.services.signed_consent_v3 import canonical_signed_consent_v3_payload
from app.services.signed_treatment_session_v1 import (
    SIGNED_TREATMENT_SESSION_V1_DOMAIN,
    SIGNED_TREATMENT_SESSION_V1_OPERATION,
    SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
    TreatmentSessionV1ProtocolError,
    canonical_signed_treatment_v1_payload,
    canonical_treatment_context_v1,
    normalize_treatment_operations,
    treatment_context_hash_v1,
)


CONTEXT = {
    "request_id": "11111111-1111-4111-8111-111111111111",
    "patient_id": "22222222-2222-4222-8222-222222222222",
    "provider_id": "33333333-3333-4333-8333-333333333333",
    "hospital_id": "44444444-4444-4444-8444-444444444444",
    "provider_session_binding_hash": "b" * 64,
    "challenge_nonce": "treatment-session-nonce-original",
    "purpose": "treatment",
    "allowed_operations": (
        ClinicalAccessOperation.READ_CLINICAL_HISTORY.value,
        ClinicalAccessOperation.CREATE_ENCOUNTER.value,
        ClinicalAccessOperation.WRITE_VITALS.value,
    ),
    "access_duration": 900,
    "issued_at": "2026-09-17T00:00:00+00:00",
    "expires_at": "2026-09-17T00:02:00+00:00",
}


def _signed_fields() -> dict:
    context_hash = treatment_context_hash_v1(**CONTEXT)
    return {
        **CONTEXT,
        "decision": "approved",
        "treatment_context_hash": context_hash,
        "device_id": "55555555-5555-4555-8555-555555555555",
        "key_id": "66666666-6666-4666-8666-666666666666",
        "key_version": 3,
        "public_key_fingerprint": "a" * 64,
    }


def test_protocol_has_distinct_domain_operation_and_policy_version() -> None:
    payload = json.loads(canonical_signed_treatment_v1_payload(**_signed_fields()))

    assert SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION == "nexa-treatment-session-v1"
    assert SIGNED_TREATMENT_SESSION_V1_DOMAIN == "NEXA_CARE_SIGNED_TREATMENT_SESSION"
    assert SIGNED_TREATMENT_SESSION_V1_OPERATION == "TREATMENT_SESSION_DECISION"
    assert payload["protocol_version"] == SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION
    assert payload["domain"] == SIGNED_TREATMENT_SESSION_V1_DOMAIN
    assert payload["operation"] == SIGNED_TREATMENT_SESSION_V1_OPERATION
    assert payload["policy_version"] == CLINICAL_ACCESS_POLICY_VERSION


def test_treatment_protocol_is_cryptographically_domain_separated_from_v3() -> None:
    v3 = canonical_signed_consent_v3_payload(
        request_id=CONTEXT["request_id"],
        patient_id=CONTEXT["patient_id"],
        provider_id=CONTEXT["provider_id"],
        hospital_id=CONTEXT["hospital_id"],
        challenge_nonce=CONTEXT["challenge_nonce"],
        decision="approved",
        purpose=CONTEXT["purpose"],
        scope="clinical",
        access_duration=CONTEXT["access_duration"],
        issued_at=CONTEXT["issued_at"],
        expires_at=CONTEXT["expires_at"],
        consent_context_hash="c" * 64,
        device_id="55555555-5555-4555-8555-555555555555",
        key_id="66666666-6666-4666-8666-666666666666",
        key_version=3,
        public_key_fingerprint="a" * 64,
    )
    treatment = canonical_signed_treatment_v1_payload(**_signed_fields())

    assert v3 != treatment
    assert hashlib.sha256(v3).digest() != hashlib.sha256(treatment).digest()
    assert b"nexa-consent-v3" in v3
    assert b"nexa-treatment-session-v1" in treatment


def test_operation_set_is_sorted_for_deterministic_set_semantics() -> None:
    forward = canonical_treatment_context_v1(**CONTEXT)
    reversed_context = {
        **CONTEXT,
        "allowed_operations": tuple(reversed(CONTEXT["allowed_operations"])),
    }

    assert canonical_treatment_context_v1(**reversed_context) == forward
    decoded = json.loads(forward)
    assert decoded["allowed_operations"] == sorted(CONTEXT["allowed_operations"])


def test_operation_widening_changes_context_hash_and_signed_bytes() -> None:
    original_hash = treatment_context_hash_v1(**CONTEXT)
    widened = {
        **CONTEXT,
        "allowed_operations": (
            *CONTEXT["allowed_operations"],
            ClinicalAccessOperation.WRITE_PRESCRIPTION.value,
        ),
    }
    widened_hash = treatment_context_hash_v1(**widened)

    assert widened_hash != original_hash

    signed = _signed_fields()
    widened_signed = {
        **signed,
        "allowed_operations": widened["allowed_operations"],
        "treatment_context_hash": widened_hash,
    }
    assert canonical_signed_treatment_v1_payload(
        **widened_signed
    ) != canonical_signed_treatment_v1_payload(**signed)


def test_operation_narrowing_changes_context_hash() -> None:
    original = treatment_context_hash_v1(**CONTEXT)
    narrowed = {
        **CONTEXT,
        "allowed_operations": (
            ClinicalAccessOperation.READ_CLINICAL_HISTORY.value,
            ClinicalAccessOperation.CREATE_ENCOUNTER.value,
        ),
    }
    assert treatment_context_hash_v1(**narrowed) != original


def test_provider_session_rebinding_changes_context_hash_and_signed_bytes() -> None:
    original = _signed_fields()
    rebound_context = {
        **CONTEXT,
        "provider_session_binding_hash": "d" * 64,
    }
    rebound_hash = treatment_context_hash_v1(**rebound_context)
    rebound_signed = {
        **original,
        "provider_session_binding_hash": rebound_context["provider_session_binding_hash"],
        "treatment_context_hash": rebound_hash,
    }

    assert rebound_hash != original["treatment_context_hash"]
    assert canonical_signed_treatment_v1_payload(
        **rebound_signed
    ) != canonical_signed_treatment_v1_payload(**original)


def test_unknown_operation_is_rejected_fail_closed() -> None:
    with pytest.raises(
        TreatmentSessionV1ProtocolError, match="unknown treatment operation"
    ):
        normalize_treatment_operations(("WRITE_ANYTHING",))


def test_duplicate_operation_is_rejected_fail_closed() -> None:
    operation = ClinicalAccessOperation.WRITE_VITALS.value
    with pytest.raises(
        TreatmentSessionV1ProtocolError, match="duplicate treatment operation"
    ):
        normalize_treatment_operations((operation, operation))


def test_empty_operation_set_is_rejected_fail_closed() -> None:
    with pytest.raises(TreatmentSessionV1ProtocolError, match="at least one"):
        normalize_treatment_operations(())


def test_string_is_not_accepted_as_operation_sequence() -> None:
    with pytest.raises(TreatmentSessionV1ProtocolError, match="must be a sequence"):
        normalize_treatment_operations(ClinicalAccessOperation.WRITE_VITALS.value)


@pytest.mark.parametrize("access_duration", [0, 299, 3601, 999999])
def test_out_of_policy_access_duration_is_rejected(access_duration: int) -> None:
    with pytest.raises(TreatmentSessionV1ProtocolError, match="outside"):
        canonical_treatment_context_v1(
            **{**CONTEXT, "access_duration": access_duration}
        )


def test_boolean_access_duration_is_rejected_even_though_bool_is_int_subclass() -> None:
    with pytest.raises(TreatmentSessionV1ProtocolError, match="integer"):
        canonical_treatment_context_v1(**{**CONTEXT, "access_duration": True})


def test_noncanonical_purpose_whitespace_is_rejected() -> None:
    with pytest.raises(TreatmentSessionV1ProtocolError, match="canonical"):
        canonical_treatment_context_v1(**{**CONTEXT, "purpose": " treatment "})


@pytest.mark.parametrize(
    "provider_session_binding_hash",
    ["", "A" * 64, "g" * 64, "a" * 63],
)
def test_invalid_provider_session_binding_hash_is_rejected(
    provider_session_binding_hash: str,
) -> None:
    with pytest.raises(TreatmentSessionV1ProtocolError, match="binding hash"):
        canonical_treatment_context_v1(
            **{
                **CONTEXT,
                "provider_session_binding_hash": provider_session_binding_hash,
            }
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("request_id", "aaaaaaaa-1111-4111-8111-111111111111"),
        ("patient_id", "bbbbbbbb-2222-4222-8222-222222222222"),
        ("provider_id", "cccccccc-3333-4333-8333-333333333333"),
        ("hospital_id", "dddddddd-4444-4444-8444-444444444444"),
        ("provider_session_binding_hash", "d" * 64),
        ("challenge_nonce", "treatment-session-nonce-substituted"),
        ("purpose", "care_coordination"),
        ("access_duration", 1200),
        ("issued_at", "2026-09-17T00:00:01+00:00"),
        ("expires_at", "2026-09-17T00:02:01+00:00"),
    ],
)
def test_every_scalar_context_field_changes_context_hash(
    field: str, replacement: object
) -> None:
    mutated = {**CONTEXT, field: replacement}
    assert treatment_context_hash_v1(**mutated) != treatment_context_hash_v1(**CONTEXT)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("decision", "denied"),
        ("treatment_context_hash", "c" * 64),
        ("device_id", "eeeeeeee-5555-4555-8555-555555555555"),
        ("key_id", "ffffffff-6666-4666-8666-666666666666"),
        ("key_version", 4),
        ("public_key_fingerprint", "d" * 64),
    ],
)
def test_every_decision_only_field_changes_signed_bytes(
    field: str, replacement: object
) -> None:
    original = _signed_fields()
    mutated = {**original, field: replacement}
    assert canonical_signed_treatment_v1_payload(
        **mutated
    ) != canonical_signed_treatment_v1_payload(**original)


def test_context_hash_matches_sha256_of_canonical_context() -> None:
    canonical = canonical_treatment_context_v1(**CONTEXT)
    assert treatment_context_hash_v1(**CONTEXT) == hashlib.sha256(canonical).hexdigest()


def test_signed_payload_carries_exact_context_and_no_scope_alias() -> None:
    signed = json.loads(canonical_signed_treatment_v1_payload(**_signed_fields()))
    context = json.loads(canonical_treatment_context_v1(**CONTEXT))

    for field, value in context.items():
        assert signed[field] == value
    assert "scope" not in signed
    assert signed["treatment_context_hash"] == hashlib.sha256(
        canonical_treatment_context_v1(**CONTEXT)
    ).hexdigest()


def test_canonical_bytes_are_compact_and_deterministic() -> None:
    first = canonical_signed_treatment_v1_payload(**_signed_fields())
    second = canonical_signed_treatment_v1_payload(
        **dict(reversed(list(_signed_fields().items())))
    )

    assert first == second
    assert b" " not in first
    assert b"\n" not in first

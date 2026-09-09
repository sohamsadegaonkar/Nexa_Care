"""Adversarial contract tests for Signed Consent V3 canonicalization.

These tests deliberately do not mock or exercise a route.  They lock the
protocol bytes themselves so a future refactor cannot silently remove a bound
field, collapse V3 into the legacy V2 domain, or make two distinct consent
contexts serialize to the same signed message.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.services.signed_approval_verifier import canonical_signed_approval_payload
from app.services.signed_consent_v3 import (
    SIGNED_CONSENT_V3_DOMAIN,
    SIGNED_CONSENT_V3_OPERATION,
    SIGNED_CONSENT_V3_PROTOCOL_VERSION,
    canonical_consent_context_v3,
    canonical_signed_consent_v3_payload,
    consent_context_hash_v3,
)


CONTEXT = {
    "request_id": "11111111-1111-4111-8111-111111111111",
    "patient_id": "22222222-2222-4222-8222-222222222222",
    "provider_id": "33333333-3333-4333-8333-333333333333",
    "hospital_id": "44444444-4444-4444-8444-444444444444",
    "challenge_nonce": "nonce-v3-original",
    "purpose": "treatment",
    "scope": "clinical",
    "access_duration": 900,
    "issued_at": "2026-09-09T05:00:00+00:00",
    "expires_at": "2026-09-09T05:02:00+00:00",
}


def _signed_fields() -> dict:
    context_hash = consent_context_hash_v3(**CONTEXT)
    return {
        **CONTEXT,
        "decision": "approved",
        "consent_context_hash": context_hash,
        "device_id": "55555555-5555-4555-8555-555555555555",
        "key_id": "66666666-6666-4666-8666-666666666666",
        "key_version": 3,
        "public_key_fingerprint": "a" * 64,
    }


def _mutated_value(field: str, value: object) -> object:
    replacements: dict[str, object] = {
        "request_id": "aaaaaaaa-1111-4111-8111-111111111111",
        "patient_id": "bbbbbbbb-2222-4222-8222-222222222222",
        "provider_id": "cccccccc-3333-4333-8333-333333333333",
        "hospital_id": "dddddddd-4444-4444-8444-444444444444",
        "challenge_nonce": "nonce-v3-substituted",
        "purpose": "care_coordination",
        "scope": "full",
        "access_duration": 1200,
        "issued_at": "2026-09-09T05:00:01+00:00",
        "expires_at": "2026-09-09T05:02:01+00:00",
        "decision": "denied",
        "consent_context_hash": "b" * 64,
        "device_id": "eeeeeeee-5555-4555-8555-555555555555",
        "key_id": "ffffffff-6666-4666-8666-666666666666",
        "key_version": 4,
        "public_key_fingerprint": "c" * 64,
    }
    replacement = replacements[field]
    assert replacement != value
    return replacement


def test_v3_protocol_has_explicit_domain_and_operation() -> None:
    payload = json.loads(canonical_signed_consent_v3_payload(**_signed_fields()))

    assert SIGNED_CONSENT_V3_PROTOCOL_VERSION == "nexa-consent-v3"
    assert SIGNED_CONSENT_V3_DOMAIN == "NEXA_CARE_SIGNED_CONSENT"
    assert SIGNED_CONSENT_V3_OPERATION == "CONSENT_DECISION"
    assert payload["protocol_version"] == SIGNED_CONSENT_V3_PROTOCOL_VERSION
    assert payload["domain"] == SIGNED_CONSENT_V3_DOMAIN
    assert payload["operation"] == SIGNED_CONSENT_V3_OPERATION


def test_v3_is_cryptographically_domain_separated_from_v2() -> None:
    v2 = canonical_signed_approval_payload(
        request_id=CONTEXT["request_id"],
        patient_id=CONTEXT["patient_id"],
        provider_id=CONTEXT["provider_id"],
        challenge_nonce=CONTEXT["challenge_nonce"],
        decision="approved",
        scope=CONTEXT["scope"],
        purpose=CONTEXT["purpose"],
        access_duration=CONTEXT["access_duration"],
        issued_at=CONTEXT["issued_at"],
        expires_at=CONTEXT["expires_at"],
        device_id="66666666-6666-4666-8666-666666666666",
    )
    v3 = canonical_signed_consent_v3_payload(**_signed_fields())

    assert v2 != v3
    assert hashlib.sha256(v2).digest() != hashlib.sha256(v3).digest()
    assert b"nexa-consent-v2" in v2
    assert b"nexa-consent-v3" in v3


@pytest.mark.parametrize("field", list(CONTEXT))
def test_every_server_context_field_changes_context_hash(field: str) -> None:
    original = consent_context_hash_v3(**CONTEXT)
    mutated = dict(CONTEXT)
    mutated[field] = _mutated_value(field, CONTEXT[field])

    assert consent_context_hash_v3(**mutated) != original


@pytest.mark.parametrize(
    "field",
    [
        "request_id",
        "patient_id",
        "provider_id",
        "hospital_id",
        "challenge_nonce",
        "decision",
        "purpose",
        "scope",
        "access_duration",
        "issued_at",
        "expires_at",
        "consent_context_hash",
        "device_id",
        "key_id",
        "key_version",
        "public_key_fingerprint",
    ],
)
def test_every_signed_v3_field_changes_canonical_decision(field: str) -> None:
    original_fields = _signed_fields()
    original = canonical_signed_consent_v3_payload(**original_fields)
    mutated = dict(original_fields)
    mutated[field] = _mutated_value(field, original_fields[field])

    assert canonical_signed_consent_v3_payload(**mutated) != original


def test_context_hash_matches_sha256_of_canonical_context() -> None:
    canonical = canonical_consent_context_v3(**CONTEXT)
    assert consent_context_hash_v3(**CONTEXT) == hashlib.sha256(canonical).hexdigest()


def test_canonical_bytes_are_deterministic_and_sorted() -> None:
    first = canonical_signed_consent_v3_payload(**_signed_fields())
    second = canonical_signed_consent_v3_payload(**dict(reversed(list(_signed_fields().items()))))
    decoded = first.decode("utf-8")

    assert first == second
    assert decoded.index('"access_duration"') < decoded.index('"challenge_nonce"')
    assert decoded.index('"hospital_id"') < decoded.index('"patient_id"')
    assert decoded.index('"key_id"') < decoded.index('"key_version"')
    assert b" " not in first
    assert b"\n" not in first


def test_context_and_decision_payload_bind_same_request_context() -> None:
    signed = json.loads(canonical_signed_consent_v3_payload(**_signed_fields()))
    context = json.loads(canonical_consent_context_v3(**CONTEXT))

    for field in CONTEXT:
        assert signed[field] == context[field]
    assert signed["consent_context_hash"] == hashlib.sha256(
        canonical_consent_context_v3(**CONTEXT)
    ).hexdigest()

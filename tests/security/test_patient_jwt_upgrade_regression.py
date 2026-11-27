"""Adversarial patient JWT checks for the upgraded PyJWT dependency.

These are synthetic, signed/unsigned test-only tokens. Never use patient
sessions or production signing keys in this suite.
"""

from __future__ import annotations

import jwt
import pytest

from app.services.patient_auth_service import (
    decode_patient_access_token,
    issue_patient_access_token,
)

SECRET = "synthetic-patient-jwt-security-secret-long-enough"


@pytest.fixture
def valid_claims(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("PATIENT_JWT_SECRET", SECRET)
    token, _expires = issue_patient_access_token(
        "test-patient-1", "test-identity-1", session_id="synthetic-session-1234567890", session_epoch=1
    )
    claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    assert decode_patient_access_token(token) == claims
    return claims


@pytest.mark.parametrize(
    ("algorithm", "key"),
    [
        ("none", None),
        ("HS512", SECRET),
        ("HS256", "different-synthetic-jwt-secret-long-enough"),
    ],
)
def test_patient_jwt_rejects_unsigned_other_algorithm_or_other_key(
    valid_claims: dict, algorithm: str, key: str | None
) -> None:
    forged = jwt.encode(valid_claims, key, algorithm=algorithm)
    assert decode_patient_access_token(forged) is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"actor_type": "provider"},
        {"auth_method": "session_cookie"},
        {"sub": "other-patient"},
        {"patient_id": "other-patient"},
    ],
)
def test_patient_jwt_rejects_wrong_actor_auth_method_or_patient_binding(
    valid_claims: dict, mutation: dict[str, str]
) -> None:
    altered = jwt.encode({**valid_claims, **mutation}, SECRET, algorithm="HS256")
    assert decode_patient_access_token(altered) is None


def test_patient_jwt_rejects_expired_token_even_when_signature_matches(
    valid_claims: dict,
) -> None:
    expired = jwt.encode({**valid_claims, "exp": 1}, SECRET, algorithm="HS256")
    assert decode_patient_access_token(expired) is None

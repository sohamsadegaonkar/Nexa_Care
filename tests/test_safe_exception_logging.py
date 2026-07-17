from __future__ import annotations

import json
import logging

import pytest
from pydantic import BaseModel, ValidationError

from app.observability.safe_exceptions import log_safe_exception


SENSITIVE_VALUES = [
    "1234-5678-9012",
    "ABHA-91-2345-6789",
    "+919876543210",
    "access-token-secret",
    "password=hunter2",
    "biometric-seed-material",
    "Asha Rao",
    "HbA1c 12.4 percent",
    "document contains psychiatric diagnosis",
]


class _Payload(BaseModel):
    patient_uuid: int


@pytest.mark.parametrize("exception_class,subsystem,expected_code", [
    (type("DatabaseError", (Exception,), {}), "database", "DATABASE_OPERATION_FAILED"),
    (type("RedisError", (Exception,), {}), "redis", "REDIS_OPERATION_FAILED"),
    (type("KMSFailure", (Exception,), {}), "kms", "KMS_OPERATION_FAILED"),
])
def test_final_log_record_never_contains_exception_values(
    caplog: pytest.LogCaptureFixture,
    exception_class: type[Exception],
    subsystem: str,
    expected_code: str,
) -> None:
    secret_text = " | ".join(SENSITIVE_VALUES)
    with caplog.at_level(logging.ERROR, logger="safe-exception-test"):
        log_safe_exception(
            logging.getLogger("safe-exception-test"),
            logging.ERROR,
            "operation_failed",
            exception_class(secret_text),
            subsystem=subsystem,
            operation="test_operation",
        )
    emitted = "\n".join(record.getMessage() for record in caplog.records)
    assert expected_code in emitted
    for sensitive in SENSITIVE_VALUES:
        assert sensitive not in emitted


def test_validation_log_has_only_safe_location_rule_and_message(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(ValidationError) as raised:
        _Payload(patient_uuid="access-token-secret")
    with caplog.at_level(logging.WARNING, logger="safe-validation-test"):
        log_safe_exception(
            logging.getLogger("safe-validation-test"),
            logging.WARNING,
            "validation_failed",
            raised.value,
            subsystem="http",
            operation="validate_request",
        )
    payload = json.loads(caplog.records[-1].getMessage())
    assert payload["validation_issues"][0]["location"] == ["patient_uuid"]
    assert set(payload["validation_issues"][0]) == {"location", "rule", "message"}
    assert "access-token-secret" not in caplog.records[-1].getMessage()


def test_nested_chained_and_long_exception_text_is_not_emitted(caplog: pytest.LogCaptureFixture) -> None:
    attacker_text = "Asha Rao " + "access-token-secret" * 10000
    try:
        try:
            raise ValueError(attacker_text)
        except ValueError as inner:
            raise RuntimeError("password=hunter2") from inner
    except RuntimeError as exc:
        with caplog.at_level(logging.ERROR, logger="safe-chain-test"):
            log_safe_exception(
                logging.getLogger("safe-chain-test"),
                logging.ERROR,
                "nested_failure",
                exc,
                subsystem="crypto",
                operation="decrypt",
            )
    emitted = caplog.records[-1].getMessage()
    assert "RuntimeError" in emitted and "ValueError" in emitted
    assert attacker_text not in emitted
    assert "access-token-secret" not in emitted
    assert "password=hunter2" not in emitted

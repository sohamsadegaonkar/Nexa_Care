"""Tests for separated Redis consent state machines."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.services.consent import break_glass, routine


def run(coro):
    return asyncio.run(coro)


@patch("app.services.consent.routine.secrets.token_urlsafe")
@patch("app.services.consent.routine.get_routine_redis_client")
def test_routine_issue_binds_patient_clinician_purpose_scope_nonce_and_ttl(mock_get_redis, mock_token) -> None:
    redis = AsyncMock()
    mock_get_redis.return_value = redis
    mock_token.side_effect = ["token", "nonce"]

    token = run(routine.issue(
        patient_id="patient-1",
        clinician_id="clinician-1",
        purpose="treatment",
        scope=["clinical.diagnoses"],
        ttl=90,
    ))

    assert token == "nexa:routine_consent:token"
    redis.set.assert_awaited_once()
    args, kwargs = redis.set.await_args
    assert args[0] == token
    payload = json.loads(args[1])
    assert payload["patient_id"] == "patient-1"
    assert payload["clinician_id"] == "clinician-1"
    assert payload["purpose"] == "treatment"
    assert payload["scope"] == ["clinical.diagnoses"]
    assert payload["nonce"] == "nonce"
    assert payload["ttl"] == 90
    assert kwargs["ex"] == 90


@patch("app.services.consent.routine.get_routine_redis_client")
def test_routine_validate_and_consume_fail_closed_on_mismatch(mock_get_redis) -> None:
    redis = AsyncMock()
    mock_get_redis.return_value = redis
    payload = json.dumps({
        "patient_id": "patient-1",
        "clinician_id": "clinician-1",
        "purpose": "treatment",
        "scope": ["clinical.diagnoses"],
        "nonce": "nonce",
        "ttl": 90,
        "issued_at": "2026-07-02T00:00:00+00:00",
    })
    redis.get.return_value = payload
    redis.getdel.return_value = payload

    valid = run(routine.validate(
        token="token",
        patient_id="patient-1",
        clinician_id="clinician-1",
        purpose="treatment",
    ))
    assert valid is not None
    assert valid.scope == ["clinical.diagnoses"]

    invalid = run(routine.validate(
        token="token",
        patient_id="patient-1",
        clinician_id="other",
        purpose="treatment",
    ))
    assert invalid is None

    consumed = run(routine.consume(
        token="token",
        patient_id="patient-1",
        clinician_id="clinician-1",
        purpose="treatment",
    ))
    assert consumed is not None
    redis.getdel.assert_awaited_once()


@patch("app.services.consent.break_glass.secrets.token_urlsafe")
@patch("app.services.consent.break_glass.get_break_glass_redis_client")
def test_break_glass_issue_uses_separate_prefix_and_notifies_compliance_queue(mock_get_redis, mock_token) -> None:
    redis = AsyncMock()
    mock_get_redis.return_value = redis
    mock_token.side_effect = ["token", "nonce"]

    token = run(break_glass.issue(
        patient_id="patient-1",
        clinician_id="clinician-1",
        purpose="emergency",
        scope=["clinical.allergies"],
        reason_code="LIFE_THREAT",
        ttl=60,
    ))

    assert token == "nexa:break_glass:token"
    redis.set.assert_awaited_once()
    redis.rpush.assert_awaited_once()
    queue_key, notification_json = redis.rpush.await_args.args
    assert queue_key == break_glass.COMPLIANCE_QUEUE_KEY
    notification = json.loads(notification_json)
    assert notification["reason_code"] == "LIFE_THREAT"
    assert notification["token"] == token

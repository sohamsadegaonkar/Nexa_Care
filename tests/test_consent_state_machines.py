"""Tests for ConsentEngine (app.services.consent_engine).

Phase 1 migration note: this file used to test the standalone
app/services/consent/routine.py and app/services/consent/break_glass.py
state machines directly. Both were folded into ConsentEngine
(docs/CURRENT-STATE.md, Section 1 / Section 4) and no longer exist as
separate modules, so these tests now exercise issue()/validate()/consume()
on ConsentEngine itself, covering both the routine grant path and the
break-glass path (is_break_glass=True routes into the same compliance
queue the old break_glass.py used).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import consent_engine


def run(coro):
    return asyncio.run(coro)


def make_fake_db():
    db = AsyncMock()
    db.add = lambda row: None
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    return db


@patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock)
@patch("app.services.consent_engine.append_audit_log", new_callable=AsyncMock)
@patch("app.services.consent_engine.get_consent_redis_client")
def test_routine_issue_binds_patient_clinician_purpose_scope_and_ttl(
    mock_get_redis, mock_audit, mock_audit_503
) -> None:
    from app.models.assurance import AssuranceLevel

    redis = AsyncMock()
    mock_get_redis.return_value = redis
    fake_db = make_fake_db()

    token = run(
        consent_engine.issue(
            db=fake_db,
            patient_id="patient-1",
            clinician_id="clinician-1",
            purpose="treatment",
            scope=["clinical.diagnoses"],
            assurance_level=AssuranceLevel.STANDARD,
            assurance_evidence={},
            ttl_seconds=90,
        )
    )

    assert token
    redis.set.assert_awaited_once()
    args, kwargs = redis.set.await_args
    assert args[0] == consent_engine._token_key(token)
    payload = json.loads(args[1])
    assert payload["patient_id"] == "patient-1"
    assert payload["clinician_id"] == "clinician-1"
    assert payload["purpose"] == "treatment"
    assert payload["scope"] == ["clinical.diagnoses"]
    assert payload["is_break_glass"] is False
    assert kwargs["ex"] == 90
    redis.rpush.assert_not_awaited()


@patch("app.services.consent_engine.get_consent_redis_client")
def test_validate_and_consume_fail_closed_on_mismatch(mock_get_redis) -> None:
    redis = AsyncMock()
    mock_get_redis.return_value = redis
    payload = json.dumps(
        {
            "patient_id": "patient-1",
            "clinician_id": "clinician-1",
            "purpose": "treatment",
            "scope": ["clinical.diagnoses"],
            "is_break_glass": False,
            "reason_code": None,
            "issued_at": "2026-07-02T00:00:00+00:00",
            "expires_at": "2026-07-02T01:00:00+00:00",
        }
    )
    redis.get.return_value = payload
    redis.getdel.return_value = payload

    valid = run(
        consent_engine.validate(
            token="token",
            patient_id="patient-1",
            clinician_id="clinician-1",
            purpose="treatment",
        )
    )
    assert valid is not None
    assert valid.scope == ["clinical.diagnoses"]

    invalid = run(
        consent_engine.validate(
            token="token",
            patient_id="patient-1",
            clinician_id="other",
            purpose="treatment",
        )
    )
    assert invalid is None

    fake_db = make_fake_db()
    with patch("app.services.consent_engine.append_audit_log", new_callable=AsyncMock):
        consumed = run(
            consent_engine.consume(
                db=fake_db,
                token="token",
                patient_id="patient-1",
                clinician_id="clinician-1",
                purpose="treatment",
            )
        )
    assert consumed is not None
    redis.getdel.assert_awaited_once()


@patch("app.services.consent_engine.append_audit_log_or_503", new_callable=AsyncMock)
@patch("app.services.consent_engine.append_audit_log", new_callable=AsyncMock)
@patch("app.services.consent_engine.get_consent_redis_client")
def test_break_glass_issue_uses_break_glass_ttl_and_notifies_compliance_queue(
    mock_get_redis, mock_audit, mock_audit_503
) -> None:
    from app.models.assurance import AssuranceLevel

    redis = AsyncMock()
    mock_get_redis.return_value = redis
    fake_db = make_fake_db()

    token = run(
        consent_engine.issue(
            db=fake_db,
            patient_id="patient-1",
            clinician_id="clinician-1",
            purpose="emergency",
            scope=["clinical.allergies"],
            assurance_level=AssuranceLevel.BREAK_GLASS,
            assurance_evidence={},
            is_break_glass=True,
            reason_code="LIFE_THREAT",
        )
    )

    assert token
    redis.set.assert_awaited_once()
    args, kwargs = redis.set.await_args
    assert kwargs["ex"] == consent_engine.BREAK_GLASS_TTL_SECONDS
    redis.rpush.assert_awaited_once()
    queue_key, notification_json = redis.rpush.await_args.args
    assert queue_key == consent_engine.COMPLIANCE_QUEUE_KEY
    notification = json.loads(notification_json)
    assert notification["reason_code"] == "LIFE_THREAT"
    assert notification["patient_id"] == "patient-1"

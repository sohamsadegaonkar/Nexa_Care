"""Security tests — T-02: Forged Assurance Claims.

Verifies that the AssuranceVerifier rejects:
- PUSH_BIOMETRIC claims without a real push approval in Redis
- Fabricated request_id not present in Redis
- Consent issue with push_biometric assurance and fake evidence → 403

Uses the real RedisAssuranceVerifier with FakeRedis.

Threat model reference: docs/threat-model.md T-02
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest

from app.models.assurance import AssuranceLevel
from app.services.assurance_verifier import RedisAssuranceVerifier
from tests.conftest import FakeRedis


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_redis():
    return FakeRedis()


# ── Tests ────────────────────────────────────────────────────────────────────


def test_forged_assurance_fabricated_request_id(fake_redis):
    """T-02a: PUSH_BIOMETRIC claim with fabricated request_id → verified=False.

    The request_id was never created by the push notification system.
    Redis has no key for it, so the verifier returns verified=False.
    """
    verifier = RedisAssuranceVerifier()
    fake_request_id = str(uuid.uuid4())
    patient_id = str(uuid.uuid4())

    result = asyncio.run(
        verifier.verify(
            level=AssuranceLevel.PUSH_BIOMETRIC,
            patient_id=patient_id,
            evidence={"request_id": fake_request_id},
            redis=fake_redis,
        )
    )
    assert not result.verified, "Fabricated request_id must fail assurance verification"


def test_forged_assurance_level_elevation(fake_redis):
    """T-02b: PUSH_BIOMETRIC with empty evidence → verified=False.

    No push notification was ever sent; the caller just claims the
    high assurance level without any evidence.
    """
    verifier = RedisAssuranceVerifier()
    patient_id = str(uuid.uuid4())

    result = asyncio.run(
        verifier.verify(
            level=AssuranceLevel.PUSH_BIOMETRIC,
            patient_id=patient_id,
            evidence={},  # no request_id at all
            redis=fake_redis,
        )
    )
    assert not result.verified, "Empty evidence must fail PUSH_BIOMETRIC verification"


def test_forged_assurance_expired_push_state(fake_redis):
    """T-02c: PUSH_BIOMETRIC with expired Redis state → verified=False.

    The push was once approved but the Redis key has expired.
    FakeRedis TTL simulation: set data then advance past TTL.
    """
    import time

    request_id = str(uuid.uuid4())
    patient_id = str(uuid.uuid4())

    # Seed an approved push state
    push_data = json.dumps(
        {
            "status": "approved",
            "patient_id": patient_id,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    key = f"assurance_evidence:{request_id}"
    fake_redis.data[key] = push_data
    fake_redis.ttls[key] = time.time() - 1  # already expired

    verifier = RedisAssuranceVerifier()
    result = asyncio.run(
        verifier.verify(
            level=AssuranceLevel.PUSH_BIOMETRIC,
            patient_id=patient_id,
            evidence={"request_id": request_id},
            redis=fake_redis,
        )
    )
    assert not result.verified, "Expired push state must fail verification"


def test_forged_assurance_wrong_patient_in_push(fake_redis):
    """T-02d: PUSH_BIOMETRIC with push approved for different patient → verified=False.

    The push was approved for Patient A, but the caller requests
    consent for Patient B. The verifier must check patient_id binding.
    """
    request_id = str(uuid.uuid4())
    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())

    # Seed push state approved for Patient A
    push_data = json.dumps(
        {
            "status": "approved",
            "patient_id": patient_a,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    key = f"assurance_evidence:{request_id}"
    fake_redis.data[key] = push_data
    fake_redis.ttls[key] = 9999999999  # far future

    verifier = RedisAssuranceVerifier()
    # Try to use it for Patient B
    result = asyncio.run(
        verifier.verify(
            level=AssuranceLevel.PUSH_BIOMETRIC,
            patient_id=patient_b,
            evidence={"request_id": request_id},
            redis=fake_redis,
        )
    )
    assert (
        not result.verified
    ), "Push approved for different patient must fail verification"


def test_forged_assurance_pending_not_approved(fake_redis):
    """T-02e: PUSH_BIOMETRIC with status=pending (not yet approved) → verified=False.

    The push request exists in Redis but hasn't been approved yet.
    """
    request_id = str(uuid.uuid4())
    patient_id = str(uuid.uuid4())

    push_data = json.dumps(
        {
            "status": "pending",
            "patient_id": patient_id,
        }
    )
    key = f"assurance_evidence:{request_id}"
    fake_redis.data[key] = push_data
    fake_redis.ttls[key] = 9999999999

    verifier = RedisAssuranceVerifier()
    result = asyncio.run(
        verifier.verify(
            level=AssuranceLevel.PUSH_BIOMETRIC,
            patient_id=patient_id,
            evidence={"request_id": request_id},
            redis=fake_redis,
        )
    )
    assert not result.verified, "Pending (not yet approved) push must fail verification"


def test_standard_assurance_always_passes(fake_redis):
    """STANDARD assurance level always verifies (session-bound)."""
    verifier = RedisAssuranceVerifier()
    result = asyncio.run(
        verifier.verify(
            level=AssuranceLevel.STANDARD,
            patient_id=str(uuid.uuid4()),
            evidence={},
            redis=fake_redis,
        )
    )
    assert result.verified
    assert result.actual_level == AssuranceLevel.STANDARD

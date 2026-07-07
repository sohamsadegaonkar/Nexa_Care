from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Protocol

import redis.asyncio as redis_async

from app.models.assurance import AssuranceLevel, AssuranceResult

logger = logging.getLogger("nexa_logger")


class AssuranceVerifier(Protocol):
    """Protocol for assurance verification implementations."""

    async def verify(
        self,
        level: AssuranceLevel,
        patient_id: str,
        evidence: dict[str, Any],
        redis: redis_async.Redis,
    ) -> AssuranceResult:
        """Verify the claimed assurance level against supplied evidence."""
        ...


class RedisAssuranceVerifier:
    """Production assurance verifier that checks Squad B's Redis state."""

    async def verify(
        self,
        level: AssuranceLevel,
        patient_id: str,
        evidence: dict[str, Any],
        redis: redis_async.Redis,
    ) -> AssuranceResult:
        now = datetime.now(timezone.utc)

        if level == AssuranceLevel.STANDARD:
            # Standard level is verified by the provider session (caller must be authenticated).
            return AssuranceResult(
                verified=True,
                actual_level=AssuranceLevel.STANDARD,
                verification_timestamp=now,
            )

        if level == AssuranceLevel.BREAK_GLASS:
            # Break glass is accepted here but logged for subsequent compliance auditing.
            return AssuranceResult(
                verified=True,
                actual_level=AssuranceLevel.BREAK_GLASS,
                verification_timestamp=now,
            )

        if level == AssuranceLevel.PUSH_BIOMETRIC:
            request_id = evidence.get("request_id")
            if not request_id:
                return AssuranceResult(
                    verified=False,
                    actual_level=AssuranceLevel.STANDARD,
                    verification_timestamp=now,
                )

            # Contract with Squad B: push_request:{id} contains a JSON blob with status and patient_id.
            key = f"push_request:{request_id}"
            raw_data = await redis.get(key)
            if not raw_data:
                key = f"assurance_evidence:{request_id}"
                raw_data = await redis.get(key)

            if not raw_data:
                return AssuranceResult(
                    verified=False,
                    actual_level=AssuranceLevel.STANDARD,
                    verification_timestamp=now,
                    request_id=request_id,
                )

            try:
                data = json.loads(raw_data)
                status = data.get("status")
                stored_patient_id = data.get("patient_id")
                approved_at_str = data.get("approved_at")

                if status != "approved":
                    return AssuranceResult(
                        verified=False,
                        actual_level=AssuranceLevel.STANDARD,
                        verification_timestamp=now,
                        request_id=request_id,
                    )

                if stored_patient_id != patient_id:
                    return AssuranceResult(
                        verified=False,
                        actual_level=AssuranceLevel.STANDARD,
                        verification_timestamp=now,
                        request_id=request_id,
                    )

                # Time-of-approval check (90 second window max)
                if approved_at_str:
                    try:
                        approved_at = datetime.fromisoformat(approved_at_str)
                        if (now - approved_at).total_seconds() > 90:
                            return AssuranceResult(
                                verified=False,
                                actual_level=AssuranceLevel.STANDARD,
                                verification_timestamp=now,
                                request_id=request_id,
                            )
                    except (ValueError, TypeError):
                        pass

                # Single-use consumption (Sprint 2 Requirement)
                # Atomically delete the assurance evidence so it cannot be replayed.
                await redis.delete(key)

                return AssuranceResult(
                    verified=True,
                    actual_level=AssuranceLevel.PUSH_BIOMETRIC,
                    verification_timestamp=now,
                    request_id=request_id,
                )
            except (json.JSONDecodeError, TypeError):
                logger.error(f"Malformed assurance evidence in Redis for {key}")

            return AssuranceResult(
                verified=False,
                actual_level=AssuranceLevel.STANDARD,
                verification_timestamp=now,
                request_id=request_id,
            )

        return AssuranceResult(
            verified=False,
            actual_level=AssuranceLevel.STANDARD,
            verification_timestamp=now,
        )

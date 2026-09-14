"""Server-owned abuse controls for patient discovery.

Low-entropy discovery modes must not become efficient account-enumeration
oracles.  These budgets are intentionally independent of the searched value:
Redis keys contain only server-resolved provider/hospital context, the closed
identifier type, and the window.  Raw or normalized patient identifiers never
enter rate-limit keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.core.rate_limiter import atomic_fixed_window


class DiscoveryAbuseControlError(RuntimeError):
    """Base class for stable discovery-abuse-control failures."""


class DiscoveryAbuseControlUnavailable(DiscoveryAbuseControlError):
    """Raised when Redis enforcement cannot be trusted."""


class DiscoveryRateLimited(DiscoveryAbuseControlError):
    """Raised after one or more server-owned discovery budgets are exceeded."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__("DISCOVERY_RATE_LIMITED")


@dataclass(frozen=True, slots=True)
class DiscoveryBudget:
    window_seconds: int
    limit: int


# Public IDs are opaque and substantially higher entropy than phone numbers.
# Phone therefore receives a deliberately smaller provider/hospital budget.
_TYPE_BUDGETS: Final[dict[str, tuple[DiscoveryBudget, ...]]] = {
    "NEXA_PUBLIC_ID": (
        DiscoveryBudget(window_seconds=60, limit=12),
        DiscoveryBudget(window_seconds=3600, limit=120),
    ),
    "PHONE": (
        DiscoveryBudget(window_seconds=60, limit=4),
        DiscoveryBudget(window_seconds=3600, limit=30),
    ),
}

# Prevent identifier-type hopping from multiplying the provider/hospital search
# budget.  These are evaluated in addition to the per-type limits.
_GLOBAL_BUDGETS: Final[tuple[DiscoveryBudget, ...]] = (
    DiscoveryBudget(window_seconds=60, limit=20),
    DiscoveryBudget(window_seconds=3600, limit=180),
)


def _budget_key(
    *,
    provider_id: str,
    hospital_id: str,
    identifier_type: str,
    window_seconds: int,
) -> str:
    return (
        "patient_discovery:budget:"
        f"{identifier_type}:{window_seconds}:{provider_id}:{hospital_id}"
    )


async def enforce_patient_discovery_budget(
    redis,
    *,
    provider_id: str,
    hospital_id: str,
    identifier_type: str,
) -> None:
    """Atomically enforce closed per-type and aggregate discovery budgets.

    The searched identifier is intentionally not accepted by this function, so
    future callers cannot accidentally place phone/public-ID material into a
    Redis key or rate-limit diagnostic.
    """

    type_budgets = _TYPE_BUDGETS.get(identifier_type)
    if type_budgets is None:
        raise ValueError("Unsupported patient-discovery identifier type")
    if not provider_id or not hospital_id:
        raise ValueError("Provider and hospital context are required")

    exceeded_ttls: list[int] = []
    checks = [
        (identifier_type, budget) for budget in type_budgets
    ] + [("ALL", budget) for budget in _GLOBAL_BUDGETS]

    try:
        for budget_type, budget in checks:
            count, ttl = await atomic_fixed_window(
                redis,
                _budget_key(
                    provider_id=provider_id,
                    hospital_id=hospital_id,
                    identifier_type=budget_type,
                    window_seconds=budget.window_seconds,
                ),
                budget.window_seconds,
            )
            if count > budget.limit:
                exceeded_ttls.append(max(1, int(ttl)))
    except Exception as exc:
        raise DiscoveryAbuseControlUnavailable(
            "Patient discovery abuse control is unavailable"
        ) from exc

    if exceeded_ttls:
        raise DiscoveryRateLimited(max(exceeded_ttls))

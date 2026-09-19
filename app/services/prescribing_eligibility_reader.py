"""Read and revalidate current prescribing-specific professional authority."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.provider import (
    PrescribingEligibilityDecision,
    ProfessionalVerification,
)
from app.services.prescribing_eligibility_policy import (
    current_prescribing_eligibility_denial,
)


class PrescribingEligibilityUnavailable(RuntimeError):
    """Authoritative prescribing state could not be loaded."""


class PrescribingEligibilityDenied(RuntimeError):
    """Current prescribing-specific professional authority is not positive."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def load_current_prescribing_authority(
    db: AsyncSession,
    *,
    provider_id: UUID,
    lock_professional: bool = False,
) -> tuple[ProfessionalVerification | None, PrescribingEligibilityDecision | None]:
    """Load the exact professional row and latest immutable decision."""

    try:
        professional_stmt = select(ProfessionalVerification).where(
            ProfessionalVerification.provider_id == provider_id
        )
        if lock_professional:
            professional_stmt = professional_stmt.with_for_update()
        professional = (await db.execute(professional_stmt)).scalar_one_or_none()
        decision = (
            await db.execute(
                select(PrescribingEligibilityDecision)
                .where(PrescribingEligibilityDecision.provider_id == provider_id)
                .order_by(PrescribingEligibilityDecision.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return professional, decision
    except Exception as exc:
        raise PrescribingEligibilityUnavailable(
            "prescribing authority store unavailable"
        ) from exc


async def assert_current_prescribing_eligibility(
    db: AsyncSession,
    *,
    provider_id: UUID,
    now: datetime | None = None,
    lock_professional: bool = False,
) -> PrescribingEligibilityDecision:
    """Fail closed unless the latest prescribing decision is currently positive."""

    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise PrescribingEligibilityDenied("PRESCRIBING_ELIGIBILITY_INTEGRITY_FAILURE")

    professional, decision = await load_current_prescribing_authority(
        db,
        provider_id=provider_id,
        lock_professional=lock_professional,
    )
    denial = current_prescribing_eligibility_denial(
        professional,
        decision,
        now=moment,
    )
    if denial is not None:
        raise PrescribingEligibilityDenied(denial)
    assert decision is not None
    return decision

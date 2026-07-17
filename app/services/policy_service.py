from datetime import datetime, timezone
import inspect
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_policy import PatientPolicy


class PolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_policy(self, patient_uuid: UUID) -> str:
        policy = await self.db.get(PatientPolicy, patient_uuid)
        if policy:
            value = getattr(policy, "consent_assurance_policy", None)
            if inspect.isawaitable(value):
                value = await value
            if isinstance(value, str) and value.strip():
                return value
        return "standard"

    async def set_policy(self, patient_uuid: UUID, policy: str) -> str:
        """Atomically upsert a patient's assurance policy.

        Uses PostgreSQL ON CONFLICT so concurrent first writes for the same
        patient cannot create duplicate rows or leak an IntegrityError.
        Last committed write wins.
        """
        now = datetime.now(timezone.utc).isoformat()
        stmt = (
            insert(PatientPolicy)
            .values(
                patient_uuid=patient_uuid,
                consent_assurance_policy=policy,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[PatientPolicy.patient_uuid],
                set_={
                    "consent_assurance_policy": policy,
                    "updated_at": now,
                },
            )
        )
        await self.db.execute(stmt)
        await self.db.commit()
        return policy

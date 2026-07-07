from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID
from app.models.patient_policy import PatientPolicy

class PolicyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_policy(self, patient_uuid: UUID) -> str:
        policy = await self.db.get(PatientPolicy, patient_uuid)
        if policy:
            return policy.consent_assurance_policy
        return "standard"

    async def set_policy(self, patient_uuid: UUID, policy: str) -> str:
        existing = await self.db.get(PatientPolicy, patient_uuid)
        if existing:
            existing.consent_assurance_policy = policy
        else:
            new_policy = PatientPolicy(
                patient_uuid=patient_uuid,
                consent_assurance_policy=policy
            )
            self.db.add(new_policy)
        await self.db.commit()
        return policy
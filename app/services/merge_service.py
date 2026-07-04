"""
Patient Merge Service (Section 9 - Alias & Tombstone)
"""

from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, insert

from app.models.patient_tombstone import PatientTombstone
from app.models.patient import Patient


class PatientMergeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def merge_patients(
        self,
        *,
        old_uuid: UUID,
        canonical_uuid: UUID,
        reason: str,
        evidence: dict | None = None,
        merged_by: str = "system",
    ) -> PatientTombstone:
        """Perform supervised patient merge"""

        # Verify both patients exist
        old_patient = await self.db.get(Patient, old_uuid)
        canonical_patient = await self.db.get(Patient, canonical_uuid)

        if not old_patient or not canonical_patient:
            raise ValueError("One or both patients not found")

        if old_uuid == canonical_uuid:
            raise ValueError("Cannot merge a patient with itself")

        # Create tombstone
        tombstone = PatientTombstone(
            old_patient_uuid=old_uuid,
            canonical_patient_uuid=canonical_uuid,
            merged_at=datetime.now(timezone.utc),
            merged_by=merged_by,
            reason=reason,
            evidence=evidence,
        )

        self.db.add(tombstone)

        # Mark old patient as tombstoned (soft delete)
        old_patient.is_deleted = True
        old_patient.updated_at = datetime.now(timezone.utc)

        await self.db.commit()
        await self.db.refresh(tombstone)

        return tombstone
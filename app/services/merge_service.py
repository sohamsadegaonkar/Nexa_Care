"""
Patient Merge Service (Section 9 - Alias & Tombstone)
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_tombstone import PatientTombstone
from app.models.patient import Patient


class PatientMergeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _tombstones_for_old(self, patient_uuid: UUID) -> list[PatientTombstone]:
        result = await self.db.execute(
            select(PatientTombstone).where(PatientTombstone.old_patient_uuid == patient_uuid)
        )
        return list(result.scalars().all())

    async def _single_tombstone_for_old(self, patient_uuid: UUID) -> PatientTombstone | None:
        rows = await self._tombstones_for_old(patient_uuid)
        if len(rows) > 1:
            raise ValueError(f"Duplicate tombstones found for patient {patient_uuid}")
        return rows[0] if rows else None

    async def _resolve_canonical_target(self, target_uuid: UUID, forbidden_old_uuid: UUID) -> UUID:
        """Resolve a target through existing tombstones while rejecting cycles."""

        current = target_uuid
        seen: set[UUID] = {forbidden_old_uuid}
        while True:
            if current in seen:
                raise ValueError("Merge would create a tombstone cycle")
            seen.add(current)
            tombstone = await self._single_tombstone_for_old(current)
            if tombstone is None:
                return current
            current = tombstone.canonical_patient_uuid

    async def merge_patients(
        self,
        *,
        old_uuid: UUID,
        canonical_uuid: UUID,
        reason: str,
        evidence: dict | None = None,
        merged_by: str = "system",
    ) -> PatientTombstone:
        """Perform supervised patient merge with duplicate and cycle guards."""

        if old_uuid == canonical_uuid:
            raise ValueError("Cannot merge a patient with itself")

        # Verify both submitted patients exist before following canonical chains.
        old_patient = await self.db.get(Patient, old_uuid)
        canonical_patient = await self.db.get(Patient, canonical_uuid)

        if not old_patient or not canonical_patient:
            raise ValueError("One or both patients not found")

        existing_old_tombstone = await self._single_tombstone_for_old(old_uuid)
        if existing_old_tombstone is not None:
            if existing_old_tombstone.canonical_patient_uuid == canonical_uuid:
                return existing_old_tombstone
            raise ValueError("Patient has already been merged into a different canonical patient")

        if getattr(old_patient, "is_deleted", False):
            raise ValueError("Patient is already tombstoned")

        resolved_canonical_uuid = await self._resolve_canonical_target(canonical_uuid, old_uuid)
        if resolved_canonical_uuid == old_uuid:
            raise ValueError("Merge would create a tombstone cycle")

        resolved_patient = await self.db.get(Patient, resolved_canonical_uuid)
        if not resolved_patient:
            raise ValueError("Resolved canonical patient not found")

        # Create tombstone pointing at the resolved canonical record.
        tombstone = PatientTombstone(
            old_patient_uuid=old_uuid,
            canonical_patient_uuid=resolved_canonical_uuid,
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

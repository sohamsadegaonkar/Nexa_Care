"""
Card Redirection Service for Tombstoned Patients
Handles legacy cards after patient merge (Section 9)
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.patient_tombstone import PatientTombstone
from app.models.nfc_card_registry import NFCCardRegistry


class TombstoneIntegrityError(RuntimeError):
    """Raised when tombstone data is duplicate, cyclic, or ambiguous."""


class CardRedirectService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _tombstones_for_old(self, patient_uuid) -> list[PatientTombstone]:
        tombstone_stmt = select(PatientTombstone).where(
            PatientTombstone.old_patient_uuid == patient_uuid
        )
        tombstone_result = await self.db.execute(tombstone_stmt)
        return list(tombstone_result.scalars().all())

    async def resolve_card_with_redirect(self, card_id: str) -> dict:
        """
        Resolve NFC card, following tombstone chain if patient was merged.
        Returns the canonical patient_uuid and redirect history.
        """
        # First resolve the card to a patient
        stmt = select(NFCCardRegistry).where(
            NFCCardRegistry.card_uid == card_id,
            NFCCardRegistry.status == "active"
        )
        result = await self.db.execute(stmt)
        card = result.scalar_one_or_none()

        if not card:
            return {"error": "CARD_NOT_FOUND"}

        current_uuid = card.patient_id
        redirect_chain = []
        seen = set()

        for _ in range(10):
            if current_uuid in seen:
                raise TombstoneIntegrityError("Tombstone cycle detected during card redirect")
            seen.add(current_uuid)

            tombstones = await self._tombstones_for_old(current_uuid)
            if len(tombstones) > 1:
                raise TombstoneIntegrityError(f"Duplicate tombstones found for patient {current_uuid}")
            if not tombstones:
                break

            tombstone = tombstones[0]
            redirect_chain.append({
                "from": str(current_uuid),
                "to": str(tombstone.canonical_patient_uuid),
                "merged_at": tombstone.merged_at.isoformat()
            })
            current_uuid = tombstone.canonical_patient_uuid
        else:
            raise TombstoneIntegrityError("Tombstone redirect chain exceeded maximum depth")

        return {
            "canonical_patient_uuid": str(current_uuid),
            "redirect_chain": redirect_chain,
            "original_patient_uuid": str(card.patient_id),
            "is_redirected": len(redirect_chain) > 0
        }

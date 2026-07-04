"""
Card Redirection Service for Tombstoned Patients
Handles legacy cards after patient merge (Section 9)
"""

from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.patient_tombstone import PatientTombstone
from app.models.nfc_card_registry import NFCCardRegistry


class CardRedirectService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def resolve_card_with_redirect(self, card_id: str) -> dict:
        """
        Resolve NFC card, following tombstone chain if patient was merged.
        Returns the canonical patient_uuid and redirect history.
        """
        # First resolve the card to a patient
        stmt = select(NFCCardRegistry).where(
            NFCCardRegistry.card_id == card_id,
            NFCCardRegistry.status == "ACTIVE"
        )
        result = await self.db.execute(stmt)
        card = result.scalar_one_or_none()

        if not card:
            return {"error": "CARD_NOT_FOUND"}

        current_uuid = card.patient_uuid
        redirect_chain = []

        # Follow tombstone chain (max 5 hops to prevent infinite loops)
        for _ in range(5):
            tombstone_stmt = select(PatientTombstone).where(
                PatientTombstone.old_patient_uuid == current_uuid
            )
            tombstone_result = await self.db.execute(tombstone_stmt)
            tombstone = tombstone_result.scalar_one_or_none()

            if not tombstone:
                break

            redirect_chain.append({
                "from": str(current_uuid),
                "to": str(tombstone.canonical_patient_uuid),
                "merged_at": tombstone.merged_at.isoformat()
            })
            current_uuid = tombstone.canonical_patient_uuid

        return {
            "canonical_patient_uuid": str(current_uuid),
            "redirect_chain": redirect_chain,
            "original_patient_uuid": str(card.patient_uuid),
            "is_redirected": len(redirect_chain) > 0
        }
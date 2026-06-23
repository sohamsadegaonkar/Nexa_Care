"""Unit tests for NFC card lifecycle ORM model metadata."""

from __future__ import annotations

import unittest

from app.models.base import Base
from app.models.nfc import NFCCardEvent, NFCCardRegistry


class TestNFCModels(unittest.TestCase):
    def test_all_models_registered_on_base_metadata(self) -> None:
        table_names = {table.name for table in Base.metadata.sorted_tables}
        self.assertIn("nfc_card_registry", table_names)
        self.assertIn("nfc_card_event", table_names)

    def test_foreign_keys_and_uniques(self) -> None:
        registry_table = NFCCardRegistry.__table__
        event_table = NFCCardEvent.__table__

        registry_fks = {fk.target_fullname for fk in registry_table.foreign_keys}
        self.assertIn("nexa_vault.id", registry_fks)
        self.assertIn("hospital_registry.id", registry_fks)

        event_fks = {fk.target_fullname for fk in event_table.foreign_keys}
        self.assertIn("nfc_card_registry.id", event_fks)

        unique_names = {
            constraint.name
            for constraint in registry_table.constraints
            if constraint.name
        }
        self.assertIn("uq_nfc_card_registry_card_uid", unique_names)

    def test_uuid_primary_keys(self) -> None:
        for model in (NFCCardRegistry, NFCCardEvent):
            pk = list(model.__table__.primary_key.columns)[0]
            self.assertEqual(pk.type.python_type.__name__, "UUID")


if __name__ == "__main__":
    unittest.main()

"""Unit tests for provider-layer ORM model metadata."""

from __future__ import annotations

import unittest

from app.models.base import Base
from app.models.provider import (
    HospitalRegistry,
    ProviderCredential,
    ProviderHospitalAffiliation,
    ProviderIdentity,
)


class TestProviderModels(unittest.TestCase):
    def test_all_models_registered_on_base_metadata(self) -> None:
        table_names = {table.name for table in Base.metadata.sorted_tables}
        self.assertIn("hospital_registry", table_names)
        self.assertIn("provider_identity", table_names)
        self.assertIn("provider_hospital_affiliation", table_names)
        self.assertIn("provider_credential", table_names)

    def test_foreign_keys_and_uniques(self) -> None:
        affiliation_table = ProviderHospitalAffiliation.__table__
        credential_table = ProviderCredential.__table__

        affiliation_fks = {fk.target_fullname for fk in affiliation_table.foreign_keys}
        self.assertIn("provider_identity.id", affiliation_fks)
        self.assertIn("hospital_registry.id", affiliation_fks)

        credential_fks = {fk.target_fullname for fk in credential_table.foreign_keys}
        self.assertIn("provider_identity.id", credential_fks)

        unique_names = {
            constraint.name
            for constraint in affiliation_table.constraints
            if constraint.name
        }
        self.assertIn("uq_provider_hospital_affiliation", unique_names)

    def test_uuid_primary_keys(self) -> None:
        for model in (
            HospitalRegistry,
            ProviderIdentity,
            ProviderHospitalAffiliation,
            ProviderCredential,
        ):
            pk = list(model.__table__.primary_key.columns)[0]
            self.assertEqual(pk.type.python_type.__name__, "UUID")


if __name__ == "__main__":
    unittest.main()

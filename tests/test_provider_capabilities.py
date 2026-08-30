from app.security.provider_capabilities import (
    ClinicalCapability,
    capability_is_granted,
    capabilities_for_affiliation_roles,
)


def test_clinician_role_maps_only_to_fixed_server_owned_capabilities() -> None:
    capabilities = capabilities_for_affiliation_roles(["clinician"])
    assert ClinicalCapability.RECORD_READ in capabilities
    assert ClinicalCapability.EMERGENCY_ATTEMPT in capabilities
    assert all(isinstance(item, ClinicalCapability) for item in capabilities)


def test_organization_or_client_supplied_capability_never_grants_access() -> None:
    assert not capabilities_for_affiliation_roles(["organization_admin"])
    assert not capability_is_granted(
        ["organization_admin"], ClinicalCapability.RECORD_READ
    )
    assert not capability_is_granted(["clinician"], "record.read")  # type: ignore[arg-type]
    assert not capabilities_for_affiliation_roles("clinician")

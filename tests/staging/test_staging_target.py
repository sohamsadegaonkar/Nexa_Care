"""No production URL is accepted as staging smoke target."""
from __future__ import annotations

import pytest

from scripts.staging.check_target import validate_staging_target


@pytest.mark.parametrize("url", [
    "https://nexa-care.onrender.com",
    "http://nexa-care-staging.onrender.com",
    "https://localhost",
    "https://127.0.0.1",
    "https://nexa-care-staging.onrender.com:4443",
    "https://nexa-care-staging.onrender.com/?patient=1",
    "https://evil.example",
    "https://nexa-care-staging.onrender.com@evil.example",
])
def test_unsafe_staging_target_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        validate_staging_target(url)


def test_staging_host_is_explicitly_allowlisted() -> None:
    assert validate_staging_target("https://nexa-care-staging.onrender.com/") == (
        "https://nexa-care-staging.onrender.com"
    )


def test_azure_stage_accepts_its_external_web_host() -> None:
    url = "https://nexa-stg-web.test-central.centralindia.azurecontainerapps.io"
    assert validate_staging_target(url, azure_only=True) == url


@pytest.mark.parametrize("url", [
    "https://nexa-care-staging.onrender.com",
    "https://nexa-production.test-central.centralindia.azurecontainerapps.io",
    "https://nexa-stg-web.test-central.eastus.azurecontainerapps.io",
    "https://nexa-stg-api.internal.test-central.centralindia.azurecontainerapps.io",
    "https://nexa-stg-web.test-central.centralindia.azurecontainerapps.io.example.com",
])
def test_azure_gate_cannot_probe_other_targets(url: str) -> None:
    with pytest.raises(ValueError):
        validate_staging_target(url, azure_only=True)

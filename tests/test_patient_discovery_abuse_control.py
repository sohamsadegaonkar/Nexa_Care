"""Pure qualification for Slice 10A discovery anti-enumeration budgets."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from app.services.patient_discovery_abuse_control import (
    DiscoveryAbuseControlUnavailable,
    DiscoveryRateLimited,
    enforce_patient_discovery_budget,
)


@pytest.mark.asyncio
async def test_phone_budget_is_stricter_and_keys_have_no_patient_identifier() -> None:
    limiter = AsyncMock(
        side_effect=[
            (4, 51),
            (30, 3400),
            (20, 50),
            (180, 3300),
        ]
    )
    with patch(
        "app.services.patient_discovery_abuse_control.atomic_fixed_window",
        new=limiter,
    ):
        await enforce_patient_discovery_budget(
            object(),
            provider_id="provider-1",
            hospital_id="hospital-1",
            identifier_type="PHONE",
        )

    keys = [call.args[1] for call in limiter.await_args_list]
    assert keys == [
        "patient_discovery:budget:PHONE:60:provider-1:hospital-1",
        "patient_discovery:budget:PHONE:3600:provider-1:hospital-1",
        "patient_discovery:budget:ALL:60:provider-1:hospital-1",
        "patient_discovery:budget:ALL:3600:provider-1:hospital-1",
    ]
    signature = inspect.signature(enforce_patient_discovery_budget)
    assert "value" not in signature.parameters
    assert "phone" not in signature.parameters
    assert "identifier" not in signature.parameters


@pytest.mark.asyncio
async def test_phone_minute_budget_denies_fifth_probe() -> None:
    limiter = AsyncMock(
        side_effect=[
            (5, 41),
            (5, 3500),
            (5, 40),
            (5, 3400),
        ]
    )
    with (
        patch(
            "app.services.patient_discovery_abuse_control.atomic_fixed_window",
            new=limiter,
        ),
        pytest.raises(DiscoveryRateLimited) as exc_info,
    ):
        await enforce_patient_discovery_budget(
            object(),
            provider_id="provider-1",
            hospital_id="hospital-1",
            identifier_type="PHONE",
        )
    assert exc_info.value.retry_after_seconds == 41
    assert limiter.await_count == 4


@pytest.mark.asyncio
async def test_public_id_allows_twelve_per_minute_but_not_thirteen() -> None:
    allowed = AsyncMock(
        side_effect=[
            (12, 55),
            (12, 3550),
            (12, 54),
            (12, 3540),
        ]
    )
    with patch(
        "app.services.patient_discovery_abuse_control.atomic_fixed_window",
        new=allowed,
    ):
        await enforce_patient_discovery_budget(
            object(),
            provider_id="provider-1",
            hospital_id="hospital-1",
            identifier_type="NEXA_PUBLIC_ID",
        )

    denied = AsyncMock(
        side_effect=[
            (13, 33),
            (13, 3500),
            (13, 32),
            (13, 3400),
        ]
    )
    with (
        patch(
            "app.services.patient_discovery_abuse_control.atomic_fixed_window",
            new=denied,
        ),
        pytest.raises(DiscoveryRateLimited) as exc_info,
    ):
        await enforce_patient_discovery_budget(
            object(),
            provider_id="provider-1",
            hospital_id="hospital-1",
            identifier_type="NEXA_PUBLIC_ID",
        )
    assert exc_info.value.retry_after_seconds == 33


@pytest.mark.asyncio
async def test_global_budget_prevents_identifier_type_hopping() -> None:
    limiter = AsyncMock(
        side_effect=[
            (1, 59),
            (1, 3599),
            (21, 48),
            (21, 3500),
        ]
    )
    with (
        patch(
            "app.services.patient_discovery_abuse_control.atomic_fixed_window",
            new=limiter,
        ),
        pytest.raises(DiscoveryRateLimited) as exc_info,
    ):
        await enforce_patient_discovery_budget(
            object(),
            provider_id="provider-1",
            hospital_id="hospital-1",
            identifier_type="PHONE",
        )
    assert exc_info.value.retry_after_seconds == 48


@pytest.mark.asyncio
async def test_redis_failure_fails_closed_without_exposing_backend_error() -> None:
    limiter = AsyncMock(side_effect=RuntimeError("redis host diagnostics"))
    with (
        patch(
            "app.services.patient_discovery_abuse_control.atomic_fixed_window",
            new=limiter,
        ),
        pytest.raises(DiscoveryAbuseControlUnavailable) as exc_info,
    ):
        await enforce_patient_discovery_budget(
            object(),
            provider_id="provider-1",
            hospital_id="hospital-1",
            identifier_type="PHONE",
        )
    assert "diagnostics" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_unknown_identifier_type_and_missing_server_context_are_rejected() -> None:
    with pytest.raises(ValueError):
        await enforce_patient_discovery_budget(
            object(),
            provider_id="provider-1",
            hospital_id="hospital-1",
            identifier_type="NAME",
        )
    with pytest.raises(ValueError):
        await enforce_patient_discovery_budget(
            object(),
            provider_id="",
            hospital_id="hospital-1",
            identifier_type="PHONE",
        )

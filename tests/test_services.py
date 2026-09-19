"""Tests for multi-hub Evri services."""

import pytest
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.evri.const import (
    CONF_DIRECTION,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DIRECTION_OUTGOING,
    DOMAIN,
)
from custom_components.evri.services import async_setup_services, async_unload_services


def _hub(hass, postcode, parcels=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=postcode,
        options={CONF_POSTAL_CODE: postcode, CONF_PARCELS: parcels or []},
    )
    entry.add_to_hass(hass)
    return entry


async def test_single_hub_implicit_route_normalizes_and_moves(hass):
    entry = _hub(hass, "SW1A1AA")
    async_setup_services(hass)
    await hass.services.async_call(
        DOMAIN, "track_parcel", {CONF_TRACKING_CODE: "h00-aaa 001"}, blocking=True
    )
    assert entry.options[CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "H00AAA001", CONF_DIRECTION: "incoming"}
    ]
    await hass.services.async_call(
        DOMAIN,
        "track_parcel",
        {CONF_TRACKING_CODE: "H00AAA001", CONF_DIRECTION: DIRECTION_OUTGOING},
        blocking=True,
    )
    assert entry.options[CONF_PARCELS][0][CONF_DIRECTION] == DIRECTION_OUTGOING
    await hass.services.async_call(
        DOMAIN,
        "track_parcel",
        {CONF_TRACKING_CODE: "H00AAA001", CONF_DIRECTION: DIRECTION_OUTGOING},
        blocking=True,
    )
    assert len(entry.options[CONF_PARCELS]) == 1


async def test_multi_hub_requires_postcode_and_routes_explicitly(hass):
    first = _hub(hass, "SW1A1AA")
    second = _hub(hass, "EC1A1BB")
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError, match="Multiple Evri hubs"):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: "ABC"}, blocking=True
        )
    await hass.services.async_call(
        DOMAIN,
        "track_parcel",
        {CONF_TRACKING_CODE: "ABC", CONF_POSTAL_CODE: "ec1a 1bb"},
        blocking=True,
    )
    assert first.options[CONF_PARCELS] == []
    assert second.options[CONF_PARCELS][0][CONF_TRACKING_CODE] == "ABC"
    await hass.services.async_call(
        DOMAIN,
        "untrack_parcel",
        {CONF_TRACKING_CODE: "ABC", CONF_POSTAL_CODE: "EC1A1BB"},
        blocking=True,
    )
    assert second.options[CONF_PARCELS] == []


async def test_service_validation_and_lifecycle(hass):
    _hub(hass, "SW1A1AA")
    async_setup_services(hass)
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "track_parcel", {CONF_TRACKING_CODE: ""}, blocking=True
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "ABC", CONF_POSTAL_CODE: "bad"},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "track_parcel",
            {CONF_TRACKING_CODE: "ABC", CONF_POSTAL_CODE: "M11AE"},
            blocking=True,
        )
    async_unload_services(hass)
    assert not hass.services.has_service(DOMAIN, "track_parcel")

"""Tests for Evri postcode hubs and directional options."""

import json
from pathlib import Path

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.evri.config_flow import (
    display_postcode,
    normalize_postcode,
    normalize_tracking_code,
    valid_postcode,
    valid_tracking_code,
)
from custom_components.evri.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_DIRECTION,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DIRECTION_INCOMING,
    DIRECTION_OUTGOING,
    DOMAIN,
)


def test_normalizers_and_validation():
    assert normalize_postcode("sw1a 1aa") == "SW1A1AA"
    assert display_postcode("SW1A1AA") == "SW1A 1AA"
    assert valid_postcode("GIR 0AA")
    assert valid_postcode("EC1A 1BB")
    assert not valid_postcode("12345")
    assert normalize_tracking_code("h00-aaa 0001") == "H00AAA0001"
    assert valid_tracking_code("A")
    assert not valid_tracking_code("")


def test_manifest_allows_multiple_entries():
    manifest = json.loads(
        (Path(__file__).parents[1] / "custom_components/evri/manifest.json").read_text()
    )
    assert "single_config_entry" not in manifest


async def _create(hass, postcode):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POSTAL_CODE: postcode}
    )


async def test_multiple_hubs_and_duplicate_postcode(hass):
    first = await _create(hass, "sw1a 1aa")
    assert first["type"] == "create_entry"
    assert first["title"] == "Evri (SW1A 1AA)"
    assert first["options"][CONF_POSTAL_CODE] == "SW1A1AA"
    second = await _create(hass, "EC1A 1BB")
    assert second["type"] == "create_entry"
    duplicate = await _create(hass, "SW1A1AA")
    assert duplicate["type"] == "abort"
    assert duplicate["reason"] == "already_configured"


async def test_invalid_postcode_stays_on_form(hass):
    result = await _create(hass, "nope")
    assert result["type"] == "form"
    assert result["errors"][CONF_POSTAL_CODE] == "invalid_postcode"


def _hub(parcels=None):
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="SW1A1AA",
        options={CONF_POSTAL_CODE: "SW1A1AA", CONF_PARCELS: parcels or []},
    )


async def _step(hass, entry, name):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == [
        "incoming_parcels",
        "outgoing_parcels",
        "settings",
    ]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": name}
    )


async def test_direction_lists_move_and_preserve_other_direction(hass):
    entry = _hub([{CONF_TRACKING_CODE: "OLD", CONF_DIRECTION: DIRECTION_OUTGOING}])
    entry.add_to_hass(hass)
    result = await _step(hass, entry, "incoming_parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["old", "new-new"]}
    )
    assert result["data"][CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "OLD", CONF_DIRECTION: DIRECTION_INCOMING},
        {CONF_TRACKING_CODE: "NEWNEW", CONF_DIRECTION: DIRECTION_INCOMING},
    ]
    entry = _hub([{CONF_TRACKING_CODE: "IN", CONF_DIRECTION: DIRECTION_INCOMING}])
    entry.add_to_hass(hass)
    result = await _step(hass, entry, "outgoing_parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["OUT", "OUT"]}
    )
    assert result["data"][CONF_PARCELS] == [
        {CONF_TRACKING_CODE: "IN", CONF_DIRECTION: DIRECTION_INCOMING},
        {CONF_TRACKING_CODE: "OUT", CONF_DIRECTION: DIRECTION_OUTGOING},
    ]


async def test_settings_preserve_postcode(hass):
    entry = _hub()
    entry.add_to_hass(hass)
    result = await _step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DELIVERED_FILTER_TYPE: "parcels",
            CONF_DELIVERED_FILTER_AMOUNT: 5,
            CONF_INCLUDE_HISTORY: True,
        },
    )
    assert result["data"][CONF_POSTAL_CODE] == "SW1A1AA"
    assert result["data"][CONF_INCLUDE_HISTORY] is True

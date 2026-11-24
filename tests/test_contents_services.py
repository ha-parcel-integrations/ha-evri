"""Tests for the contents field, the update_contents action and its overlay."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.evri import async_remove_entry
from custom_components.evri.const import (
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from custom_components.evri.contents import CONTENTS_STORE_MAX_CODES, ContentsStore
from custom_components.evri.services import async_setup_services

from .payloads import ACTIVE_CODE, DELIVERED_CODE, active_sample, delivered_sample

OTHER_POSTCODE = "EC1A1BB"
FETCH = "custom_components.evri.api.EvriApiClient.async_get_parcel"


def _answer(code, postcode):
    if code == DELIVERED_CODE:
        return delivered_sample(code)
    return active_sample(code)


async def _setup(hass, postcode="SW1A1AA", codes=(ACTIVE_CODE,)):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=postcode,
        options={
            CONF_POSTAL_CODE: postcode,
            CONF_PARCELS: [{CONF_TRACKING_CODE: code} for code in codes],
        },
    )
    entry.add_to_hass(hass)
    with patch(FETCH, new=AsyncMock(side_effect=_answer)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _call(hass, service, data):
    return hass.services.async_call(DOMAIN, service, data, blocking=True)


async def test_set_replace_clear(hass):
    entry = await _setup(hass)
    coordinator = entry.runtime_data.coordinator
    assert coordinator.data[0]["contents"] is None
    with patch(FETCH, new=AsyncMock(side_effect=_answer)) as fetch:
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "  Running   shoes "},
        )
        assert coordinator.data[0]["contents"] == "Running shoes"
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: ACTIVE_CODE.lower(), "contents": "Books"},
        )
        assert coordinator.data[0]["contents"] == "Books"
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: ACTIVE_CODE})
        assert coordinator.data[0]["contents"] is None
        fetch.assert_not_called()


async def test_unchanged_value_writes_nothing(hass):
    entry = await _setup(hass)
    coordinator = entry.runtime_data.coordinator
    data = {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Books"}
    await _call(hass, "update_contents", data)
    with patch.object(coordinator, "async_apply_contents") as apply:
        await _call(hass, "update_contents", data)
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: "NOPE"})
    apply.assert_not_called()


async def test_untracked_code_can_be_set(hass):
    entry = await _setup(hass)
    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: "OTHER99", "contents": "Lamp"}
    )
    assert entry.runtime_data.coordinator.contents_store.get("other99") == "Lamp"


async def test_overlay_survives_poll_and_reaches_delivered(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SW1A1AA",
        options={
            CONF_POSTAL_CODE: "SW1A1AA",
            CONF_PARCELS: [
                {CONF_TRACKING_CODE: ACTIVE_CODE},
                {CONF_TRACKING_CODE: DELIVERED_CODE},
            ],
            "delivered_filter_type": "parcels",
            "delivered_filter_amount": 5,
        },
    )
    entry.add_to_hass(hass)
    with patch(FETCH, new=AsyncMock(side_effect=_answer)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: DELIVERED_CODE, "contents": "Mug"},
    )
    assert coordinator.delivered[0]["contents"] == "Mug"
    with patch(FETCH, new=AsyncMock(side_effect=_answer)):
        await coordinator.async_refresh()
    assert coordinator.delivered[0]["contents"] == "Mug"
    assert coordinator.data[0]["contents"] is None


async def test_track_parcel_sets_contents(hass):
    entry = await _setup(hass, codes=())
    with patch(FETCH, new=AsyncMock(side_effect=_answer)):
        await _call(
            hass,
            "track_parcel",
            {CONF_TRACKING_CODE: "h00aaa0000000001", "contents": "Shoes"},
        )
        await hass.async_block_till_done()
    coordinator = entry.runtime_data.coordinator
    assert coordinator.data[0]["contents"] == "Shoes"
    # Already tracked: a changed value is laid over without a fetch.
    with patch(FETCH, new=AsyncMock(side_effect=_answer)) as fetch:
        await _call(
            hass,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Boots"},
        )
        await _call(
            hass,
            "track_parcel",
            {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Boots"},
        )
        fetch.assert_not_called()
    assert coordinator.data[0]["contents"] == "Boots"


async def test_untrack_drops_the_value(hass):
    entry = await _setup(hass)
    store = entry.runtime_data.coordinator.contents_store
    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Hat"}
    )
    with patch(FETCH, new=AsyncMock(side_effect=_answer)):
        await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})
    assert store.get(ACTIVE_CODE) is None


async def test_untrack_on_unloaded_entry_still_works(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SW1A1AA",
        options={
            CONF_POSTAL_CODE: "SW1A1AA",
            CONF_PARCELS: [{CONF_TRACKING_CODE: ACTIVE_CODE}],
        },
    )
    entry.add_to_hass(hass)
    async_setup_services(hass)
    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: ACTIVE_CODE})
    assert entry.options[CONF_PARCELS] == []
    with pytest.raises(ServiceValidationError):
        await _call(hass, "track_parcel", {CONF_TRACKING_CODE: "X1", "contents": "Hat"})


async def test_cap_drops_the_oldest(hass):
    entry = await _setup(hass)
    store = entry.runtime_data.coordinator.contents_store
    for index in range(CONTENTS_STORE_MAX_CODES + 1):
        store.async_set(f"CODE{index}", "Item")
    assert store.get("CODE0") is None
    assert store.get("CODE1") == "Item"
    assert store.get(f"CODE{CONTENTS_STORE_MAX_CODES}") == "Item"


async def test_routing_single_entry_targets_it_directly(hass):
    entry = await _setup(hass)
    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: "ELSEWHERE", "contents": "Pen"}
    )
    assert entry.runtime_data.coordinator.contents_store.get("ELSEWHERE") == "Pen"


async def test_routing_updates_every_entry_listing_the_code(hass):
    first = await _setup(hass, "SW1A1AA", (ACTIVE_CODE, DELIVERED_CODE))
    second = await _setup(hass, OTHER_POSTCODE, (ACTIVE_CODE,))
    third = await _setup(hass, "M11AA", ("H00AAA0000000009",))
    await _call(
        hass, "update_contents", {CONF_TRACKING_CODE: ACTIVE_CODE, "contents": "Rug"}
    )
    stores = [e.runtime_data.coordinator.contents_store for e in (first, second, third)]
    assert [s.get(ACTIVE_CODE) for s in stores] == ["Rug", "Rug", None]
    assert first.runtime_data.coordinator.data[0]["contents"] == "Rug"


async def test_routing_without_a_match_needs_an_entry(hass):
    await _setup(hass, "SW1A1AA")
    await _setup(hass, OTHER_POSTCODE)
    with pytest.raises(ServiceValidationError) as err:
        await _call(
            hass, "update_contents", {CONF_TRACKING_CODE: "NOPE", "contents": "x"}
        )
    assert err.value.translation_key == "update_contents_entry_required"


async def test_routing_explicit_entry_id(hass):
    first = await _setup(hass, "SW1A1AA")
    second = await _setup(hass, OTHER_POSTCODE)
    await _call(
        hass,
        "update_contents",
        {
            CONF_TRACKING_CODE: "NOPE",
            "contents": "Vase",
            "config_entry_id": second.entry_id,
        },
    )
    assert second.runtime_data.coordinator.contents_store.get("NOPE") == "Vase"
    assert first.runtime_data.coordinator.contents_store.get("NOPE") is None
    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            "update_contents",
            {CONF_TRACKING_CODE: "NOPE", "config_entry_id": "missing"},
        )


async def test_update_contents_errors(hass):
    async_setup_services(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: "A1"})
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await _call(hass, "update_contents", {CONF_TRACKING_CODE: " "})


async def test_entry_without_a_store_file_works_and_remove_deletes_it(hass):
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.coordinator.data[0]["contents"] is None
    with patch.object(ContentsStore, "async_remove", AsyncMock()) as remove:
        await async_remove_entry(hass, entry)
    remove.assert_awaited_once()

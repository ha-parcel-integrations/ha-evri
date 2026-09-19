"""Tests for Evri fetching, buckets, cache and events."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.evri.api import EvriApiError, EvriCredentialError
from custom_components.evri.const import (
    CONF_DIRECTION,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DIRECTION_OUTGOING,
    DOMAIN,
    ParcelStatus,
)
from custom_components.evri.coordinator import EvriCoordinator

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    POSTCODE,
    active_sample,
    delivered_sample,
)


def _entry(parcels):
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=POSTCODE,
        options={
            CONF_POSTAL_CODE: POSTCODE,
            CONF_PARCELS: parcels,
            "delivered_filter_type": "parcels",
            "delivered_filter_amount": 100,
        },
    )


async def test_fetch_splits_directions_and_delivered(hass):
    entry = _entry(
        [
            {CONF_TRACKING_CODE: ACTIVE_CODE},
            {CONF_TRACKING_CODE: DELIVERED_CODE, CONF_DIRECTION: DIRECTION_OUTGOING},
        ]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = lambda code, postcode: (
        active_sample(code) if code == ACTIVE_CODE else delivered_sample(code)
    )
    coordinator = EvriCoordinator(hass, client, entry)
    data = await coordinator._async_update_data()
    assert [p["barcode"] for p in data] == [ACTIVE_CODE]
    assert coordinator.outgoing == []
    assert [p["barcode"] for p in coordinator.delivered_outgoing] == [DELIVERED_CODE]
    assert coordinator.delivered_codes == {DELIVERED_CODE}
    assert all(
        call.args[1] == POSTCODE for call in client.async_get_parcel.await_args_list
    )


async def test_outgoing_active_and_pending_placeholder(hass):
    entry = _entry(
        [{CONF_TRACKING_CODE: ACTIVE_CODE, CONF_DIRECTION: DIRECTION_OUTGOING}]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = None
    coordinator = EvriCoordinator(hass, client, entry)
    assert await coordinator._async_update_data() == []
    assert coordinator.outgoing[0]["barcode"] == ACTIVE_CODE
    assert coordinator.outgoing[0]["status"] is ParcelStatus.UNKNOWN


async def test_cache_reused_and_pruned(hass):
    entry = _entry([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = MagicMock()
    client.async_get_parcel = AsyncMock(return_value=active_sample())
    coordinator = EvriCoordinator(hass, client, entry)
    coordinator._raw_cache["GONE"] = active_sample("GONE")
    await coordinator._async_update_data()
    client.async_get_parcel.side_effect = EvriApiError("down")
    data = await coordinator._async_update_data()
    assert data[0]["barcode"] == ACTIVE_CODE
    assert "GONE" not in coordinator._raw_cache


async def test_all_fail_and_credential_fail_whole_poll(hass):
    entry = _entry([{CONF_TRACKING_CODE: ACTIVE_CODE}, {CONF_TRACKING_CODE: "SECOND"}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = EvriApiError("down")
    with pytest.raises(UpdateFailed):
        await EvriCoordinator(hass, client, entry)._async_update_data()
    client.async_get_parcel.reset_mock()
    client.async_get_parcel.side_effect = EvriCredentialError("rotated")
    with pytest.raises(UpdateFailed, match="public tracking credential"):
        await EvriCoordinator(hass, client, entry)._async_update_data()
    assert client.async_get_parcel.await_count == 1


async def test_rate_limit_backoff(hass):
    entry = _entry([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = EvriApiError(
        "limited", status_code=429, retry_after=20
    )
    with pytest.raises(UpdateFailed) as exc:
        await EvriCoordinator(hass, client, entry)._async_update_data()
    assert exc.value.retry_after == 20


async def test_events_are_suppressed_then_split(hass):
    entry = _entry(
        [
            {CONF_TRACKING_CODE: ACTIVE_CODE},
            {CONF_TRACKING_CODE: "SENT", CONF_DIRECTION: DIRECTION_OUTGOING},
        ]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = lambda code, postcode: active_sample(
        code, "2"
    )
    coordinator = EvriCoordinator(hass, client, entry)
    incoming_events = []
    outgoing_events = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_status_changed", incoming_events.append)
    hass.bus.async_listen(
        f"{DOMAIN}_outgoing_parcel_status_changed", outgoing_events.append
    )
    await coordinator._async_update_data()
    assert not incoming_events and not outgoing_events
    client.async_get_parcel.side_effect = lambda code, postcode: active_sample(
        code, "4_COURIER"
    )
    await coordinator._async_update_data()
    await hass.async_block_till_done()
    assert len(incoming_events) == 1
    assert len(outgoing_events) == 1


async def test_delivered_is_skipped_next_poll(hass):
    entry = _entry([{CONF_TRACKING_CODE: DELIVERED_CODE}])
    entry.add_to_hass(hass)
    client = MagicMock()
    client.async_get_parcel = AsyncMock(return_value=delivered_sample())
    coordinator = EvriCoordinator(hass, client, entry)
    await coordinator._async_update_data()
    client.async_get_parcel.reset_mock()
    await coordinator._async_update_data()
    client.async_get_parcel.assert_not_awaited()


async def test_registered_delivered_and_delivery_time_events(hass):
    entry = _entry([{CONF_TRACKING_CODE: ACTIVE_CODE}])
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.side_effect = lambda code, postcode: active_sample(
        code, "2"
    )
    coordinator = EvriCoordinator(hass, client, entry)
    await coordinator._async_update_data()

    registered = []
    delivered = []
    eta_changed = []
    hass.bus.async_listen(f"{DOMAIN}_parcel_registered", registered.append)
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivered", delivered.append)
    hass.bus.async_listen(f"{DOMAIN}_parcel_delivery_time_changed", eta_changed.append)

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_PARCELS: [
                {CONF_TRACKING_CODE: ACTIVE_CODE},
                {CONF_TRACKING_CODE: "NEWCODE"},
            ],
        },
    )
    await coordinator._async_update_data()
    await hass.async_block_till_done()
    assert len(registered) == 1

    client.async_get_parcel.side_effect = lambda code, postcode: delivered_sample(code)
    await coordinator._async_update_data()
    await hass.async_block_till_done()
    assert len(delivered) == 2

    normalized = coordinator.delivered[0]
    normalized = {**normalized, "planned_from": "2026-09-19T10:00:00Z"}
    coordinator._known_state = {normalized["barcode"]: normalized["status"]}
    coordinator._known_delivery_times = {normalized["barcode"]: (None, None)}
    coordinator._fire_change_events([normalized])
    await hass.async_block_till_done()
    assert len(eta_changed) == 1


async def test_outgoing_delivered_event(hass):
    entry = _entry(
        [{CONF_TRACKING_CODE: ACTIVE_CODE, CONF_DIRECTION: DIRECTION_OUTGOING}]
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    client.async_get_parcel.return_value = active_sample(ACTIVE_CODE, "2")
    coordinator = EvriCoordinator(hass, client, entry)
    await coordinator._async_update_data()
    events = []
    hass.bus.async_listen(f"{DOMAIN}_outgoing_parcel_delivered", events.append)
    client.async_get_parcel.return_value = delivered_sample(ACTIVE_CODE)
    await coordinator._async_update_data()
    await hass.async_block_till_done()
    assert len(events) == 1

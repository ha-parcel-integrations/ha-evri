"""Tests for Evri device triggers."""

from unittest.mock import AsyncMock, patch

from custom_components.evri.const import DOMAIN
from custom_components.evri.device_trigger import (
    TRIGGER_EVENTS,
    async_attach_trigger,
    async_get_triggers,
)


async def test_get_triggers_returns_all_six(hass):
    triggers = await async_get_triggers(hass, "device123")
    types = {t["type"] for t in triggers}
    assert types == {
        "parcel_registered",
        "parcel_status_changed",
        "parcel_delivered",
        "parcel_delivery_time_changed",
        "outgoing_parcel_status_changed",
        "outgoing_parcel_delivered",
    }
    for trigger in triggers:
        assert trigger["domain"] == DOMAIN
        assert trigger["device_id"] == "device123"


def test_trigger_events_map_to_domain_prefix():
    assert TRIGGER_EVENTS["parcel_registered"] == f"{DOMAIN}_parcel_registered"


async def test_attach_trigger_delegates_to_event_trigger(hass):
    action = AsyncMock()
    with patch(
        "custom_components.evri.device_trigger.event_trigger.async_attach_trigger",
        new=AsyncMock(return_value="remove"),
    ) as attach:
        result = await async_attach_trigger(
            hass,
            {"type": "parcel_delivered", "device_id": "device123"},
            action,
            {},
        )
    assert result == "remove"
    event_config = attach.await_args.args[1]
    assert f"{DOMAIN}_parcel_delivered" in str(event_config["event_type"])
    assert "device123" in str(event_config["event_data"])

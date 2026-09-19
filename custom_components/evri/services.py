"""Track and untrack services shared by all Evri postcode hubs."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .config_flow import (
    normalize_postcode,
    normalize_tracking_code,
    valid_postcode,
    valid_tracking_code,
)
from .const import (
    CONF_DIRECTION,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DEFAULT_DIRECTION,
    DIRECTION_INCOMING,
    DIRECTION_OUTGOING,
    DOMAIN,
)
from .parcels import tracked_direction

SERVICE_TRACK_PARCEL = "track_parcel"
SERVICE_UNTRACK_PARCEL = "untrack_parcel"

_TRACK_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_CODE): cv.string,
        vol.Optional(CONF_POSTAL_CODE): cv.string,
        vol.Optional(CONF_DIRECTION, default=DEFAULT_DIRECTION): vol.In(
            [DIRECTION_INCOMING, DIRECTION_OUTGOING]
        ),
    }
)
_UNTRACK_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_CODE): cv.string,
        vol.Optional(CONF_POSTAL_CODE): cv.string,
    }
)


def _resolve_entry(hass: HomeAssistant, postcode: str | None):
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("Evri is not set up")
    if postcode:
        target = normalize_postcode(postcode)
        if not valid_postcode(target):
            raise ServiceValidationError(f"'{postcode}' is not a valid UK postcode")
        for entry in entries:
            if entry.options.get(CONF_POSTAL_CODE) == target:
                return entry
        raise ServiceValidationError(f"No Evri hub for postcode {postcode}")
    if len(entries) == 1:
        return entries[0]
    raise ServiceValidationError(
        "Multiple Evri hubs are set up — pass postcode to choose one"
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the shared Evri services idempotently."""
    if hass.services.has_service(DOMAIN, SERVICE_TRACK_PARCEL):
        return

    async def _track(call: ServiceCall) -> None:
        code = normalize_tracking_code(call.data[CONF_TRACKING_CODE])
        if not valid_tracking_code(code):
            raise ServiceValidationError("The Evri tracking code cannot be empty")
        entry = _resolve_entry(hass, call.data.get(CONF_POSTAL_CODE))
        direction = call.data[CONF_DIRECTION]
        parcels = [dict(parcel) for parcel in entry.options.get(CONF_PARCELS, [])]
        existing = next(
            (parcel for parcel in parcels if parcel.get(CONF_TRACKING_CODE) == code),
            None,
        )
        if existing is not None:
            if tracked_direction(existing) == direction:
                return
            existing[CONF_DIRECTION] = direction
        else:
            parcels.append({CONF_TRACKING_CODE: code, CONF_DIRECTION: direction})
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_PARCELS: parcels}
        )

    async def _untrack(call: ServiceCall) -> None:
        code = normalize_tracking_code(call.data[CONF_TRACKING_CODE])
        entry = _resolve_entry(hass, call.data.get(CONF_POSTAL_CODE))
        current = entry.options.get(CONF_PARCELS, [])
        kept = [parcel for parcel in current if parcel.get(CONF_TRACKING_CODE) != code]
        if len(kept) != len(current):
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, CONF_PARCELS: kept}
            )

    hass.services.async_register(
        DOMAIN, SERVICE_TRACK_PARCEL, _track, schema=_TRACK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UNTRACK_PARCEL, _untrack, schema=_UNTRACK_SCHEMA
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the shared Evri services."""
    for service in (SERVICE_TRACK_PARCEL, SERVICE_UNTRACK_PARCEL):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)

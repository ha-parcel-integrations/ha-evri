"""Track, untrack and contents services shared by all Evri postcode hubs."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .config_flow import (
    normalize_postcode,
    normalize_tracking_code,
    valid_postcode,
    valid_tracking_code,
)
from .const import (
    CONF_CONFIG_ENTRY_ID,
    CONF_CONTENTS,
    CONF_DIRECTION,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DEFAULT_DIRECTION,
    DIRECTION_INCOMING,
    DIRECTION_OUTGOING,
    DOMAIN,
)
from .contents import normalize_code
from .parcels import tracked_direction

SERVICE_TRACK_PARCEL = "track_parcel"
SERVICE_UNTRACK_PARCEL = "untrack_parcel"
SERVICE_UPDATE_CONTENTS = "update_contents"

_TRACK_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_CODE): cv.string,
        vol.Optional(CONF_POSTAL_CODE): cv.string,
        vol.Optional(CONF_DIRECTION, default=DEFAULT_DIRECTION): vol.In(
            [DIRECTION_INCOMING, DIRECTION_OUTGOING]
        ),
        vol.Optional(CONF_CONTENTS): cv.string,
    }
)
_UPDATE_CONTENTS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_TRACKING_CODE): cv.string,
        vol.Optional(CONF_CONTENTS): cv.string,
        vol.Optional(CONF_CONFIG_ENTRY_ID): cv.string,
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


def _loaded_coordinator(entry):
    """Return the coordinator of a set-up entry, or raise when it is not loaded."""
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError("Evri is not loaded")
    return entry.runtime_data.coordinator


def _contents_store(entry):
    """Return the entry's contents store, or ``None`` when it is not loaded."""
    if entry.state is not ConfigEntryState.LOADED:
        return None
    return entry.runtime_data.coordinator.contents_store


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
        coordinator = _loaded_coordinator(entry) if CONF_CONTENTS in call.data else None
        contents_changed = False
        if coordinator is not None:
            contents_changed = coordinator.contents_store.async_set(
                code, call.data[CONF_CONTENTS]
            )
        parcels = [dict(parcel) for parcel in entry.options.get(CONF_PARCELS, [])]
        existing = next(
            (parcel for parcel in parcels if parcel.get(CONF_TRACKING_CODE) == code),
            None,
        )
        if existing is not None:
            if tracked_direction(existing) == direction:
                if coordinator is not None and contents_changed:
                    coordinator.async_apply_contents()
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
        if (store := _contents_store(entry)) is not None:
            store.async_set(code, None)
        current = entry.options.get(CONF_PARCELS, [])
        kept = [parcel for parcel in current if parcel.get(CONF_TRACKING_CODE) != code]
        if len(kept) != len(current):
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, CONF_PARCELS: kept}
            )

    async def _update_contents(call: ServiceCall) -> None:
        code = normalize_code(call.data[CONF_TRACKING_CODE])
        if not code:
            raise ServiceValidationError("A tracking code is required")
        loaded = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]
        if not loaded:
            raise ServiceValidationError("Evri is not set up")
        if entry_id := call.data.get(CONF_CONFIG_ENTRY_ID):
            targets = [entry for entry in loaded if entry.entry_id == entry_id]
            if not targets:
                raise ServiceValidationError(f"'{entry_id}' is not a loaded Evri hub")
        elif len(loaded) == 1:
            targets = loaded
        else:
            # A code can sit in two hubs, so every hub that lists it gets the
            # value; guessing a hub would write to the wrong one.
            targets = [
                entry
                for entry in loaded
                if code in entry.runtime_data.coordinator.known_codes
            ]
            if not targets:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="update_contents_entry_required",
                )
        for entry in targets:
            coordinator = entry.runtime_data.coordinator
            if coordinator.contents_store.async_set(code, call.data.get(CONF_CONTENTS)):
                coordinator.async_apply_contents()

    hass.services.async_register(
        DOMAIN, SERVICE_TRACK_PARCEL, _track, schema=_TRACK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UNTRACK_PARCEL, _untrack, schema=_UNTRACK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_CONTENTS,
        _update_contents,
        schema=_UPDATE_CONTENTS_SCHEMA,
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the shared Evri services."""
    for service in (
        SERVICE_TRACK_PARCEL,
        SERVICE_UNTRACK_PARCEL,
        SERVICE_UPDATE_CONTENTS,
    ):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)

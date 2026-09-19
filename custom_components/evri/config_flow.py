"""Config and options flows for postcode-keyed Evri hubs."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_DIRECTION,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_POSTAL_CODE,
    CONF_TRACKING_CODE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DIRECTION_INCOMING,
    DIRECTION_OUTGOING,
    DOMAIN,
)
from .parcels import tracked_direction

_POSTCODE_RE = re.compile(
    r"^(?:GIR0AA|(?:[A-PR-UWYZ][0-9][0-9]?|[A-PR-UWYZ][A-HK-Y][0-9][0-9]?|[A-PR-UWYZ][0-9][A-HJKSTUW]|[A-PR-UWYZ][A-HK-Y][0-9][ABEHMNPRVWXY])[0-9][ABD-HJLNP-UW-Z]{2})$"
)


def normalize_postcode(value: str) -> str:
    """Return an uppercase UK postcode without whitespace."""
    return re.sub(r"\s+", "", (value or "").upper())


def display_postcode(value: str) -> str:
    """Format a compact postcode with its standard inward-code space."""
    compact = normalize_postcode(value)
    return f"{compact[:-3]} {compact[-3:]}" if len(compact) > 3 else compact


def valid_postcode(value: str) -> bool:
    """Return whether a value has a valid UK postcode shape."""
    return bool(_POSTCODE_RE.fullmatch(normalize_postcode(value)))


def normalize_tracking_code(value: str) -> str:
    """Uppercase a code and discard non-alphanumeric separators."""
    return re.sub(r"[^A-Z0-9]+", "", (value or "").upper())


def valid_tracking_code(value: str) -> bool:
    """Accept any non-empty normalized tracking code."""
    return bool(value)


def _current_parcels(entry: ConfigEntry) -> list[dict[str, str]]:
    return [dict(item) for item in entry.options.get(CONF_PARCELS, [])]


class EvriConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create one Evri hub per UK delivery postcode."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EvriOptionsFlowHandler:
        """Return the Evri options flow."""
        return EvriOptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the immutable delivery postcode for a hub."""
        errors: dict[str, str] = {}
        if user_input is not None:
            postcode = normalize_postcode(user_input[CONF_POSTAL_CODE])
            if not valid_postcode(postcode):
                errors[CONF_POSTAL_CODE] = "invalid_postcode"
            else:
                await self.async_set_unique_id(postcode)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Evri ({display_postcode(postcode)})",
                    data={},
                    options={
                        CONF_POSTAL_CODE: postcode,
                        CONF_PARCELS: [],
                        CONF_DELIVERED_FILTER_TYPE: DEFAULT_DELIVERED_FILTER_TYPE,
                        CONF_DELIVERED_FILTER_AMOUNT: DEFAULT_DELIVERED_FILTER_AMOUNT,
                        CONF_INCLUDE_HISTORY: DEFAULT_INCLUDE_HISTORY,
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_POSTAL_CODE): str}),
            errors=errors,
        )


class EvriOptionsFlowHandler(OptionsFlow):
    """Edit incoming/outgoing parcel lists and display settings."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show parcel direction lists and settings."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["incoming_parcels", "outgoing_parcels", "settings"],
        )

    async def async_step_incoming_parcels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit incoming tracking codes."""
        return await self._async_step_parcel_list(DIRECTION_INCOMING, user_input)

    async def async_step_outgoing_parcels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit outgoing tracking codes."""
        return await self._async_step_parcel_list(DIRECTION_OUTGOING, user_input)

    async def _async_step_parcel_list(
        self, direction: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        step_id = f"{direction}_parcels"
        errors: dict[str, str] = {}
        if user_input is not None:
            codes = list(
                dict.fromkeys(
                    normalize_tracking_code(code)
                    for code in user_input.get("tracking_codes", [])
                    if normalize_tracking_code(code)
                )
            )
            if any(not valid_tracking_code(code) for code in codes):
                errors["base"] = "invalid_tracking_code"
            else:
                kept = [
                    parcel
                    for parcel in _current_parcels(self.config_entry)
                    if tracked_direction(parcel) != direction
                    and parcel.get(CONF_TRACKING_CODE) not in codes
                ]
                return self.async_create_entry(
                    title="",
                    data={
                        **self.config_entry.options,
                        CONF_PARCELS: kept
                        + [
                            {CONF_TRACKING_CODE: code, CONF_DIRECTION: direction}
                            for code in codes
                        ],
                    },
                )
        current = [
            parcel[CONF_TRACKING_CODE]
            for parcel in _current_parcels(self.config_entry)
            if tracked_direction(parcel) == direction
        ]
        schema = vol.Schema(
            {
                vol.Optional("tracking_codes"): selector.TextSelector(
                    selector.TextSelectorConfig(multiple=True)
                )
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(
                schema, {"tracking_codes": current}
            ),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit retention and history settings."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    **self.config_entry.options,
                    CONF_DELIVERED_FILTER_TYPE: user_input[CONF_DELIVERED_FILTER_TYPE],
                    CONF_DELIVERED_FILTER_AMOUNT: int(
                        user_input[CONF_DELIVERED_FILTER_AMOUNT]
                    ),
                    CONF_INCLUDE_HISTORY: bool(user_input[CONF_INCLUDE_HISTORY]),
                },
            )
        current = self.config_entry.options
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DELIVERED_FILTER_TYPE,
                        default=current.get(
                            CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
                        ),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["days", "parcels"],
                            translation_key=CONF_DELIVERED_FILTER_TYPE,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Required(
                        CONF_DELIVERED_FILTER_AMOUNT,
                        default=current.get(
                            CONF_DELIVERED_FILTER_AMOUNT,
                            DEFAULT_DELIVERED_FILTER_AMOUNT,
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1, max=365, step=1, mode=selector.NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_INCLUDE_HISTORY,
                        default=current.get(
                            CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
                        ),
                    ): selector.BooleanSelector(),
                }
            ),
        )

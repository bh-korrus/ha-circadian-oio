"""Config flow for Circadian OIO."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_WRAPPED_DEVICES,
    DOMAIN,
    KORRUS_MANUFACTURER_MATCHES,
)


def _is_oio_manufacturer(manufacturer: str | None) -> bool:
    if not manufacturer:
        return False
    m = manufacturer.lower()
    return any(needle in m for needle in KORRUS_MANUFACTURER_MATCHES)


def _discover_oio_devices(hass) -> dict[str, str]:
    """Return {device_id: label} for devices that appear to be OIO bulbs."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    result: dict[str, str] = {}
    for device in dev_reg.devices.values():
        if not _is_oio_manufacturer(device.manufacturer):
            continue
        # Only wrap devices that actually have a light entity.
        has_light = any(
            ent.entity_id.startswith("light.")
            for ent in er.async_entries_for_device(
                ent_reg, device.id, include_disabled_entities=True
            )
        )
        if not has_light:
            continue
        label = device.name_by_user or device.name or device.id
        result[device.id] = label
    return result


class CircadianOIOConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Circadian OIO."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """First step: pick which OIO devices to wrap."""
        # Allow only one config entry; the user can edit selection in Options later.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        devices = _discover_oio_devices(self.hass)
        if not devices:
            return self.async_abort(reason="no_oio_devices")

        if user_input is not None:
            return self.async_create_entry(
                title="Circadian OIO",
                data={CONF_WRAPPED_DEVICES: user_input[CONF_WRAPPED_DEVICES]},
            )

        options = [
            SelectOptionDict(value=did, label=label)
            for did, label in devices.items()
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_WRAPPED_DEVICES, default=list(devices.keys())
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> CircadianOIOOptionsFlow:
        return CircadianOIOOptionsFlow(config_entry)


class CircadianOIOOptionsFlow(config_entries.OptionsFlow):
    """Allow the user to change which devices are wrapped."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        devices = _discover_oio_devices(self.hass)
        current = self.entry.data.get(CONF_WRAPPED_DEVICES, [])

        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_WRAPPED_DEVICES: user_input[CONF_WRAPPED_DEVICES]},
            )
            return self.async_create_entry(title="", data={})

        options = [
            SelectOptionDict(value=did, label=label)
            for did, label in devices.items()
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_WRAPPED_DEVICES, default=current
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

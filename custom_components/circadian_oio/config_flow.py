"""Config flow for Circadian OIO."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TimeSelector,
)

from .const import (
    CONF_DAY_MAX_CCT,
    CONF_MIN_BRIGHTNESS,
    CONF_MIN_CCT,
    CONF_NIGHT_BRIGHTNESS_PCT,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_TRANSITION_MINUTES,
    CONF_WRAPPED_DEVICES,
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START,
    DOMAIN,
    KORRUS_MANUFACTURER_MATCHES,
    LATE_NIGHT_MAX_B_PCT,
    MAX_CCT_DAY,
    MIN_BRIGHTNESS,
    MIN_CCT,
    NINEPM_TRANSITION_LEAD_MIN,
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
        opts = self.entry.options

        if user_input is not None:
            # Bulb selection lives in entry.data; the render tuning lives in
            # entry.options. Both changes reload the entry via the listener.
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={
                    **self.entry.data,
                    CONF_WRAPPED_DEVICES: user_input[CONF_WRAPPED_DEVICES],
                },
            )
            tuning = {
                k: v for k, v in user_input.items() if k != CONF_WRAPPED_DEVICES
            }
            return self.async_create_entry(title="", data=tuning)

        device_options = [
            SelectOptionDict(value=did, label=label)
            for did, label in devices.items()
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_WRAPPED_DEVICES, default=current
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=device_options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_NIGHT_START,
                    default=opts.get(CONF_NIGHT_START, DEFAULT_NIGHT_START),
                ): TimeSelector(),
                vol.Required(
                    CONF_NIGHT_END,
                    default=opts.get(CONF_NIGHT_END, DEFAULT_NIGHT_END),
                ): TimeSelector(),
                vol.Required(
                    CONF_TRANSITION_MINUTES,
                    default=opts.get(
                        CONF_TRANSITION_MINUTES, NINEPM_TRANSITION_LEAD_MIN
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=120,
                        step=1,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_NIGHT_BRIGHTNESS_PCT,
                    default=opts.get(
                        CONF_NIGHT_BRIGHTNESS_PCT, LATE_NIGHT_MAX_B_PCT
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=100,
                        step=1,
                        unit_of_measurement="%",
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Required(
                    CONF_DAY_MAX_CCT,
                    default=opts.get(CONF_DAY_MAX_CCT, MAX_CCT_DAY),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=2700,
                        max=6500,
                        step=50,
                        unit_of_measurement="K",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_MIN_BRIGHTNESS,
                    default=opts.get(CONF_MIN_BRIGHTNESS, MIN_BRIGHTNESS),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=128,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_MIN_CCT,
                    default=opts.get(CONF_MIN_CCT, MIN_CCT),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=800,
                        max=2700,
                        step=50,
                        unit_of_measurement="K",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

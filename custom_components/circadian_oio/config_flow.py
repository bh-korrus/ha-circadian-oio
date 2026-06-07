"""Config flow for Circadian OIO."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
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
    CONF_DAY_BASE_CCT,
    CONF_DAY_MAX_CCT,
    CONF_MIN_BRIGHTNESS,
    CONF_MIN_CCT,
    CONF_NIGHT_BRIGHTNESS_PCT,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_OVERRIDES,
    CONF_TRANSITION_MINUTES,
    CONF_WRAPPED_DEVICES,
    DAY_BASE_CCT,
    DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START,
    DOMAIN,
    KORRUS_MANUFACTURER_MATCHES,
    LATE_NIGHT_MAX_B_PCT,
    MAX_CCT_DAY,
    MIN_BRIGHTNESS,
    MIN_CCT,
    NINEPM_TRANSITION_LEAD_MIN,
    TUNABLE_KEYS,
)

# Default value per tunable key, used when neither a per-bulb override nor a
# global option is set.
_TUNABLE_DEFAULTS = {
    CONF_NIGHT_START: DEFAULT_NIGHT_START,
    CONF_NIGHT_END: DEFAULT_NIGHT_END,
    CONF_TRANSITION_MINUTES: NINEPM_TRANSITION_LEAD_MIN,
    CONF_NIGHT_BRIGHTNESS_PCT: LATE_NIGHT_MAX_B_PCT,
    CONF_DAY_MAX_CCT: MAX_CCT_DAY,
    CONF_DAY_BASE_CCT: DAY_BASE_CCT,
    CONF_MIN_BRIGHTNESS: MIN_BRIGHTNESS,
    CONF_MIN_CCT: MIN_CCT,
}


def _tuning_fields(values: dict[str, Any]) -> dict:
    """Build the seven tunable form fields, defaulting from `values`.

    Used for both the global defaults form and each per-bulb override form.
    """

    def d(key):
        return values.get(key, _TUNABLE_DEFAULTS[key])

    return {
        vol.Required(CONF_NIGHT_START, default=d(CONF_NIGHT_START)): TimeSelector(),
        vol.Required(CONF_NIGHT_END, default=d(CONF_NIGHT_END)): TimeSelector(),
        vol.Required(
            CONF_TRANSITION_MINUTES, default=d(CONF_TRANSITION_MINUTES)
        ): NumberSelector(
            NumberSelectorConfig(
                min=0, max=120, step=1, unit_of_measurement="min",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(
            CONF_NIGHT_BRIGHTNESS_PCT, default=d(CONF_NIGHT_BRIGHTNESS_PCT)
        ): NumberSelector(
            NumberSelectorConfig(
                min=1, max=100, step=1, unit_of_measurement="%",
                mode=NumberSelectorMode.SLIDER,
            )
        ),
        vol.Required(CONF_DAY_MAX_CCT, default=d(CONF_DAY_MAX_CCT)): NumberSelector(
            NumberSelectorConfig(
                min=2700, max=6500, step=50, unit_of_measurement="K",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(CONF_DAY_BASE_CCT, default=d(CONF_DAY_BASE_CCT)): NumberSelector(
            NumberSelectorConfig(
                min=2700, max=6500, step=50, unit_of_measurement="K",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(
            CONF_MIN_BRIGHTNESS, default=d(CONF_MIN_BRIGHTNESS)
        ): NumberSelector(
            NumberSelectorConfig(min=1, max=128, step=1, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_MIN_CCT, default=d(CONF_MIN_CCT)): NumberSelector(
            NumberSelectorConfig(
                min=800, max=2700, step=50, unit_of_measurement="K",
                mode=NumberSelectorMode.BOX,
            )
        ),
    }


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
    """Options: pick bulbs, set global defaults, or override a single bulb."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry
        self._override_device: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["bulbs", "defaults", "overrides"],
        )

    # --- Bulb selection -------------------------------------------------------

    async def async_step_bulbs(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        devices = _discover_oio_devices(self.hass)
        current = self.entry.data.get(CONF_WRAPPED_DEVICES, [])

        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={
                    **self.entry.data,
                    CONF_WRAPPED_DEVICES: user_input[CONF_WRAPPED_DEVICES],
                },
            )
            return self.async_create_entry(title="", data=dict(self.entry.options))

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
            }
        )
        return self.async_show_form(step_id="bulbs", data_schema=schema)

    # --- Global defaults ------------------------------------------------------

    async def async_step_defaults(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            new_options = dict(self.entry.options)
            new_options.update(user_input)
            return self.async_create_entry(title="", data=new_options)

        schema = vol.Schema(_tuning_fields(self.entry.options))
        return self.async_show_form(step_id="defaults", data_schema=schema)

    # --- Per-bulb overrides ---------------------------------------------------

    async def async_step_overrides(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        devices = _discover_oio_devices(self.hass)
        wrapped = self.entry.data.get(CONF_WRAPPED_DEVICES, [])
        choices = {did: devices.get(did, did) for did in wrapped}
        if not choices:
            return self.async_abort(reason="no_oio_devices")

        if user_input is not None:
            self._override_device = user_input["device"]
            return await self.async_step_device()

        options = [
            SelectOptionDict(value=did, label=label)
            for did, label in choices.items()
        ]
        schema = vol.Schema(
            {
                vol.Required("device"): SelectSelector(
                    SelectSelectorConfig(
                        options=options, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
            }
        )
        return self.async_show_form(step_id="overrides", data_schema=schema)

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        device_id = self._override_device
        all_overrides = self.entry.options.get(CONF_OVERRIDES, {})
        # Pre-fill with this bulb's effective values (global defaults overlaid
        # with any existing override for it).
        effective = {
            k: self.entry.options.get(k)
            for k in TUNABLE_KEYS
            if self.entry.options.get(k) is not None
        }
        effective.update(all_overrides.get(device_id, {}))

        if user_input is not None:
            new_options = dict(self.entry.options)
            overrides = dict(new_options.get(CONF_OVERRIDES, {}))
            if user_input.pop("reset_to_defaults", False):
                overrides.pop(device_id, None)
            else:
                overrides[device_id] = {
                    k: v for k, v in user_input.items() if k in TUNABLE_KEYS
                }
            new_options[CONF_OVERRIDES] = overrides
            return self.async_create_entry(title="", data=new_options)

        schema = vol.Schema(
            {
                **_tuning_fields(effective),
                vol.Optional("reset_to_defaults", default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="device",
            data_schema=schema,
            description_placeholders={"bulb": choices_label(self.hass, device_id)},
        )


def choices_label(hass, device_id: str) -> str:
    """Friendly label for a device, for the override form heading."""
    return _discover_oio_devices(hass).get(device_id, device_id)

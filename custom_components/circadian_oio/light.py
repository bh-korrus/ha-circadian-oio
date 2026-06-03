"""Light platform for Circadian OIO."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.sun import get_astral_event_next
from homeassistant.util import dt as dt_util

from .const import (
    CONF_WRAPPED_DEVICES,
    DATA_HIDDEN,
    DOMAIN,
    MAX_BRIGHTNESS,
    MIN_BRIGHTNESS,
    RENDER_TRANSITION_SECONDS,
    UPDATE_INTERVAL_SECONDS,
    USER_TRANSITION_SECONDS,
)
from .render import render

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up wrapper lights from a config entry."""
    device_ids: list[str] = entry.data.get(CONF_WRAPPED_DEVICES, [])
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    hidden: dict[str, object] = hass.data[DOMAIN][entry.entry_id][DATA_HIDDEN]

    entities: list[CircadianOIOLight] = []
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        if device is None:
            _LOGGER.warning("Wrapped device %s no longer exists", device_id)
            continue

        # Find the underlying light entity on this device. Exclude our own
        # wrapper entities: the wrapper shares the bulb's device_info, so on any
        # reload it appears here too. Without this filter a wrapper whose
        # entity_id sorts before the raw bulb's would be picked as its own
        # "underlying" — hiding itself and driving itself in a feedback loop.
        # Pick the lowest remaining entity_id so the choice is stable.
        light_entities = sorted(
            ent.entity_id
            for ent in er.async_entries_for_device(
                ent_reg, device_id, include_disabled_entities=True
            )
            if ent.entity_id.startswith("light.") and ent.platform != DOMAIN
        )
        if not light_entities:
            _LOGGER.warning("Device %s has no light entity to wrap", device_id)
            continue
        underlying_entity_id = light_entities[0]

        # Hide the underlying entity so the user only sees the wrapper, but
        # remember its prior state so we can restore it when unwrapped/unloaded.
        underlying = ent_reg.async_get(underlying_entity_id)
        prior = underlying.hidden_by if underlying else None
        hidden[underlying_entity_id] = prior
        if prior != er.RegistryEntryHider.USER:
            ent_reg.async_update_entity(
                underlying_entity_id,
                hidden_by=er.RegistryEntryHider.INTEGRATION,
            )

        entities.append(
            CircadianOIOLight(hass, device, underlying_entity_id)
        )

    async_add_entities(entities)


class CircadianOIOLight(LightEntity, RestoreEntity):
    """A single-axis dimmer that wraps a real OIO bulb with circadian rendering."""

    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(
        self,
        hass: HomeAssistant,
        device,
        underlying_entity_id: str,
    ) -> None:
        self.hass = hass
        self._device = device
        self._underlying_entity_id = underlying_entity_id

        base_name = device.name_by_user or device.name or device.id
        self._attr_name = f"{base_name} (Circadian)"
        self._attr_unique_id = f"{DOMAIN}_{device.id}"
        self._attr_device_info = {"identifiers": device.identifiers}

        # Intent is the user-visible "brightness." Stored as 0–100 (float).
        self._intent: float = 100.0
        self._is_on: bool = False
        # Guards against a 60s tick firing while a prior render is still in flight.
        self._applying: bool = False

    # --- Lifecycle ------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Restore intent and start the time-based render loop."""
        await super().async_added_to_hass()

        # Self-heal: a pre-fix version could pick this wrapper as its own
        # underlying on reload and hide it. If our own entity is hidden by the
        # integration, make it visible again. (A deliberate USER hide is left
        # alone.)
        if (
            self.registry_entry is not None
            and self.registry_entry.hidden_by is er.RegistryEntryHider.INTEGRATION
        ):
            er.async_get(self.hass).async_update_entity(
                self.entity_id, hidden_by=None
            )

        last_state = await self.async_get_last_state()
        if last_state and last_state.state == STATE_ON:
            self._is_on = True
            br = last_state.attributes.get(ATTR_BRIGHTNESS)
            if br:
                self._intent = (br / 255.0) * 100.0

        # Re-render every minute so caps shift visibly with time of day.
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._handle_tick,
                timedelta(seconds=UPDATE_INTERVAL_SECONDS),
            )
        )

        # Push the current intent to the bulb on startup (if on). Settle quickly
        # rather than crawling in over the long time-of-day transition.
        if self._is_on:
            await self._apply(USER_TRANSITION_SECONDS)

    # --- LightEntity surface --------------------------------------------------

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def brightness(self) -> int | None:
        if not self._is_on:
            return None
        return max(1, min(255, round(self._intent / 100.0 * 255.0)))

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            self._intent = (kwargs[ATTR_BRIGHTNESS] / 255.0) * 100.0
        self._is_on = True
        # A direct user/script/voice change should track the control, not crawl
        # in over the slow time-of-day transition.
        await self._apply(USER_TRANSITION_SECONDS)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._is_on = False
        await self.hass.services.async_call(
            "light",
            "turn_off",
            {"entity_id": self._underlying_entity_id},
            blocking=False,
        )
        self.async_write_ha_state()

    # --- Render plumbing ------------------------------------------------------

    @callback
    def _handle_tick(self, _now) -> None:
        """Periodic re-render; fire-and-forget. Uses the long transition so the
        time-of-day drift fades smoothly between ticks."""
        if self._is_on and not self._applying:
            self.hass.async_create_task(self._apply(RENDER_TRANSITION_SECONDS))

    async def _apply(self, transition: float) -> None:
        """Compute and push (brightness, CCT) to the underlying bulb.

        transition is the fade time handed to the underlying light: short for
        direct user actions, long (RENDER_TRANSITION_SECONDS) for the periodic
        time-of-day re-render.
        """
        self._applying = True
        try:
            now = dt_util.now()
            next_sunset = get_astral_event_next(self.hass, "sunset")
            sun_state = self.hass.states.get("sun.sun")
            is_day = sun_state is not None and sun_state.state == "above_horizon"

            brightness, cct = render(
                intent=self._intent,
                now=now,
                next_sunset=next_sunset,
                is_day=is_day,
            )

            await self.hass.services.async_call(
                "light",
                "turn_on",
                {
                    "entity_id": self._underlying_entity_id,
                    "brightness": brightness,
                    "color_temp_kelvin": cct,
                    "transition": transition,
                },
                blocking=False,
            )

            _LOGGER.debug(
                "%s rendered intent=%.1f -> brightness=%d, cct=%dK on %s",
                self.entity_id,
                self._intent,
                brightness,
                cct,
                self._underlying_entity_id,
            )
        finally:
            self._applying = False

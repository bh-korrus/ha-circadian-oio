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
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.sun import get_astral_event_next
from homeassistant.util import dt as dt_util

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
    DATA_HIDDEN,
    DAY_BASE_CCT,
    DOMAIN,
    LATE_NIGHT_END_MIN,
    LATE_NIGHT_MAX_B_PCT,
    LATE_NIGHT_START_MIN,
    MAX_BRIGHTNESS,
    MAX_CCT_DAY,
    MIN_BRIGHTNESS,
    MIN_CCT,
    NINEPM_TRANSITION_LEAD_MIN,
    RENDER_TRANSITION_SECONDS,
    TUNABLE_KEYS,
    UPDATE_INTERVAL_SECONDS,
    USER_TRANSITION_SECONDS,
)
from .render import RenderSettings, compute_caps, current_period, render


def _parse_hhmm_to_min(value: str | None, default_min: int) -> int:
    """Parse a 'HH:MM' or 'HH:MM:SS' time string to minutes since midnight."""
    if not value:
        return default_min
    try:
        parts = value.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return default_min


def _next_occurrence(now: datetime, minutes: int) -> datetime:
    """Next datetime at the given minutes-since-midnight, today or tomorrow."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    when = midnight + timedelta(minutes=minutes % (24 * 60))
    if when <= now:
        when += timedelta(days=1)
    return when


def _next_transition(
    now: datetime,
    s: RenderSettings,
    next_sunset: datetime | None,
    next_sunrise: datetime | None,
) -> tuple[str | None, int | None]:
    """Soonest upcoming schedule boundary as (name, minutes_until)."""
    lead = s.transition_lead_min
    events: list[tuple[str, datetime]] = [
        ("pre_night", _next_occurrence(now, s.late_night_start_min - lead)),
        ("night", _next_occurrence(now, s.late_night_start_min)),
        ("morning_ramp", _next_occurrence(now, s.late_night_end_min)),
        ("day", _next_occurrence(now, s.late_night_end_min + lead)),
    ]
    if next_sunset is not None:
        if lead > 0:
            events.append(("sunset_transition", next_sunset - timedelta(minutes=lead)))
        events.append(("sunset", next_sunset))
    if next_sunrise is not None:
        if lead > 0:
            events.append(("pre_sunrise", next_sunrise - timedelta(minutes=lead)))
        events.append(("sunrise", next_sunrise))

    future = [(name, when) for name, when in events if when > now]
    if not future:
        return None, None
    name, when = min(future, key=lambda item: item[1])
    return name, round((when - now).total_seconds() / 60.0)


def _effective_options(options, device_id: str) -> dict:
    """Global tunables overlaid with this bulb's per-bulb overrides."""
    effective = {
        k: options.get(k) for k in TUNABLE_KEYS if options.get(k) is not None
    }
    override = options.get(CONF_OVERRIDES, {}).get(device_id, {})
    effective.update({k: v for k, v in override.items() if v is not None})
    return effective


def _settings_from_options(options) -> RenderSettings:
    """Build render settings from a flat options dict, with defaults."""
    return RenderSettings(
        late_night_start_min=_parse_hhmm_to_min(
            options.get(CONF_NIGHT_START), LATE_NIGHT_START_MIN
        ),
        late_night_end_min=_parse_hhmm_to_min(
            options.get(CONF_NIGHT_END), LATE_NIGHT_END_MIN
        ),
        transition_lead_min=int(
            options.get(CONF_TRANSITION_MINUTES, NINEPM_TRANSITION_LEAD_MIN)
        ),
        late_night_max_b_pct=float(
            options.get(CONF_NIGHT_BRIGHTNESS_PCT, LATE_NIGHT_MAX_B_PCT)
        ),
        max_cct_day=int(options.get(CONF_DAY_MAX_CCT, MAX_CCT_DAY)),
        day_base_cct=int(options.get(CONF_DAY_BASE_CCT, DAY_BASE_CCT)),
        min_brightness=int(options.get(CONF_MIN_BRIGHTNESS, MIN_BRIGHTNESS)),
        min_cct=int(options.get(CONF_MIN_CCT, MIN_CCT)),
    )

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

        settings = _settings_from_options(
            _effective_options(entry.options, device_id)
        )
        entities.append(
            CircadianOIOLight(hass, device, underlying_entity_id, settings)
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
        settings: RenderSettings,
    ) -> None:
        self.hass = hass
        self._device = device
        self._underlying_entity_id = underlying_entity_id
        self._settings = settings

        base_name = device.name_by_user or device.name or device.id
        self._attr_name = f"{base_name} (Circadian)"
        self._attr_unique_id = f"{DOMAIN}_{device.id}"
        self._attr_device_info = {"identifiers": device.identifiers}

        # Intent is the user-visible "brightness." Stored as 0–100 (float).
        self._intent: float = 100.0
        self._is_on: bool = False
        # Guards against a 60s tick firing while a prior render is still in flight.
        self._applying: bool = False
        # Last values pushed to the bulb, and the published state attributes.
        self._rendered_brightness: int | None = None
        self._rendered_cct: int | None = None
        self._attrs: dict[str, Any] = {}

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

        # React the instant the underlying bulb is switched on or off by any
        # route — a Pico, a group, a scene, voice — rather than waiting up to a
        # minute for the next tick to notice.
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self._underlying_entity_id],
                self._underlying_changed,
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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Surface the circadian state for dashboards and automations."""
        return self._attrs

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
    def _underlying_changed(self, event) -> None:
        """Mirror the underlying bulb's on/off the moment it changes, so an
        external control (Pico, group, scene, voice) gets an instant circadian
        render instead of waiting for the periodic tick. Guarded so the
        wrapper's own commands don't re-trigger it."""
        if self._applying:
            return
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        if new_state.state == STATE_ON and not self._is_on:
            self._is_on = True
            self.async_write_ha_state()
            self.hass.async_create_task(self._apply(USER_TRANSITION_SECONDS))
        elif new_state.state == STATE_OFF and self._is_on:
            self._is_on = False
            self.async_write_ha_state()

    @callback
    def _handle_tick(self, _now) -> None:
        """Periodic re-render; fire-and-forget. Uses the long transition so the
        time-of-day drift fades smoothly between ticks.

        Syncs to the underlying bulb's real state first, so circadian drift
        applies whenever the bulb is on — even if it was turned on or off
        outside the wrapper (voice, a scene, or the raw entity). Without this,
        the bulb only tracked time when the wrapper itself had turned it on.
        """
        if self._applying:
            return

        underlying = self.hass.states.get(self._underlying_entity_id)
        if underlying is not None:
            if underlying.state == STATE_ON and not self._is_on:
                self._is_on = True
                self.async_write_ha_state()
            elif underlying.state == STATE_OFF and self._is_on:
                self._is_on = False
                self.async_write_ha_state()

        if self._is_on:
            # Periodic drift: skip the command when nothing actually changed, so
            # we don't flood the Matter/Thread network with identical re-renders.
            self.hass.async_create_task(
                self._apply(RENDER_TRANSITION_SECONDS, force=False)
            )

    async def _apply(self, transition: float, force: bool = True) -> None:
        """Compute and push (brightness, CCT) to the underlying bulb.

        transition is the fade time handed to the underlying light: short for
        direct user actions, long (RENDER_TRANSITION_SECONDS) for the periodic
        time-of-day re-render. When force is False (the periodic tick), the
        Matter command is skipped if the rendered (brightness, CCT) is unchanged
        since the last send, to keep network traffic down. Deliberate inputs use
        force=True so a press always lands.
        """
        self._applying = True
        try:
            now = dt_util.now()
            next_sunset = get_astral_event_next(self.hass, "sunset")
            next_sunrise = get_astral_event_next(self.hass, "sunrise")
            sun_state = self.hass.states.get("sun.sun")
            is_day = sun_state is not None and sun_state.state == "above_horizon"

            brightness, cct = render(
                intent=self._intent,
                now=now,
                next_sunset=next_sunset,
                is_day=is_day,
                settings=self._settings,
                next_sunrise=next_sunrise,
            )

            changed = (
                brightness != self._rendered_brightness
                or cct != self._rendered_cct
            )
            if force or changed:
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

            self._rendered_brightness = brightness
            self._rendered_cct = cct
            self._update_attrs(now, next_sunset, next_sunrise, is_day)
            self.async_write_ha_state()
        finally:
            self._applying = False

    @callback
    def _update_attrs(self, now, next_sunset, next_sunrise, is_day: bool) -> None:
        """Recompute the published state attributes from the current moment."""
        max_b_pct, max_cct = compute_caps(
            now, next_sunset, is_day, self._settings, next_sunrise
        )
        period = current_period(
            now, next_sunset, is_day, self._settings, next_sunrise
        )
        nxt_name, nxt_min = _next_transition(
            now, self._settings, next_sunset, next_sunrise
        )
        attrs: dict[str, Any] = {
            "intent": round(self._intent, 1),
            "circadian_period": period,
            "rendered_brightness": self._rendered_brightness,
            "rendered_color_temp_kelvin": self._rendered_cct,
            "max_brightness_pct": round(max_b_pct, 1),
            "max_color_temp_kelvin": max_cct,
            "underlying_entity_id": self._underlying_entity_id,
        }
        if nxt_name is not None:
            attrs["next_transition"] = nxt_name
            attrs["minutes_to_next_transition"] = nxt_min
        self._attrs = attrs

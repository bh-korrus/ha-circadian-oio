"""Integration tests for the wrapper entity (light.py).

These drive a real Home Assistant so the entity lifecycle and the render path
get exercised end to end — which is where the dt_util.now() regression lived.
Skipped automatically when HA is not installed.

Run with:
    pip install -r requirements-test.txt
    pytest tests/
"""
from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from datetime import timedelta  # noqa: E402

from homeassistant.const import ATTR_ENTITY_ID  # noqa: E402
from homeassistant.core import HomeAssistant, callback  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.circadian_oio.const import (  # noqa: E402
    CONF_WRAPPED_DEVICES,
    DOMAIN,
    MAX_BRIGHTNESS,
    MIN_BRIGHTNESS,
    MIN_CCT,
    MAX_CCT_DAY,
    RENDER_TRANSITION_SECONDS,
    UPDATE_INTERVAL_SECONDS,
    USER_TRANSITION_SECONDS,
)


def test_settings_from_options_parses_and_defaults():
    """The options dict maps to RenderSettings; empty options give defaults."""
    from custom_components.circadian_oio.light import _settings_from_options
    from custom_components.circadian_oio.render import DEFAULT_SETTINGS

    s = _settings_from_options(
        {
            "night_start": "22:30:00",
            "night_end": "06:15",
            "transition_minutes": 45,
            "night_brightness_pct": 15,
            "day_max_cct": 5000,
            "day_base_cct": 4000,
            "min_brightness": 6,
            "min_cct": 1800,
        }
    )
    assert s.late_night_start_min == 22 * 60 + 30
    assert s.late_night_end_min == 6 * 60 + 15
    assert s.transition_lead_min == 45
    assert s.late_night_max_b_pct == 15.0
    assert s.max_cct_day == 5000
    assert s.day_base_cct == 4000
    assert s.min_brightness == 6
    assert s.min_cct == 1800

    assert _settings_from_options({}) == DEFAULT_SETTINGS


def test_effective_options_overlays_per_bulb_override():
    """Per-bulb overrides win over globals; other bulbs keep the globals."""
    from custom_components.circadian_oio.light import _effective_options

    options = {
        "night_start": "21:00:00",
        "min_brightness": 1,
        "overrides": {"dev1": {"night_start": "23:00:00", "min_brightness": 6}},
    }
    eff1 = _effective_options(options, "dev1")
    assert eff1["night_start"] == "23:00:00"
    assert eff1["min_brightness"] == 6

    eff2 = _effective_options(options, "dev2")
    assert eff2["night_start"] == "21:00:00"
    assert eff2["min_brightness"] == 1


def _wrapper_entity_id(hass: HomeAssistant, device_id: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "light", DOMAIN, f"{DOMAIN}_{device_id}"
    )


async def _setup_entry(hass: HomeAssistant, device_id: str) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_WRAPPED_DEVICES: [device_id]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_creates_wrapper_and_hides_underlying(
    hass, auto_enable_custom_integrations, oio_device
):
    """Wrapping a bulb exposes a circadian dimmer and hides the raw light."""
    device_id, underlying = oio_device
    await _setup_entry(hass, device_id)

    wrapper_id = _wrapper_entity_id(hass, device_id)
    assert wrapper_id is not None
    assert hass.states.get(wrapper_id) is not None

    hidden = er.async_get(hass).async_get(underlying).hidden_by
    assert hidden == er.RegistryEntryHider.INTEGRATION


async def test_turn_on_drives_underlying_with_brightness_and_cct(
    hass, auto_enable_custom_integrations, oio_device
):
    """Turning the wrapper on must push a (brightness, color_temp_kelvin) pair
    to the underlying bulb. This is the regression guard for the tz crash: if
    _apply() raised, no downstream call would ever be recorded."""
    device_id, underlying = oio_device
    await _setup_entry(hass, device_id)
    wrapper_id = _wrapper_entity_id(hass, device_id)

    downstream: list[dict] = []

    @callback
    def _record(event):
        data = event.data
        if data.get("domain") == "light" and data.get("service") == "turn_on":
            service_data = data.get("service_data", {})
            if service_data.get(ATTR_ENTITY_ID) == underlying:
                downstream.append(service_data)

    hass.bus.async_listen("call_service", _record)

    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: wrapper_id, "brightness": 200},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert downstream, "wrapper never drove the underlying light (did _apply raise?)"
    call = downstream[-1]
    assert MIN_BRIGHTNESS <= call["brightness"] <= MAX_BRIGHTNESS
    assert MIN_CCT <= call["color_temp_kelvin"] <= MAX_CCT_DAY
    # A direct user action fades fast, not over the slow time-of-day window.
    assert call["transition"] == USER_TRANSITION_SECONDS

    # The wrapper should report the user's intent back, not the rendered value.
    state = hass.states.get(wrapper_id)
    assert state.state == "on"
    assert state.attributes["brightness"] == 200


async def test_periodic_tick_uses_long_transition(
    hass, auto_enable_custom_integrations, oio_device
):
    """The once-a-minute time-of-day re-render fades slowly; a user action does
    not. This is the regression guard for the 'bulb lags the slider' bug."""
    device_id, underlying = oio_device
    await _setup_entry(hass, device_id)
    wrapper_id = _wrapper_entity_id(hass, device_id)

    downstream: list[dict] = []

    @callback
    def _record(event):
        data = event.data
        if data.get("domain") == "light" and data.get("service") == "turn_on":
            service_data = data.get("service_data", {})
            if service_data.get(ATTR_ENTITY_ID) == underlying:
                downstream.append(service_data)

    hass.bus.async_listen("call_service", _record)

    # User turns it on: fast fade.
    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: wrapper_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert downstream[-1]["transition"] == USER_TRANSITION_SECONDS

    # Advance the clock past the update interval to fire the periodic tick.
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=UPDATE_INTERVAL_SECONDS + 1)
    )
    await hass.async_block_till_done()

    assert len(downstream) >= 2, "periodic tick never re-rendered"
    assert downstream[-1]["transition"] == RENDER_TRANSITION_SECONDS


async def test_state_attributes_are_published(
    hass, auto_enable_custom_integrations, oio_device
):
    """The wrapper exposes circadian state for dashboards/automations."""
    device_id, _ = oio_device
    await _setup_entry(hass, device_id)
    wrapper_id = _wrapper_entity_id(hass, device_id)

    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: wrapper_id, "brightness": 200}, blocking=True
    )
    await hass.async_block_till_done()

    attrs = hass.states.get(wrapper_id).attributes
    assert "circadian_period" in attrs
    assert "max_brightness_pct" in attrs
    assert "max_color_temp_kelvin" in attrs
    assert attrs["rendered_brightness"] is not None
    assert attrs["rendered_color_temp_kelvin"] is not None
    assert attrs["intent"] == pytest.approx(200 / 255 * 100, abs=0.5)


async def test_tick_adopts_externally_turned_on_bulb(
    hass, auto_enable_custom_integrations, oio_device
):
    """If the bulb is turned on outside the wrapper, the periodic tick adopts it
    and applies circadian drift — it must not only track time when the wrapper
    itself turned the bulb on."""
    device_id, underlying = oio_device
    await _setup_entry(hass, device_id)
    wrapper_id = _wrapper_entity_id(hass, device_id)
    assert hass.states.get(wrapper_id).state == "off"

    # Simulate the underlying bulb being switched on by something else.
    hass.states.async_set(underlying, "on")

    rendered: list[dict] = []

    @callback
    def _record(event):
        data = event.data
        if data.get("domain") == "light" and data.get("service") == "turn_on":
            service_data = data.get("service_data", {})
            if (
                service_data.get(ATTR_ENTITY_ID) == underlying
                and "color_temp_kelvin" in service_data
            ):
                rendered.append(service_data)

    hass.bus.async_listen("call_service", _record)
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=UPDATE_INTERVAL_SECONDS + 1)
    )
    await hass.async_block_till_done()

    assert hass.states.get(wrapper_id).state == "on", "wrapper did not adopt the bulb"
    assert rendered, "tick did not apply circadian render to the adopted bulb"


async def test_turn_off_routes_to_underlying(
    hass, auto_enable_custom_integrations, oio_device
):
    """Turning the wrapper off turns the real bulb off."""
    device_id, underlying = oio_device
    await _setup_entry(hass, device_id)
    wrapper_id = _wrapper_entity_id(hass, device_id)

    off_calls: list[dict] = []

    @callback
    def _record(event):
        data = event.data
        if data.get("domain") == "light" and data.get("service") == "turn_off":
            service_data = data.get("service_data", {})
            if service_data.get(ATTR_ENTITY_ID) == underlying:
                off_calls.append(service_data)

    hass.bus.async_listen("call_service", _record)

    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: wrapper_id}, blocking=True
    )
    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: wrapper_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert off_calls, "wrapper never turned the underlying light off"
    assert hass.states.get(wrapper_id).state == "off"


async def test_does_not_wrap_its_own_wrapper_entity(
    hass, auto_enable_custom_integrations, oio_device
):
    """On reload the wrapper shares the bulb's device, so it shows up among the
    device's light entities. Setup must skip our own platform and still target
    the real bulb — otherwise the wrapper drives itself (the self-wrap bug)."""
    device_id, underlying = oio_device
    reg = er.async_get(hass)
    dev_id = reg.async_get(underlying).device_id

    # A leftover wrapper entity from a previous setup, on the same device, with
    # an entity_id that sorts BEFORE the raw bulb — exactly what triggered the bug.
    stale = reg.async_get_or_create(
        "light",
        DOMAIN,
        f"{DOMAIN}_stale",
        device_id=dev_id,
        suggested_object_id="aaa_stale_circadian",
    )
    assert stale.entity_id < underlying  # would have been picked by the old code

    await _setup_entry(hass, device_id)
    wrapper_id = _wrapper_entity_id(hass, device_id)

    downstream: list[dict] = []

    @callback
    def _record(event):
        data = event.data
        if data.get("domain") == "light" and data.get("service") == "turn_on":
            service_data = data.get("service_data", {})
            # Only the wrapper's render calls carry color_temp_kelvin; this skips
            # the test's own plain turn_on of the wrapper.
            if "color_temp_kelvin" in service_data:
                downstream.append(service_data)

    hass.bus.async_listen("call_service", _record)
    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: wrapper_id}, blocking=True
    )
    await hass.async_block_till_done()

    targets = [c.get(ATTR_ENTITY_ID) for c in downstream]
    assert underlying in targets, "wrapper did not drive the real bulb"
    assert stale.entity_id not in targets, "wrapper drove a wrapper (self-wrap bug)"
    assert wrapper_id not in targets, "wrapper drove itself"


async def test_unload_restores_hidden_underlying(
    hass, auto_enable_custom_integrations, oio_device
):
    """Unloading the integration un-hides the real bulb so it doesn't vanish."""
    device_id, underlying = oio_device
    entry = await _setup_entry(hass, device_id)

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get(underlying).hidden_by == er.RegistryEntryHider.INTEGRATION

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert ent_reg.async_get(underlying).hidden_by is None

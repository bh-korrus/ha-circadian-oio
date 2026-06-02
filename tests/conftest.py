"""Test setup.

Two test tiers live side by side:

- Pure render tests (test_render.py, test_light_plumbing.py) need no Home
  Assistant. To let them run in a bare interpreter we stub the HA modules with
  MagicMock — but only when HA is genuinely not installed.
- Integration tests (test_config_flow.py, test_light.py) need a real Home
  Assistant. They run under pytest-homeassistant-custom-component, which
  provides the `hass` fixture and friends. When HA is importable we must NOT
  stub it, or those fixtures break.

So: try to import homeassistant. If that works, register the PHACC plugin and
do nothing else. If it fails, install the lightweight stubs.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

try:
    import homeassistant  # noqa: F401

    HA_AVAILABLE = True
except ImportError:
    HA_AVAILABLE = False


if HA_AVAILABLE:
    # PHACC ships pytest fixtures (hass, enable_custom_integrations, etc.) as a
    # plugin. Loading it here means the integration tests can request `hass`.
    pytest_plugins = ["pytest_homeassistant_custom_component"]

    import pytest

    @pytest.fixture
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Make the custom integration loadable in every integration test."""
        yield

    @pytest.fixture(autouse=True)
    async def _unload_entries(hass):
        """Unload our config entries after each test.

        The wrapper registers a 60s render tick (cancelled on entity removal),
        so a still-loaded entry would leave a lingering timer that PHACC flags.
        Unloading here keeps each test self-contained.
        """
        yield
        from homeassistant.config_entries import ConfigEntryState

        from custom_components.circadian_oio.const import DOMAIN as _DOMAIN

        for entry in hass.config_entries.async_entries(_DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    @pytest.fixture
    async def oio_device(hass):
        """Register a fake Korrus/OIO device with one underlying light entity.

        Returns (device_id, underlying_entity_id). The device hangs off a stub
        host config entry (as a Matter bulb would); we never set that entry up,
        we only need it to exist so the registries accept the device.
        """
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er
        from pytest_homeassistant_custom_component.common import MockConfigEntry

        host = MockConfigEntry(domain="matter", data={})
        host.add_to_hass(hass)

        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=host.entry_id,
            identifiers={("matter", "oio-bulb-1")},
            manufacturer="Korrus Inc.",
            name="Kitchen Bulb",
        )
        entity = er.async_get(hass).async_get_or_create(
            "light",
            "matter",
            "oio-bulb-1-light",
            device_id=device.id,
            config_entry=host,
            original_name="Kitchen Bulb",
        )
        return device.id, entity.entity_id
else:
    # No HA installed: stub the modules the package imports so the pure render
    # tests can still be collected and run.
    for module_name in [
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.light",
        "homeassistant.config_entries",
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.helpers",
        "homeassistant.helpers.device_registry",
        "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.entity_registry",
        "homeassistant.helpers.event",
        "homeassistant.helpers.restore_state",
        "homeassistant.helpers.selector",
        "homeassistant.helpers.sun",
        "homeassistant.util",
        "homeassistant.util.dt",
        "voluptuous",
    ]:
        sys.modules.setdefault(module_name, MagicMock())

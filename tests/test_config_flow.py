"""Integration tests for the config and options flows.

These run against a real Home Assistant via pytest-homeassistant-custom-component.
They are skipped automatically when HA is not installed, so the pure render
suite still runs on a bare interpreter.

Run with:
    pip install -r requirements-test.txt
    pytest tests/
"""
from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import SOURCE_USER  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.circadian_oio.const import (  # noqa: E402
    CONF_WRAPPED_DEVICES,
    DOMAIN,
)


async def test_flow_discovers_and_creates_entry(
    hass, auto_enable_custom_integrations, oio_device
):
    """A Korrus device should be offered and selectable, creating an entry."""
    device_id, _ = oio_device

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_WRAPPED_DEVICES: [device_id]}
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_WRAPPED_DEVICES] == [device_id]


async def test_flow_aborts_without_oio_devices(
    hass, auto_enable_custom_integrations
):
    """No Korrus device present: the flow aborts cleanly."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_oio_devices"


async def test_flow_ignores_non_oio_manufacturer(
    hass, auto_enable_custom_integrations
):
    """A non-Korrus light must not be discovered — this is the science guard."""
    host = MockConfigEntry(domain="other", data={})
    host.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=host.entry_id,
        identifiers={("other", "acme-1")},
        manufacturer="Acme Lighting",
        name="Generic RGBW Lamp",
    )
    er.async_get(hass).async_get_or_create(
        "light", "other", "acme-1-light", device_id=device.id, config_entry=host
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "no_oio_devices"


async def test_flow_single_instance_only(
    hass, auto_enable_custom_integrations, oio_device
):
    """A second setup attempt aborts; bulbs are managed via Options instead."""
    existing = MockConfigEntry(domain=DOMAIN, data={CONF_WRAPPED_DEVICES: []})
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_unwrap_restores_underlying(
    hass, auto_enable_custom_integrations, oio_device
):
    """Removing a bulb in Options must un-hide its underlying light.

    This is the end-to-end version of the teardown regression: wrap a bulb
    (underlying hidden), then unwrap it via Options (entry reloads), and the
    real light should be visible again.
    """
    device_id, underlying = oio_device
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_WRAPPED_DEVICES: [device_id]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get(underlying).hidden_by == er.RegistryEntryHider.INTEGRATION

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_WRAPPED_DEVICES: []}
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.data[CONF_WRAPPED_DEVICES] == []
    assert ent_reg.async_get(underlying).hidden_by is None

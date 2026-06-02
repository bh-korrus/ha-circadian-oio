"""The Circadian OIO integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DATA_HIDDEN, DOMAIN

PLATFORMS: list[Platform] = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Circadian OIO from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    # Per-entry runtime store. DATA_HIDDEN maps each underlying entity_id we
    # hide to its prior hidden_by value so async_unload_entry can restore it.
    hass.data[DOMAIN][entry.entry_id] = {DATA_HIDDEN: {}}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and un-hide any underlying lights we hid."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        store = hass.data[DOMAIN].pop(entry.entry_id, None)
        if store:
            _restore_hidden(hass, store.get(DATA_HIDDEN, {}))
    return unload_ok


def _restore_hidden(hass: HomeAssistant, hidden: dict[str, object]) -> None:
    """Restore the original hidden_by state of every entity we wrapped.

    A prior USER hide is preserved; anything else (including a leftover
    INTEGRATION hide from us) is cleared so the user's real bulbs reappear.
    """
    ent_reg = er.async_get(hass)
    for entity_id, prior in hidden.items():
        if ent_reg.async_get(entity_id) is None:
            continue
        restore = prior if prior == er.RegistryEntryHider.USER else None
        ent_reg.async_update_entity(entity_id, hidden_by=restore)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)

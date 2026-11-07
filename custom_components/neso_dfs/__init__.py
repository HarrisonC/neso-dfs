"""NESO Demand Flexibility Service integration."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_VERSION
from .coordinator import DfsConfigEntry, DfsCoordinator, storage_key

PLATFORMS = (Platform.CALENDAR, Platform.BINARY_SENSOR, Platform.SENSOR)


async def async_setup_entry(hass: HomeAssistant, entry: DfsConfigEntry) -> bool:
    """Restore and fetch the shared schedule, then set up its entities."""
    coordinator = DfsCoordinator(hass, entry)
    entry.runtime_data = coordinator
    try:
        await coordinator.async_initialize()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.async_shutdown()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DfsConfigEntry) -> bool:
    """Remove entities and cancel all integration timers."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: DfsConfigEntry) -> None:
    """Remove the integration-owned history when its entry is deleted."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()

"""Diagnostic timestamps for NESO downloads and new events."""

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DfsConfigEntry, DfsCoordinator
from .entity import DfsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DfsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(
        [
            DfsLastSuccessSensor(entry.runtime_data),
            DfsLastNewEventSensor(entry.runtime_data),
        ]
    )


class DfsLastSuccessSensor(DfsEntity, SensorEntity):
    """Keep the last successful timestamp visible during outages."""

    _attr_translation_key = "last_successful_update"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DfsCoordinator) -> None:
        super().__init__(coordinator, "last_successful_update")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.last_success


class DfsLastNewEventSensor(DfsEntity, SensorEntity):
    """Show when the latest new matching event was first observed."""

    _attr_translation_key = "last_new_event_detected"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DfsCoordinator) -> None:
        super().__init__(coordinator, "last_new_event_detected")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.last_new_event_detected

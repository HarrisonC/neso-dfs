"""Direction-specific active periods and report freshness."""

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DIRECTIONS
from .coordinator import DfsConfigEntry, DfsCoordinator
from .entity import DfsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DfsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(DfsActiveSensor(coordinator, direction) for direction in DIRECTIONS),
            DfsStaleSensor(coordinator),
        ]
    )


class DfsActiveSensor(DfsEntity, BinarySensorEntity):
    """Use clock listeners to change state without waiting for a CSV fetch."""

    def __init__(self, coordinator: DfsCoordinator, direction: str) -> None:
        super().__init__(coordinator, f"{direction.lower()}_active")
        self.direction = direction
        self._attr_translation_key = f"{direction.lower()}_active"
        self._attr_icon = "mdi:transmission-tower"

    @property
    def is_on(self) -> bool:
        now = dt_util.utcnow()
        period = self.coordinator.data.current_or_next(now, self.direction)
        return period is not None and period.start <= now < period.end

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.period_attributes(self.direction)


class DfsStaleSensor(DfsEntity, BinarySensorEntity):
    """Always expose the status of the latest attempt, including at startup."""

    _attr_translation_key = "data_stale"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-alert"

    def __init__(self, coordinator: DfsCoordinator) -> None:
        super().__init__(coordinator, "data_stale")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.stale

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "last_error": self.coordinator.last_error,
            "coverage_date": (
                self.coordinator.data.coverage_date.isoformat()
                if self.coordinator.data.coverage_date
                else None
            ),
        }

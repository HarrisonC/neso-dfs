"""Shared entity identity and cache-aware availability."""

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DATA_URL, DOMAIN, NAME
from .coordinator import DfsCoordinator


class DfsEntity(CoordinatorEntity[DfsCoordinator]):
    """A service entity backed by the last validated schedule."""

    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset({"slots", "source_event_ids"})

    def __init__(self, coordinator: DfsCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=f"{NAME} {coordinator.participant} Zone {coordinator.zone}",
            manufacturer="National Energy System Operator",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=DATA_URL,
        )

    @property
    def available(self) -> bool:
        """A failed poll does not invalidate today's saved schedule."""
        return self.coordinator.has_current_coverage

    def period_attributes(self, direction: str | None = None) -> dict[str, Any]:
        """Return current/next details without putting full history in state."""
        coordinator = self.coordinator
        period = coordinator.data.current_or_next(dt_util.utcnow(), direction)
        attributes = {
            "participant": coordinator.participant,
            "zone": coordinator.zone,
            "data_stale": coordinator.stale,
            "last_successful_update": (
                coordinator.data.last_success.isoformat()
                if coordinator.data.last_success
                else None
            ),
        }
        if period:
            attributes.update(period.attributes())
        elif direction:
            attributes["direction"] = direction
        return attributes

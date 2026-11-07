"""Read-only calendar of merged NESO DFS events."""

from datetime import date, datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOWNWARDS
from .coordinator import DfsConfigEntry, DfsCoordinator
from .entity import DfsEntity
from .models import Period


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DfsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([DfsCalendar(entry.runtime_data)])


def _utc_window(start: datetime, end: datetime, delivery_date: date) -> str:
    """Show dates when the UTC window spans a different day."""
    if start.date() == end.date() == delivery_date:
        return f"{start:%H:%M}–{end:%H:%M}"
    return f"{start:%d %b %H:%M} – {end:%d %b %H:%M}"


def calendar_event(period: Period) -> CalendarEvent:
    """List the event and its individual bid prices as readable text."""
    first = period.first
    action = (
        "Reduce energy usage"
        if first.direction == DOWNWARDS
        else "Increase energy usage"
    )
    window = _utc_window(period.start, period.end, first.delivery_date)
    description = [
        f"Participant: {first.participant}",
        f"Zone: {first.zone}",
        f"Direction: {first.direction}",
        f"Delivery date: {first.delivery_date:%d %b %Y}",
        f"Window (UTC): {window}",
        "",
        "Accepted live slots (UTC)",
    ]
    for slot in period.slots:
        slot_window = _utc_window(slot.start, slot.end, slot.delivery_date)
        description.append(
            f"• {slot_window} · £{slot.price}/MWh "
            f"· Event {slot.event_id} · Unit {slot.unit_id}"
        )
    return CalendarEvent(
        start=period.start,
        end=period.end,
        summary=f"DFS: {action}",
        description="\n".join(description),
        location=f"{first.participant} — Zone {first.zone}",
        uid=period.uid,
    )


class DfsCalendar(DfsEntity, CalendarEntity):
    """Expose the schedule without depending on a separate calendar service."""

    _attr_translation_key = "events"
    _attr_icon = "mdi:calendar-lightning"

    def __init__(self, coordinator: DfsCoordinator) -> None:
        super().__init__(coordinator, "events")

    @property
    def available(self) -> bool:
        """Keep archived calendar queries available during a new-day outage."""
        return super().available or bool(self.coordinator.data.periods)

    @property
    def event(self) -> CalendarEvent | None:
        period = self.coordinator.data.current_or_next(dt_util.utcnow())
        return calendar_event(period) if period else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.period_attributes()

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return overlapping events, with exclusive query boundaries."""
        return [
            calendar_event(period)
            for period in self.coordinator.data.periods
            if period.end > start_date and period.start < end_date
        ]

"""Polling, persistent schedules, and clock-driven entity updates."""

import asyncio
import logging
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import FetchError, async_fetch_report
from .const import (
    CONF_PARTICIPANT,
    CONF_POLL_INTERVAL,
    CONF_RETENTION_DAYS,
    CONF_ZONE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    STORAGE_VERSION,
    UK_TIMEZONE,
)
from .models import Schedule, participant_key

_LOGGER = logging.getLogger(__name__)


def storage_key(entry_id: str) -> str:
    """Use a store separate from config entries and Recorder."""
    return f"{DOMAIN}.{entry_id}"


class DfsCoordinator(DataUpdateCoordinator[Schedule]):
    """Own the single download and saved schedule for one participant/zone."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.data[CONF_PARTICIPANT]} zone {entry.data[CONF_ZONE]}",
            update_interval=timedelta(
                minutes=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
            ),
        )
        self.participant: str = entry.data[CONF_PARTICIPANT]
        self.zone: int = entry.data[CONF_ZONE]
        self.retention_days: int = entry.options.get(
            CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS
        )
        self.data = Schedule()
        self.stale = True
        self.last_error: str | None = None
        self._store = Store(hass, STORAGE_VERSION, storage_key(entry.entry_id))
        self._store_lock = asyncio.Lock()
        self._cancel_boundary = None
        self._clock_day = dt_util.utcnow().astimezone(UK_TIMEZONE).date()

    @property
    def has_current_coverage(self) -> bool:
        """An empty successfully fetched day is also valid coverage."""
        return (
            self.data.coverage_date == dt_util.utcnow().astimezone(UK_TIMEZONE).date()
        )

    async def async_initialize(self) -> None:
        """Restore before polling so an outage does not discard known events."""
        stored = await self._store.async_load()
        if stored is not None:
            try:
                schedule = Schedule.from_dict(stored)
                if any(
                    participant_key(slot.participant)
                    != participant_key(self.participant)
                    or slot.zone != self.zone
                    for period in schedule.periods
                    for slot in period.slots
                ):
                    raise ValueError("Cache belongs to another participant/zone")
                self.data = schedule.prune(
                    self._clock_day, self.retention_days, dt_util.utcnow()
                )
            except KeyError, TypeError, ValueError, ArithmeticError:
                _LOGGER.warning(
                    "Ignoring invalid NESO schedule cache for %s", self.name
                )
        await self.async_refresh()
        if not self.last_update_success and self.data.last_success is None:
            raise ConfigEntryNotReady(self.last_error or "NESO report unavailable")
        self._schedule_boundary()

    async def _async_update_data(self) -> Schedule:
        try:
            report = await async_fetch_report(self.hass)
        except FetchError as err:
            self.stale = True
            self.last_error = str(err)
            raise UpdateFailed(str(err)) from err
        async with self._store_lock:
            # A download may finish after the entry has been unloaded or
            # removed. It must not resurrect its store or overwrite a reload.
            if self._shutdown_requested:
                return self.data
            now = dt_util.utcnow()
            schedule = self.data.apply(
                report, self.participant, self.zone, now, self.retention_days
            )
            try:
                await self._store.async_save(schedule.as_dict())
            except (HomeAssistantError, OSError) as err:
                self.stale = True
                self.last_error = "Could not persist the NESO schedule"
                raise UpdateFailed(self.last_error) from err
            self.stale = False
            self.last_error = None
            self._clock_day = now.astimezone(UK_TIMEZONE).date()
            # Install before scheduling; the parent notifies entities once.
            self.data = schedule
        self._schedule_boundary()
        return schedule

    @callback
    def _schedule_boundary(self) -> None:
        """Schedule the next start, end, or UK midnight without an HTTP poll."""
        if self._cancel_boundary:
            self._cancel_boundary()
            self._cancel_boundary = None
        if self._shutdown_requested:
            return
        now = dt_util.utcnow()
        today = now.astimezone(UK_TIMEZONE).date()
        midnight = datetime.combine(today + timedelta(days=1), time.min, UK_TIMEZONE)
        candidates = [midnight]
        candidates.extend(
            boundary
            for period in self.data.periods
            for boundary in (period.start, period.end)
            if boundary > now
        )
        self._cancel_boundary = async_track_point_in_utc_time(
            self.hass,
            self._async_boundary,
            min(candidates),
        )

    async def _async_boundary(self, _scheduled_time: datetime) -> None:
        """Update time-dependent states immediately; fetch at UK midnight."""
        self._cancel_boundary = None
        # A timer can run late after a suspended host or a wall-clock change.
        # Use the actual time rather than the original scheduled boundary.
        now = dt_util.utcnow()
        today = now.astimezone(UK_TIMEZONE).date()
        rollover = today != self._clock_day
        self._clock_day = today
        self.data = self.data.prune(today, self.retention_days, now)
        if rollover:
            self.stale = True
        self._schedule_boundary()
        self.async_update_listeners()
        if rollover:
            await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Cancel the clock listener as well as the coordinator's polling."""
        if self._shutdown_requested:
            return
        if self._cancel_boundary:
            self._cancel_boundary()
            self._cancel_boundary = None
        await super().async_shutdown()
        async with self._store_lock:
            if self.data.last_success is not None:
                await self._store.async_save(self.data.as_dict())


type DfsConfigEntry = ConfigEntry[DfsCoordinator]

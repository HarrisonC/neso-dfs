"""Exercise entity setup, boundaries, cached operation, and cleanup in HA."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState

from custom_components.neso_dfs.api import FetchError
from custom_components.neso_dfs.calendar import DfsCalendar
from custom_components.neso_dfs.coordinator import storage_key
from custom_components.neso_dfs.models import parse_report

from .helpers import async_fire_time_changed, csv_text


async def test_entities_and_calendar_queries(hass, loaded_entry, mock_fetch):
    assert mock_fetch.call_count == 1
    assert len(hass.states.async_all("calendar")) == 1
    assert len(hass.states.async_all("binary_sensor")) == 3
    assert len(hass.states.async_all("sensor")) == 2
    calendar = DfsCalendar(loaded_entry.runtime_data)
    events = await calendar.async_get_events(
        hass,
        datetime(2026, 11, 6, 17, 20, tzinfo=UTC),
        datetime(2026, 11, 6, 17, 40, tzinfo=UTC),
    )
    assert len(events) == 1
    assert events[0].summary == "DFS: Reduce energy usage"
    description = events[0].description
    assert description is not None
    assert "\nZone: 6\nDirection: Downwards\n" in description
    assert "Window (UTC): 17:00–18:00" in description
    assert "17:00–17:30 · £200.125/MWh · Event 201 · Unit OCTO-1-Z6" in description
    assert "17:30–18:00 · £250.75/MWh · Event 202 · Unit OCTO-1-Z6" in description
    assert len(hass.states.async_all("calendar")[0].attributes["slots"]) == 2
    assert (
        await calendar.async_get_events(
            hass,
            datetime(2026, 11, 6, 18, tzinfo=UTC),
            datetime(2026, 11, 6, 19, tzinfo=UTC),
        )
        == []
    )
    assert (
        await calendar.async_get_events(
            hass,
            datetime(2026, 11, 6, 16, tzinfo=UTC),
            datetime(2026, 11, 6, 17, tzinfo=UTC),
        )
        == []
    )
    assert mock_fetch.call_count == 1


async def test_new_event_timestamp_survives_polling_and_restart(
    hass, loaded_entry, freezer, mock_fetch, report
):
    entity = next(
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_last_new_event_detected")
    )
    assert entity.state == "unknown"

    # A new NESO ID can be part of the same merged calendar event.
    new_slots = parse_report(csv_text({"Event ID": "203"})).slots
    updated_report = replace(report, slots=report.slots + new_slots)
    detected = "2026-11-06T16:55:00+00:00"
    freezer.move_to(detected)
    mock_fetch.return_value = updated_report
    await loaded_entry.runtime_data.async_refresh()
    assert len(loaded_entry.runtime_data.data.periods) == 1
    assert hass.states.get(entity.entity_id).state == detected

    # Withdraw the new ID before restarting. Its reappearance is already known.
    freezer.move_to("2026-11-06T17:10:00+00:00")
    mock_fetch.return_value = report
    await loaded_entry.runtime_data.async_refresh()
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    mock_fetch.return_value = updated_report
    freezer.move_to("2026-11-06T17:25:00+00:00")
    assert await hass.config_entries.async_setup(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity.entity_id).state == detected


async def test_exact_boundaries_without_network_poll(
    hass, loaded_entry, freezer, mock_fetch
):
    coordinator = loaded_entry.runtime_data
    coordinator.update_interval = None
    coordinator._async_unsub_refresh()
    for instant, expected in [
        ("2026-11-06T17:00:00+00:00", "on"),
        ("2026-11-06T17:30:00+00:00", "on"),
        ("2026-11-06T18:00:00+00:00", "off"),
    ]:
        freezer.move_to(instant)
        async_fire_time_changed(hass, datetime.fromisoformat(instant))
        await hass.async_block_till_done()
        down = next(
            s
            for s in hass.states.async_all("binary_sensor")
            if "downwards_active" in s.entity_id
        )
        assert down.state == expected
    assert mock_fetch.call_count == 1


async def test_outage_uses_cache_then_recovers(hass, loaded_entry, mock_fetch, freezer):
    coordinator = loaded_entry.runtime_data
    saved = coordinator.data
    mock_fetch.side_effect = FetchError("offline")
    freezer.move_to("2026-11-06T17:10:00+00:00")
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data == saved
    assert coordinator.stale
    assert coordinator.has_current_coverage
    down = next(
        s
        for s in hass.states.async_all("binary_sensor")
        if "downwards_active" in s.entity_id
    )
    assert down.state == "on" and down.attributes["data_stale"]
    stale = next(
        s for s in hass.states.async_all("binary_sensor") if "data_stale" in s.entity_id
    )
    assert stale.state == "on"
    mock_fetch.side_effect = None
    mock_fetch.return_value = parse_report(csv_text())
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.stale and coordinator.data.periods == ()
    assert hass.states.get(down.entity_id).state == "off"


@pytest.mark.parametrize("empty_cache", [False, True])
async def test_restart_restores_empty_or_nonempty_cache(
    hass, loaded_entry, mock_fetch, empty_cache
):
    if empty_cache:
        mock_fetch.return_value = parse_report(csv_text())
        await loaded_entry.runtime_data.async_refresh()
    original = loaded_entry.runtime_data.data
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    mock_fetch.side_effect = FetchError("offline")
    assert await hass.config_entries.async_setup(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data.data == original
    assert loaded_entry.runtime_data.stale
    assert len(hass.states.async_all("calendar")) == 1


async def test_interval_polls_once_for_all_entities(
    hass, loaded_entry, mock_fetch, freezer
):
    freezer.move_to("2026-11-06T17:00:01+00:00")
    async_fire_time_changed(hass, datetime(2026, 11, 6, 17, 0, 1, tzinfo=UTC))
    await hass.async_block_till_done()
    assert mock_fetch.call_count == 2
    assert not loaded_entry.runtime_data.stale


async def test_midnight_refresh_no_coverage_and_history(
    hass, loaded_entry, mock_fetch, freezer
):
    coordinator = loaded_entry.runtime_data
    coordinator.update_interval = None
    coordinator._async_unsub_refresh()
    mock_fetch.side_effect = FetchError("offline")
    freezer.move_to("2026-11-07T00:00:00+00:00")
    async_fire_time_changed(hass, datetime(2026, 11, 7, tzinfo=UTC))
    await hass.async_block_till_done()
    assert mock_fetch.call_count == 2
    assert not coordinator.has_current_coverage
    assert coordinator.data.periods
    down = next(
        s
        for s in hass.states.async_all("binary_sensor")
        if "downwards_active" in s.entity_id
    )
    assert down.state == "unavailable"
    assert hass.states.async_all("calendar")[0].state != "unavailable"


async def test_initial_outage_retries_setup(hass, entry, mock_fetch, freezer):
    freezer.move_to("2026-11-06T16:45:00+00:00")
    mock_fetch.side_effect = FetchError("offline")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_and_remove_cleanup(hass, loaded_entry, hass_storage):
    coordinator = loaded_entry.runtime_data
    assert coordinator._cancel_boundary is not None
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    assert coordinator._cancel_boundary is None
    assert coordinator._shutdown_requested
    assert all(s.state == "unavailable" for s in hass.states.async_all("calendar"))
    assert storage_key(loaded_entry.entry_id) in hass_storage
    await hass.config_entries.async_remove(loaded_entry.entry_id)
    assert storage_key(loaded_entry.entry_id) not in hass_storage
    assert not hass.states.async_all("calendar")


async def test_late_download_cannot_recreate_removed_store(
    hass, loaded_entry, mock_fetch, report, hass_storage
):
    started, finished = asyncio.Event(), asyncio.Event()

    async def delayed(_hass):
        started.set()
        await finished.wait()
        return report

    coordinator = loaded_entry.runtime_data
    mock_fetch.side_effect = delayed
    task = hass.async_create_task(coordinator.async_refresh())
    await started.wait()
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.config_entries.async_remove(loaded_entry.entry_id)
    finished.set()
    await task
    assert storage_key(loaded_entry.entry_id) not in hass_storage
    assert coordinator._cancel_boundary is None


async def test_storage_error_keeps_last_good_schedule(hass, loaded_entry, mock_fetch):
    coordinator = loaded_entry.runtime_data
    original = coordinator.data
    mock_fetch.return_value = parse_report(csv_text())
    with patch.object(coordinator._store, "async_save", AsyncMock(side_effect=OSError)):
        await coordinator.async_refresh()
    assert coordinator.data == original
    assert coordinator.stale and coordinator.has_current_coverage

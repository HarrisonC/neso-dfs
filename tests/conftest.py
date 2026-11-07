"""Fixtures using real Home Assistant with network access mocked."""

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.neso_dfs.const import DOMAIN
from custom_components.neso_dfs.models import parse_report

from .helpers import PARTICIPANT, MockConfigEntry, csv_text


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def report():
    return parse_report(
        csv_text(
            {},
            {
                "Event ID": "202",
                "From_Local": "17:30",
                "To_Local": "18:00",
                "From_UTC": "17:30",
                "To_UTC": "18:00",
                "Utilisation Price GBP per MWh": "250.75",
            },
        )
    )


@pytest.fixture
def mock_fetch(report):
    fetch = AsyncMock(return_value=report)
    with (
        patch("custom_components.neso_dfs.coordinator.async_fetch_report", fetch),
        patch("custom_components.neso_dfs.config_flow.async_fetch_report", fetch),
    ):
        yield fetch


@pytest.fixture
def entry():
    return MockConfigEntry(
        domain=DOMAIN,
        title="Octopus Zone 6",
        data={"participant": PARTICIPANT, "zone": 6},
        options={"poll_interval": 15, "retention_days": 30},
        unique_id=f"{PARTICIPANT.casefold()}|6",
    )


@pytest.fixture
async def loaded_entry(hass, entry, mock_fetch, freezer):
    freezer.move_to("2026-11-06T16:45:00+00:00")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry

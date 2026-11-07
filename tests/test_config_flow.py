"""Configuration and options through Home Assistant's actual flow manager."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.neso_dfs.api import FetchError
from custom_components.neso_dfs.const import DOMAIN
from custom_components.neso_dfs.models import parse_report

from .helpers import PARTICIPANT, csv_text

INPUT = {
    "participant": PARTICIPANT,
    "zone": 6,
    "poll_interval": 15,
    "retention_days": 30,
}


async def test_setup_discovers_historical_rejected_participants(hass, mock_fetch):
    mock_fetch.return_value = parse_report(
        csv_text({"Status": "Rejected", "Delivery Date": "01/05/2026"})
    )
    with patch(
        "custom_components.neso_dfs.async_setup_entry", AsyncMock(return_value=True)
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], INPUT
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"participant": PARTICIPANT, "zone": 6}
    assert result["options"] == {"poll_interval": 15, "retention_days": 30}
    assert result["result"].unique_id == f"{PARTICIPANT.casefold()}|6"
    assert mock_fetch.call_count == 1


async def test_duplicate_pair_aborts(hass, entry, mock_fetch):
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    "values",
    [
        {"zone": 0},
        {"zone": 1.5},
        {"poll_interval": 0},
        {"poll_interval": 1.5},
        {"retention_days": -1},
        {"retention_days": 1.5},
        {"participant": "UNKNOWN"},
    ],
)
async def test_invalid_configuration(hass, mock_fetch, values):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=INPUT | values
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]


async def test_connection_error_retry_and_empty_discovery(hass, mock_fetch):
    mock_fetch.side_effect = FetchError("offline")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    mock_fetch.side_effect = None
    mock_fetch.return_value = parse_report(csv_text())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "no_participants"}
    mock_fetch.return_value = parse_report(csv_text({}))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {}


async def test_options_update_reloads_and_preserves_history(
    hass, loaded_entry, mock_fetch
):
    original = loaded_entry.runtime_data.data.periods
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"poll_interval": 2, "retention_days": 0}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options == {"poll_interval": 2, "retention_days": 0}
    assert loaded_entry.runtime_data.update_interval.total_seconds() == 120
    assert loaded_entry.runtime_data.data.periods == original
    assert mock_fetch.call_count == 2


@pytest.mark.parametrize("interval", [0, 1.5])
async def test_invalid_options_do_not_reload(hass, loaded_entry, interval):
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    values = {"poll_interval": interval, "retention_days": 0}
    if interval == 0:
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(result["flow_id"], values)
    else:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], values
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_options"}
    assert loaded_entry.options["poll_interval"] == 15

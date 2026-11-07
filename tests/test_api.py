"""Real aiohttp client behavior against mocked remote responses."""

import pytest

from custom_components.neso_dfs.api import FetchError, async_fetch_report
from custom_components.neso_dfs.const import CSV_URL

from .helpers import csv_text


async def test_csv_download_handles_bom(hass, aioclient_mock):
    aioclient_mock.get(CSV_URL, text="\ufeff" + csv_text({}))
    report = await async_fetch_report(hass)
    assert len(report.slots) == 1
    assert aioclient_mock.call_count == 1


@pytest.mark.parametrize("status", [404, 429, 500])
async def test_http_errors(hass, aioclient_mock, status):
    aioclient_mock.get(CSV_URL, status=status)
    with pytest.raises(FetchError, match=f"HTTP {status}"):
        await async_fetch_report(hass)


async def test_bad_csv(hass, aioclient_mock):
    aioclient_mock.get(CSV_URL, text="<html>temporarily unavailable</html>")
    with pytest.raises(FetchError, match="required columns"):
        await async_fetch_report(hass)


async def test_timeout(hass, aioclient_mock):
    aioclient_mock.get(CSV_URL, exc=TimeoutError())
    with pytest.raises(FetchError, match="Could not download"):
        await async_fetch_report(hass)

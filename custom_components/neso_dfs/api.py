"""Download the NESO report using Home Assistant's shared HTTP session."""

import asyncio

from aiohttp import ClientError, ClientResponseError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CSV_URL, REQUEST_TIMEOUT
from .models import Report, ReportError, parse_report


class FetchError(Exception):
    """The remote report could not be read or validated."""


async def async_fetch_report(hass: HomeAssistant) -> Report:
    """Download once, following NESO's redirect to the actual CSV file."""
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with async_get_clientsession(hass).get(
                CSV_URL, allow_redirects=True
            ) as response:
                response.raise_for_status()
                text = await response.text(encoding="utf-8-sig")
        return await hass.async_add_executor_job(parse_report, text)
    except ClientResponseError as err:
        raise FetchError(f"NESO returned HTTP {err.status}") from err
    except (ClientError, TimeoutError, UnicodeError) as err:
        raise FetchError("Could not download the NESO CSV") from err
    except ReportError as err:
        raise FetchError(str(err)) from err

"""Test data and compatibility imports for the two supported HA releases."""

import csv
from io import StringIO

try:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_fire_time_changed,
    )
except ImportError:
    from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

__all__ = ["MockConfigEntry", "async_fire_time_changed", "csv_text", "PARTICIPANT"]

PARTICIPANT = "OCTOPUS ENERGY LIMITED"
BASE_ROW = {
    "Event ID": "201",
    "Event Type": "Downwards",
    "Event Tag": "Energy",
    "Delivery Date": "06/11/2026",
    "From_Local": "17:00",
    "To_Local": "17:30",
    "From_UTC": "17:00",
    "To_UTC": "17:30",
    "Registered DFS Participant": PARTICIPANT,
    "DFS Unit ID": "OCTO-1-Z6",
    "Zone": "6",
    "DFS Procured MW": "1",
    "Service Requirement Type": "Live",
    "Utilisation Price GBP per MWh": "200.125",
    "Status": "Accepted",
}


def csv_text(*overrides: dict[str, str]) -> str:
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(BASE_ROW))
    writer.writeheader()
    writer.writerows(BASE_ROW | row for row in overrides)
    return buffer.getvalue()

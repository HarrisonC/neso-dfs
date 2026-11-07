"""Behavioral tests for report filtering, time resolution and reconciliation."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.neso_dfs.models import (
    ReportError,
    Schedule,
    merge_slots,
    parse_report,
    whole_number,
)

from .helpers import PARTICIPANT, csv_text

NOW = datetime(2026, 11, 6, 16, 45, tzinfo=UTC)


def schedule(text, now=NOW, previous=None, retention=30):
    return (previous or Schedule()).apply(
        parse_report(text), PARTICIPANT, 6, now, retention
    )


def test_new_event_detection_uses_saved_baseline_and_delivery_date():
    initial = schedule(csv_text({}))
    old_cache = initial.as_dict()
    del old_cache["last_new_event_detected"]
    del old_cache["seen_event_ids"]
    restored = Schedule.from_dict(old_cache)

    correction = csv_text({"Utilisation Price GBP per MWh": "300"})
    corrected = schedule(correction, NOW + timedelta(minutes=15), restored)
    assert corrected.last_new_event_detected is None

    tomorrow = NOW + timedelta(days=1)
    new_day = schedule(csv_text({"Delivery Date": "07/11/2026"}), tomorrow, corrected)
    assert new_day.last_new_event_detected == tomorrow
    assert Schedule.from_dict(new_day.as_dict()) == new_day


def test_filter_today_participant_zone_status_and_service():
    report = parse_report(
        csv_text(
            {},
            {"Registered DFS Participant": "OTHER PROVIDER"},
            {"Zone": "7"},
            {"Status": "Rejected"},
            {"Status": "Declined"},
            {"Service Requirement Type": "Test"},
            {"Delivery Date": "07/11/2026"},
            {"Delivery Date": "05/11/2026"},
        )
    )
    assert "OTHER PROVIDER" in report.participants
    result = Schedule().apply(report, "  octopus energy limited ", 6, NOW, 30)
    assert len(result.periods) == 1
    assert len(result.periods[0].slots) == 1
    assert result.coverage_date == NOW.date()


def test_adjacent_and_overlapping_slots_merge_across_event_ids(report):
    periods = merge_slots(report.slots + report.slots)
    assert len(periods) == 1
    period = periods[0]
    assert period.start.hour == 17 and period.end.hour == 18
    assert len(period.slots) == 2
    assert period.attributes()["source_event_ids"] == ["201", "202"]
    assert [s.price for s in period.slots] == [Decimal("200.125"), Decimal("250.75")]
    overlap = parse_report(csv_text({"From_UTC": "17:15", "From_Local": "17:15"})).slots
    assert len(merge_slots((*report.slots, *overlap))) == 1


def test_rejected_gap_and_opposite_direction_stay_separate():
    result = schedule(
        csv_text(
            {},
            {
                "From_UTC": "17:30",
                "From_Local": "17:30",
                "To_UTC": "18:00",
                "To_Local": "18:00",
                "Status": "Rejected",
            },
            {
                "From_UTC": "18:00",
                "From_Local": "18:00",
                "To_UTC": "18:30",
                "To_Local": "18:30",
            },
            {"Event Type": "Upwards"},
        )
    )
    assert len(result.periods) == 3
    assert sum(p.first.direction == "Downwards" for p in result.periods) == 2


@pytest.mark.parametrize(
    (
        "day",
        "utc_start",
        "utc_end",
        "local_start",
        "local_end",
        "expected_start",
        "expected_end",
    ),
    [
        (
            "01/07/2026",
            "23:30",
            "00:00",
            "00:30",
            "01:00",
            "2026-06-30T23:30:00+00:00",
            "2026-07-01T00:00:00+00:00",
        ),
        (
            "01/07/2026",
            "22:30",
            "23:30",
            "23:30",
            "00:30",
            "2026-07-01T22:30:00+00:00",
            "2026-07-01T23:30:00+00:00",
        ),
        (
            "29/03/2026",
            "00:30",
            "01:30",
            "00:30",
            "02:30",
            "2026-03-29T00:30:00+00:00",
            "2026-03-29T01:30:00+00:00",
        ),
        (
            "25/10/2026",
            "00:30",
            "01:30",
            "01:30",
            "01:30",
            "2026-10-25T00:30:00+00:00",
            "2026-10-25T01:30:00+00:00",
        ),
        (
            "06/11/2026",
            "23:30",
            "00:30",
            "23:30",
            "00:30",
            "2026-11-06T23:30:00+00:00",
            "2026-11-07T00:30:00+00:00",
        ),
    ],
)
def test_uk_midnight_and_dst(
    day, utc_start, utc_end, local_start, local_end, expected_start, expected_end
):
    (slot,) = parse_report(
        csv_text(
            {
                "Delivery Date": day,
                "From_UTC": utc_start,
                "To_UTC": utc_end,
                "From_Local": local_start,
                "To_Local": local_end,
            }
        )
    ).slots
    assert slot.start.isoformat() == expected_start
    assert slot.end.isoformat() == expected_end


def test_latest_report_can_split_remove_or_shorten():
    rows = [
        {},
        {
            "From_UTC": "17:30",
            "From_Local": "17:30",
            "To_UTC": "18:00",
            "To_Local": "18:00",
        },
        {
            "From_UTC": "18:00",
            "From_Local": "18:00",
            "To_UTC": "18:30",
            "To_Local": "18:30",
        },
    ]
    initial = schedule(csv_text(*rows))
    split = schedule(
        csv_text(rows[0], rows[1] | {"Status": "Rejected"}, rows[2]), previous=initial
    )
    assert len(split.periods) == 2
    assert split.periods[0].end.hour == 17 and split.periods[0].end.minute == 30
    assert schedule(csv_text(rows[0]), previous=initial).periods[0].end.minute == 30
    assert schedule(csv_text(), previous=initial).periods == ()


def test_history_retention_no_backfill_and_round_trip(report):
    initial = Schedule().apply(report, PARTICIPANT, 6, NOW, 30)
    assert Schedule.from_dict(initial.as_dict()) == initial
    next_day = initial.apply(
        parse_report(csv_text()), PARTICIPANT, 6, NOW + timedelta(days=1), 30
    )
    assert next_day.periods == initial.periods
    assert schedule(csv_text(), NOW + timedelta(days=31), next_day).periods == ()
    assert (
        schedule(csv_text(), NOW + timedelta(days=1), initial, retention=0).periods
        == ()
    )
    assert schedule(csv_text({"Delivery Date": "05/11/2026"})).periods == ()


def test_ongoing_overnight_retained_even_with_zero_history():
    initial = schedule(
        csv_text(
            {
                "From_UTC": "23:30",
                "From_Local": "23:30",
                "To_UTC": "00:30",
                "To_Local": "00:30",
            }
        )
    )
    tomorrow = datetime(2026, 11, 7, 0, 10, tzinfo=UTC)
    result = schedule(csv_text(), tomorrow, initial, retention=0)
    assert result.current_or_next(tomorrow) is not None
    assert (
        result.prune(date(2026, 11, 7), 0, tomorrow + timedelta(hours=1)).periods == ()
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "<html>unavailable</html>",
        "Event Type,Status\nDownwards,Accepted\n",
        csv_text({"From_Local": "19:00"}),
        csv_text({"Delivery Date": "invalid"}),
        csv_text({"Zone": "6.5"}),
        csv_text({"Utilisation Price GBP per MWh": "NaN"}),
        csv_text({"Event Type": "Unknown"}),
        csv_text({"From_UTC": "17:30"}),
        csv_text({})[:-6],
        csv_text({}) + '"unterminated',
    ],
)
def test_malformed_reports_rejected(text):
    with pytest.raises(ReportError):
        parse_report(text)


@pytest.mark.parametrize("value", [True, -1, 1.2, "nan", float("inf"), None])
def test_whole_number_validation(value):
    with pytest.raises(ValueError):
        whole_number(value)


def test_uid_survives_extension_and_price_changes(report):
    original = merge_slots((report.slots[0],))[0]
    extended = merge_slots(report.slots)[0]
    corrected = schedule(csv_text({"Utilisation Price GBP per MWh": "350"})).periods[0]
    # Report slots are deliberately unordered: choose the earliest original.
    earliest = min(report.slots, key=lambda slot: slot.start)
    assert merge_slots((earliest,))[0].uid == extended.uid == corrected.uid
    assert original.uid

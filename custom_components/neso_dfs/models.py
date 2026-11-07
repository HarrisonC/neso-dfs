"""CSV parsing and schedule operations, independent of Home Assistant."""

import csv
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
from typing import Any

from .const import DIRECTIONS, UK_TIMEZONE

REQUIRED_COLUMNS = {
    "Event Type",
    "Delivery Date",
    "From_UTC",
    "To_UTC",
    "From_Local",
    "To_Local",
    "Registered DFS Participant",
    "Zone",
    "Utilisation Price GBP per MWh",
    "Status",
    "Service Requirement Type",
    "Event ID",
    "DFS Unit ID",
}


class ReportError(ValueError):
    """The report is invalid and must not replace the saved schedule."""


def participant_key(value: str) -> str:
    """Normalize a participant without accepting partial name matches."""
    return " ".join(value.split()).casefold()


def whole_number(value: Any, minimum: int = 0) -> int:
    """Validate selector numbers without silently rounding them."""
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError
        result = int(number)
        if isinstance(value, bool) or result < minimum:
            raise ValueError
    except (InvalidOperation, ValueError, TypeError, OverflowError) as err:
        raise ValueError(f"Expected a whole number of at least {minimum}") from err
    return result


def _clock(value: str) -> time:
    clock = time.fromisoformat(value)
    if clock.tzinfo is not None:
        raise ValueError("Clock must not contain a timezone")
    return clock


def _window(row: dict[str, str], day: date) -> tuple[datetime, datetime]:
    """Resolve UTC dates using the UK delivery date and local clock columns.

    At 00:30 BST the UTC date is the previous day. During the autumn clock
    change, the UTC column disambiguates the two occurrences of 01:30.
    """
    from_utc, to_utc = _clock(row["From_UTC"]), _clock(row["To_UTC"])
    from_local, to_local = _clock(row["From_Local"]), _clock(row["To_Local"])
    if from_utc == to_utc and from_local == to_local:
        raise ValueError("Ambiguous zero-length or full-day window")
    starts = [
        instant
        for offset in (-1, 0, 1)
        if (instant := datetime.combine(day + timedelta(days=offset), from_utc, UTC))
        .astimezone(UK_TIMEZONE)
        .date()
        == day
        and instant.astimezone(UK_TIMEZONE).time() == from_local
    ]
    if len(starts) != 1:
        raise ValueError("UTC/local start does not match the UK delivery date")
    start = starts[0]
    for offset in (0, 1, 2):
        end = datetime.combine(start.date() + timedelta(days=offset), to_utc, UTC)
        local_end = end.astimezone(UK_TIMEZONE)
        if (
            start < end <= start + timedelta(hours=25)
            and local_end.date() in (day, day + timedelta(days=1))
            and local_end.time() == to_local
        ):
            return start, end
    raise ValueError("UTC/local end does not form a valid window")


@dataclass(frozen=True)
class Slot:
    """An accepted live bid, including its original price and provenance."""

    delivery_date: date
    participant: str
    zone: int
    direction: str
    start: datetime
    end: datetime
    price: Decimal
    event_id: str
    unit_id: str

    def as_dict(self) -> dict[str, Any]:
        """Serialize without losing decimal precision."""
        return {
            "delivery_date": self.delivery_date.isoformat(),
            "participant": self.participant,
            "zone": self.zone,
            "direction": self.direction,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "price_gbp_per_mwh": str(self.price),
            "event_id": self.event_id,
            "unit_id": self.unit_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Slot:
        """Validate a stored slot before trusting it for automation."""
        start, end = (
            datetime.fromisoformat(data["start"]),
            datetime.fromisoformat(data["end"]),
        )
        price = Decimal(data["price_gbp_per_mwh"])
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or start >= end
            or data["direction"] not in DIRECTIONS
            or not price.is_finite()
            or not isinstance(data["participant"], str)
            or not data["participant"].strip()
        ):
            raise ValueError("Invalid stored slot")
        return cls(
            date.fromisoformat(data["delivery_date"]),
            data["participant"],
            whole_number(data["zone"], 1),
            data["direction"],
            start.astimezone(UTC),
            end.astimezone(UTC),
            price,
            str(data["event_id"]),
            str(data["unit_id"]),
        )


@dataclass(frozen=True)
class Report:
    """Participant discovery uses all rows; slots contain accepted live rows."""

    participants: tuple[str, ...]
    slots: tuple[Slot, ...]


def parse_report(text: str) -> Report:
    """Parse a complete CSV; any invalid row leaves the last good cache intact."""
    try:
        reader = csv.DictReader(
            StringIO(text.lstrip("\ufeff"), newline=""), strict=True
        )
        if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
            raise ReportError("CSV is missing required columns")
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ReportError("CSV contains duplicate column names")
        participants: dict[str, str] = {}
        slots: set[Slot] = set()
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ReportError(f"Incomplete CSV row at line {reader.line_num}")
            row = {key: value.strip() for key, value in row.items()}
            participant = row["Registered DFS Participant"]
            if not participant:
                raise ReportError(f"Missing participant at line {reader.line_num}")
            participants.setdefault(participant_key(participant), participant)
            status = row["Status"].casefold()
            service_type = row["Service Requirement Type"].casefold()
            if status not in {"accepted", "rejected", "declined"}:
                raise ReportError(f"Unknown bid status at line {reader.line_num}")
            if service_type not in {"live", "test"}:
                raise ReportError(f"Unknown service type at line {reader.line_num}")
            if status != "accepted" or service_type != "live":
                continue
            direction = row["Event Type"].capitalize()
            if direction not in DIRECTIONS:
                raise ReportError(f"Unknown event direction at line {reader.line_num}")
            day = datetime.strptime(row["Delivery Date"], "%d/%m/%Y").date()
            start, end = _window(row, day)
            price = Decimal(row["Utilisation Price GBP per MWh"])
            if not price.is_finite():
                raise ReportError(f"Invalid price at line {reader.line_num}")
            slots.add(
                Slot(
                    day,
                    participant,
                    whole_number(row["Zone"], 1),
                    direction,
                    start,
                    end,
                    price,
                    row["Event ID"],
                    row["DFS Unit ID"],
                )
            )
    except (csv.Error, ValueError, InvalidOperation) as err:
        if isinstance(err, ReportError):
            raise
        raise ReportError(f"Invalid CSV data: {err}") from err
    return Report(tuple(sorted(participants.values(), key=str.casefold)), tuple(slots))


def _slot_key(slot: Slot) -> tuple:
    return (
        slot.delivery_date,
        participant_key(slot.participant),
        slot.zone,
        slot.direction,
        slot.start,
        slot.end,
        slot.unit_id,
        slot.price,
        slot.event_id,
    )


@dataclass(frozen=True)
class Period:
    """A continuous period of accepted slots of a single direction."""

    slots: tuple[Slot, ...]

    @property
    def start(self) -> datetime:
        return min(slot.start for slot in self.slots)

    @property
    def end(self) -> datetime:
        return max(slot.end for slot in self.slots)

    @property
    def first(self) -> Slot:
        return self.slots[0]

    @property
    def uid(self) -> str:
        """Keep the UID stable across end extensions and price corrections."""
        key = (
            f"{participant_key(self.first.participant)}|{self.first.zone}|"
            f"{self.first.delivery_date}|{self.first.direction}|{self.start.isoformat()}"
        )
        return sha256(key.encode()).hexdigest()[:32]

    def attributes(self) -> dict[str, Any]:
        """Expose machine-readable prices for every source bid."""
        return {
            "uid": self.uid,
            "participant": self.first.participant,
            "zone": self.first.zone,
            "direction": self.first.direction,
            "delivery_date": self.first.delivery_date.isoformat(),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "source_event_ids": sorted({slot.event_id for slot in self.slots}),
            "slots": [slot.as_dict() for slot in self.slots],
        }


def merge_slots(slots: tuple[Slot, ...] | list[Slot]) -> tuple[Period, ...]:
    """Union duplicate, overlapping, and touching windows within each group."""
    result: list[Period] = []
    current: list[Slot] = []
    group: tuple | None = None
    end: datetime | None = None
    for slot in sorted(set(slots), key=_slot_key):
        key = _slot_key(slot)[:4]
        if current and (key != group or slot.start > end):
            result.append(Period(tuple(current)))
            current = []
        current.append(slot)
        end = max(item.end for item in current)
        group = key
    if current:
        result.append(Period(tuple(current)))
    return tuple(
        sorted(
            result,
            key=lambda period: (period.start, period.first.direction, period.uid),
        )
    )


@dataclass(frozen=True)
class Schedule:
    """Today's authoritative schedule and previously observed history."""

    periods: tuple[Period, ...] = ()
    coverage_date: date | None = None
    last_success: datetime | None = None
    last_new_event_detected: datetime | None = None
    seen_event_ids: frozenset[str] = frozenset()

    def prune(self, today: date, retention_days: int, now: datetime) -> Schedule:
        cutoff = today - timedelta(days=retention_days)
        return replace(
            self,
            periods=tuple(
                period
                for period in self.periods
                if period.first.delivery_date <= today
                and (period.first.delivery_date >= cutoff or period.end > now)
            ),
        )

    def apply(
        self,
        report: Report,
        participant: str,
        zone: int,
        now: datetime,
        retention_days: int,
    ) -> Schedule:
        today = now.astimezone(UK_TIMEZONE).date()
        history = tuple(
            p
            for p in self.prune(today, retention_days, now).periods
            if p.first.delivery_date < today
        )
        slots = tuple(
            slot
            for slot in report.slots
            if slot.delivery_date == today
            and slot.zone == zone
            and participant_key(slot.participant) == participant_key(participant)
        )
        periods = tuple(
            sorted((*history, *merge_slots(slots)), key=lambda p: (p.start, p.uid))
        )
        event_ids = {slot.event_id for slot in slots if slot.event_id}
        seen = self.seen_event_ids if self.coverage_date == today else frozenset()
        # The first successful download establishes a baseline. Source IDs
        # survive changes to prices, slots, and merged calendar boundaries.
        detected = self.last_new_event_detected
        if self.coverage_date is not None and event_ids - seen:
            detected = now
        return Schedule(periods, today, now, detected, seen | event_ids)

    def current_or_next(
        self, now: datetime, direction: str | None = None
    ) -> Period | None:
        return next(
            (
                period
                for period in self.periods
                if period.end > now
                and (direction is None or period.first.direction == direction)
            ),
            None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage_date": self.coverage_date.isoformat()
            if self.coverage_date
            else None,
            "last_success": self.last_success.isoformat()
            if self.last_success
            else None,
            "last_new_event_detected": self.last_new_event_detected.isoformat()
            if self.last_new_event_detected
            else None,
            "seen_event_ids": sorted(self.seen_event_ids),
            "slots": [
                slot.as_dict() for period in self.periods for slot in period.slots
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Schedule:
        coverage = (
            date.fromisoformat(data["coverage_date"]) if data["coverage_date"] else None
        )
        last = (
            datetime.fromisoformat(data["last_success"])
            if data["last_success"]
            else None
        )
        if (last is None) != (coverage is None) or (last and last.tzinfo is None):
            raise ValueError("Invalid cache coverage")
        slots = tuple(Slot.from_dict(item) for item in data["slots"])
        if coverage and (
            last.astimezone(UK_TIMEZONE).date() != coverage
            or any(s.delivery_date > coverage for s in slots)
        ):
            raise ValueError("Invalid cached delivery dates")
        detected_text = data.get("last_new_event_detected")
        detected = datetime.fromisoformat(detected_text) if detected_text else None
        if detected and (detected.tzinfo is None or coverage is None):
            raise ValueError("Invalid cached detection timestamp")
        saved_ids = data.get("seen_event_ids", [])
        if not isinstance(saved_ids, list) or any(
            not isinstance(event_id, str) for event_id in saved_ids
        ):
            raise ValueError("Invalid cached event IDs")
        # Older caches already contain the slots from the most recent poll.
        # Use them as the baseline when upgrading an existing installation.
        seen = frozenset(saved_ids) | {
            slot.event_id
            for slot in slots
            if slot.delivery_date == coverage and slot.event_id
        }
        return cls(merge_slots(slots), coverage, last, detected, seen)

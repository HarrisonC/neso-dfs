# NESO Demand Flexibility for Home Assistant

Track accepted NESO Demand Flexibility Service (DFS) events for an energy
participant and a numeric NESO zone. The integration creates a read-only calendar,
separate reduction/increase active sensors, and freshness diagnostics.

Requires **Home Assistant Core 2026.10.4 or later**. Tested against 2026.10.4 and
2026.11.0. No API key or additional runtime package is required.

## Installation

1. Copy the `custom_components/neso_dfs` directory from this repository into your
   Home Assistant configuration directory, under `custom_components/neso_dfs`.
   The resulting path should include `custom_components/neso_dfs/manifest.json`.
2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration** and search for
   **NESO Demand Flexibility**.
4. Select your registered participant name and enter your **NESO DFS zone
   number**. This is unrelated to Home Assistant's home/location zones. The
   current report uses zones 1–12.
5. Choose the polling interval and history retention. Defaults are **15 minutes**
   and **30 completed UK delivery days**, plus today.

To upgrade an existing installation, replace the component files and restart
Home Assistant. The integration reads your existing entries and saved schedule.

Each participant/zone pair can be added once. Add another integration entry to
track another pair. To change a participant or zone, remove its entry and add a
new one. Removal deletes that entry's saved history. Use the integration's
**Configure** button to change polling and retention without losing history.
Polling accepts positive whole minutes; retention accepts nonnegative whole days.
Retention `0` keeps today and any previous-day event that has not yet ended.

The repository includes `hacs.json` and the standard custom-component layout for
installation as a custom repository after it is hosted on GitHub. It has not been
submitted to a public integration catalog.

## Data and events

The integration follows redirects from the
[NESO utilisation report CSV](https://api.neso.energy/dataset/829e325f-8926-40ab-9dd6-8b7aea6cb244/resource/3ebf77d7-05df-466e-a023-dc45a90efeea/download/dfs-utilisation-report.csv).
Participant discovery considers the entire report, so your participant need not
have an event today to be configured.

Only **Accepted**, **Live** rows matching the participant and zone for the
**current Europe/London delivery day** enter the schedule. `Test`, `Rejected`,
and `Declined` rows are excluded. Future delivery dates are ignored until their
day begins. History starts when the integration first runs; historical rows in
the download are not backfilled.

- **Downwards** means reduce energy usage.
- **Upwards** means increase energy usage.
- Duplicate rows are removed. Overlapping or exactly adjacent accepted slots on
  the same delivery day and in the same direction become one event, even across
  NESO Event IDs or different prices. Gaps and opposite directions stay separate.
- Each successful refresh replaces today's schedule. Changed prices, removals,
  and rejected bids update, shorten, split, or remove the corresponding events.
  Completed delivery days remain as last observed until retention expires.
- Dates use the CSV's `DD/MM/YYYY` format. The local columns establish the UK
  delivery date; the UTC columns disambiguate summer time and repeated autumn
  clock times. Stored timestamps are UTC; the calendar displays HA's timezone.
- The calendar includes retained history. A previous-day event still in progress
  remains active until its actual end, subject to current-day coverage below.

The calendar event description lists the delivery window and each accepted bid.

```text
Participant: OCTOPUS ENERGY LIMITED
Zone: 6
Direction: Downwards
Delivery date: 06 Nov 2026
Window (UTC): 17:00–18:00

Accepted live slots (UTC)
• 17:00–17:30 · £200.125/MWh · Event 201 · Unit OCTO-1-Z6
• 17:30–18:00 · £250.75/MWh · Event 202 · Unit OCTO-1-Z6
```

For automations, the calendar and active sensors expose the current or next
event's `slots` and `source_event_ids` attributes. Each slot includes UTC
`start` and `end`, the `price_gbp_per_mwh` as a decimal string, `event_id`, and
`unit_id`. These are the utilisation bid prices for each source slot.

## Entities and freshness

Each entry provides six entities.

| Entity | Use |
| --- | --- |
| Events calendar | Calendar dashboard, calendar card, `calendar.get_events`, calendar triggers |
| Downwards active | `on` during a reduction event |
| Upwards active | `on` during an increase event |
| Data stale | Diagnostic: `on` after a failed download or while restored data awaits verification |
| Last successful update | Diagnostic: timestamp of the latest valid CSV refresh |
| Last new event detected | Diagnostic: timestamp of the first poll that saw the latest new matching NESO event ID |

**Last new event detected** tracks new Accepted Live event IDs for your
participant, zone, and current UK delivery day. Repeat polls, changes to an
existing ID, and reappearance of an ID already seen that day leave the sensor's
value unchanged. The timestamp survives restarts. The first successful poll on
a fresh installation establishes a baseline, and the sensor shows `unknown`
until a later poll detects a new event. On upgrade, the saved schedule provides
the baseline when available.

The NESO report contains delivery dates and times. It has no per-event
publication timestamp, so the sensor shows when Home Assistant first detected
the event. If NESO lists a future event early, it becomes eligible for detection
when its UK delivery day begins.

Active sensors expose their current or next event's details. `data_stale` and
`last_successful_update` attributes identify the schedule's freshness. The stale
diagnostic also exposes `last_error` and `coverage_date`. Actual entity IDs depend
on your participant, zone, and any renaming in HA.

One coordinator per entry fetches the report, so calendar queries and entity
updates share the download. A manual `homeassistant.update_entity` action on an
integration entity requests a refresh. Starts and ends use local timers and do
not wait for the polling interval; a newly discovered event already in progress
activates its sensor immediately.

The validated schedule and timestamp are saved using Home Assistant's `.storage`
helper, independently of Recorder. They survive restarts. Download, timeout, or
CSV validation failures retain the last good schedule, set the stale diagnostic,
and retry at the normal polling interval. The initial installation retries setup
if no valid report or saved schedule is available.

At UK midnight the integration prunes history and refreshes the report. If there
is no successfully fetched schedule for the new day, active sensors are
**unavailable** until a fetch succeeds; yesterday's download cannot establish
whether today has new events. The calendar still serves retained history.
A valid report containing no matches establishes an empty schedule and turns
active sensors `off`. Restore followed by a failed fetch keeps a valid saved
current-day schedule available with `Data stale` on.

## Automation examples

Replace the entity IDs with those from your installation. These examples create
Home Assistant notifications; change the actions to suit your devices.

React to a reduction event, including one discovered after its start:

```yaml
alias: DFS reduction active
triggers:
  - trigger: state
    entity_id: binary_sensor.neso_demand_flexibility_octopus_energy_limited_zone_6_downwards_active
    to: "on"
actions:
  - action: persistent_notification.create
    data:
      title: DFS reduction event
      message: >-
        Reduce usage until
        {{ state_attr(trigger.entity_id, 'end') | as_datetime | as_local }}.
mode: restart
```

Prepare before a calendar event:

```yaml
alias: DFS prepare 15 minutes before
triggers:
  - trigger: calendar
    entity_id: calendar.neso_demand_flexibility_octopus_energy_limited_zone_6_events
    event: start
    offset: "-00:15:00"
actions:
  - action: persistent_notification.create
    data:
      title: DFS event approaching
      message: >-
        {{ trigger.calendar_event.summary }} at
        {{ trigger.calendar_event.start }}.
mode: queued
```

Home Assistant reads calendar triggers every 15 minutes, so events announced or
changed shortly before their start may miss a calendar trigger. A shorter CSV
polling interval does not remove this calendar-trigger limitation. Use the
active sensors when reacting to the current period is what matters. See
[Home Assistant calendar automation guidance](https://www.home-assistant.io/integrations/calendar/#automation).

## Development

Use Python **3.14.2+**, matching the supported HA releases. All automated tests mock
network access; no Home Assistant instance or NESO account is needed.

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements-test-min.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .

uv venv --python 3.14 .venv-current
uv pip install --python .venv-current/bin/python -r requirements-test-current.txt
.venv-current/bin/python -m pytest -p pytest_homeassistant
```

The suites cover the CSV client, parsing, merging and prices, UK midnight and DST,
retention, report corrections, persistence/restarts, config and options flows,
entity updates, calendar ranges, outages, and unload/removal cleanup.

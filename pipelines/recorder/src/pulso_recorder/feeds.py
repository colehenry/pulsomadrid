"""Turn one Renfe GTFS-RT publication into rows.

Everything here is pure: payload in, rows out, no clients and no clock. That is what
makes it testable against the committed fixtures, which are real bytes read from the
live endpoints on 2026-09-01 rather than anything hand-written.

Shape of the sources, read from the feeds and not from documentation:

    vehicle_positions.json
      {"header": {"timestamp": "1788233450"},
       "entity": [{"id": "VP_C1-23501",
                   "vehicle": {"trip": {"tripId": "3042M23501C1"},
                               "position": {"latitude": 37.48, "longitude": -5.93},
                               "currentStatus": "INCOMING_AT",
                               "timestamp": "1788233448",
                               "stopId": "50703",
                               "vehicle": {"id": "23501", "label": "C1-23501-PLATF.(1)"}}}]}

    trip_updates.json
      {"header": {"timestamp": "1788233450"},
       "entity": [{"id": "TUUPDATE_3042M23501C1",
                   "tripUpdate": {"trip": {"tripId": "…", "scheduleRelationship": "SCHEDULED"},
                                  "stopTimeUpdate": [{"arrival": {"delay": 60, "time": "…"},
                                                      "stopId": "50704"}],
                                  "vehicle": {"wheelchairAccessible": "…"},
                                  "delay": 60}}]}

    alerts.json
      {"header": {"timestamp": "1788233574"},
       "entity": [{"id": "AVISO_510970",
                   "alert": {"activePeriod": [{"start": "1788206280"}],
                             "informedEntity": [{"routeId": "10T0031C2"}, …],
                             "descriptionText": {"translation": [{"text": "…",
                                                                  "language": "es"}]}}}]}

Anomalies go to ops.rejected_rows, but the observation itself is still written. That is
a deliberate departure from the static pipeline, where a bad row is refused outright.
The static feed can be downloaded again; this one cannot. Losing an observation because
an accessibility enum was unfamiliar would destroy a fact we can never re-collect, so
the row is kept, the oddity is recorded, and the audit has something to find.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import MADRID_NUCLEO, SERVICE_DAY_CUTOVER_HOUR, TIMEZONE

MADRID = ZoneInfo(TIMEZONE)

# 'C5-19507-PLATF.(4)' -> line, train number, platform. Matched 3,877 of 3,877 Madrid
# labels; anything else is recorded as an anomaly and leaves platform_number NULL.
LABEL_RE = re.compile(r"^(?P<line>.+)-(?P<train>\d+)-PLATF\.\((?P<platform>[^)]*)\)$")

# GTFS defines UNKNOWN_ACCESSIBILITY as well. Renfe has not been seen to send it, and
# mapping it to NULL would make "the operator says unknown" indistinguishable from "the
# operator said nothing", so an unmapped value is reported rather than coerced.
WHEELCHAIR = {"WHEELCHAIR_ACCESSIBLE": True, "WHEELCHAIR_INACCESSIBLE": False}


@dataclass(frozen=True)
class Trip:
    """One scheduled Madrid trip, as the recorder needs it."""
    train_number: str
    line_id: str
    service_date: date
    scheduled_departure: datetime | None = None
    scheduled_arrival: datetime | None = None


@dataclass(frozen=True)
class Anomaly:
    """Something unexpected in a row we kept anyway. Lands in ops.rejected_rows."""
    source_file: str
    raw_row: str
    reason: str


def header_timestamp(payload: dict[str, Any]) -> datetime | None:
    """The publication instant. None if the feed did not send one."""
    raw = (payload.get("header") or {}).get("timestamp")
    if raw is None:
        return None
    return datetime.fromtimestamp(int(raw), UTC)


def _ts(value: Any) -> datetime | None:
    return None if value in (None, "") else datetime.fromtimestamp(int(value), UTC)


def service_date_for(observed_at: datetime) -> date:
    """Madrid service date of an observation that has no trip to take one from.

    The day is cut at 03:00 rather than midnight, so an alert seen at 00:30 belongs to
    the night still running. Cercanias schedules nothing at all in hours 02 and 03, so
    any cut inside that window gives identical answers.
    """
    local = observed_at.astimezone(MADRID)
    return (local - timedelta(hours=SERVICE_DAY_CUTOVER_HOUR)).date()


def parse_platform(label: str | None) -> str | None:
    m = LABEL_RE.match(label or "")
    return m.group("platform") if m else None


def parse_trains(
    vehicles: dict[str, Any] | None,
    updates: dict[str, Any] | None,
    trips: dict[str, Trip],
    stations: set[str],
    observed_at: datetime,
) -> tuple[list[dict[str, Any]], list[Anomaly]]:
    """One row per Madrid train for a single publication of both vehicle feeds.

    The two feeds are merged on trip_id under one observed_at, which they share: every
    sampled publication carried a byte-identical header timestamp in both. A trip present
    in only one of them yields a row with the other side's columns NULL -- in every
    observed case that meant a CANCELED trip, which has no vehicle entity at all.
    """
    rows: dict[str, dict[str, Any]] = {}
    anomalies: list[Anomaly] = []

    for entity in (vehicles or {}).get("entity") or []:
        v = entity.get("vehicle") or {}
        trip_id = str((v.get("trip") or {}).get("tripId") or "")
        trip = trips.get(trip_id)
        if trip is None:
            continue  # not a Madrid trip we hold: the national feed, filtered by join
        position = v.get("position") or {}
        label = (v.get("vehicle") or {}).get("label")
        platform = parse_platform(label)
        if label and platform is None:
            anomalies.append(Anomaly(
                "vehicle_positions.json", json.dumps(entity),
                f"vehicle.label {label!r} does not match the '{{line}}-{{train}}-PLATF.(n)' form"))
        station_id = v.get("stopId")
        if station_id and station_id not in stations:
            anomalies.append(Anomaly(
                "vehicle_positions.json", json.dumps(entity),
                f"stopId {station_id!r} is not in dimensions.cercanias_stations"))
        rows[trip_id] = {
            "trip_id": trip_id,
            "service_date": trip.service_date.isoformat(),
            "observed_at": observed_at.isoformat(),
            "train_number": trip.train_number,
            "line_id": trip.line_id,
            "station_id": station_id,
            "current_status": v.get("currentStatus"),
            "lat": position.get("latitude"),
            "lon": position.get("longitude"),
            "platform_number": platform,
            "source_vehicle_label": label,
            "vehicle_timestamp": _dt_iso(_ts(v.get("timestamp"))),
            "schedule_relationship": None,
            "next_station_id": None,
            "predicted_arrival": None,
            "source_delay_seconds": None,
            "skipped_station_ids": [],
            "n_stop_time_updates": None,
            "wheelchair_accessible": None,
        }

    for entity in (updates or {}).get("entity") or []:
        tu = entity.get("tripUpdate") or {}
        trip_id = str((tu.get("trip") or {}).get("tripId") or "")
        trip = trips.get(trip_id)
        if trip is None:
            continue
        stus = tu.get("stopTimeUpdate") or []
        # Exactly one entry carries a delay and it is always the first: 3,877 of 3,877.
        # Every entry after it was SKIPPED, 778 of 778.
        first = next((s for s in stus if (s.get("arrival") or {}).get("delay") is not None), None)
        arrival = (first or {}).get("arrival") or {}
        skipped = [s.get("stopId") for s in stus
                   if s.get("scheduleRelationship") == "SKIPPED" and s.get("stopId")]

        raw_wheelchair = (tu.get("vehicle") or {}).get("wheelchairAccessible")
        wheelchair = WHEELCHAIR.get(raw_wheelchair) if raw_wheelchair else None
        if raw_wheelchair and raw_wheelchair not in WHEELCHAIR:
            anomalies.append(Anomaly(
                "trip_updates.json", json.dumps(entity),
                f"unmapped wheelchairAccessible value {raw_wheelchair!r}"))

        row = rows.get(trip_id) or {
            "trip_id": trip_id,
            "service_date": trip.service_date.isoformat(),
            "observed_at": observed_at.isoformat(),
            "train_number": trip.train_number,
            "line_id": trip.line_id,
            "station_id": None, "current_status": None, "lat": None, "lon": None,
            "platform_number": None, "source_vehicle_label": None, "vehicle_timestamp": None,
        }
        row.update({
            "schedule_relationship": (tu.get("trip") or {}).get("scheduleRelationship"),
            "next_station_id": (first or {}).get("stopId"),
            "predicted_arrival": _dt_iso(_ts(arrival.get("time"))),
            "source_delay_seconds": arrival.get("delay"),
            "skipped_station_ids": skipped,
            "n_stop_time_updates": len(stus),
            "wheelchair_accessible": wheelchair,
        })
        rows[trip_id] = row

    return list(rows.values()), anomalies


def _dt_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def canonical_alert(alert: dict[str, Any]) -> str:
    """The alert as stable JSON text, so that a repeat publication hashes the same.

    Renfe reorders the translation array between publications: on one sample 13 of 71
    alerts changed and changed back every other publication with nothing else differing.
    Hashing the payload as delivered would manufacture a new version every 20 seconds.
    """
    canonical = {
        "activePeriod": sorted(
            json.dumps(p, sort_keys=True) for p in alert.get("activePeriod") or []),
        "informedEntity": sorted(
            json.dumps(e, sort_keys=True) for e in alert.get("informedEntity") or []),
        "translation": sorted(
            json.dumps(t, sort_keys=True)
             for t in (alert.get("descriptionText") or {}).get("translation") or []),
        "effect": alert.get("effect"),
        "cause": alert.get("cause"),
    }
    return json.dumps(canonical, sort_keys=True, ensure_ascii=False)


def content_hash(alert: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_alert(alert).encode()).hexdigest()[:12]


def parse_alerts(
    payload: dict[str, Any] | None, observed_at: datetime,
) -> dict[str, dict[str, Any]]:
    """Madrid alerts in this publication, keyed by alert_id.

    Madrid is decided by route id prefix, the same '10T' rule the static pipeline uses.
    An alert that names a Madrid route is kept whole, including the routes it names in
    other networks: a disruption spanning several of them is one fact, and truncating
    its entity list would misreport how far it reached.
    """
    out: dict[str, dict[str, Any]] = {}
    for entity in (payload or {}).get("entity") or []:
        alert = entity.get("alert") or {}
        informed = alert.get("informedEntity") or []
        route_ids = [e["routeId"] for e in informed if e.get("routeId")]
        madrid_routes = [r for r in route_ids if r.startswith(MADRID_NUCLEO)]
        if not madrid_routes:
            continue
        periods = alert.get("activePeriod") or []
        translations = sorted(
            ({"language": t.get("language"), "text": t.get("text")}
             for t in (alert.get("descriptionText") or {}).get("translation") or []),
            key=lambda t: (t["language"] or "", t["text"] or ""))
        out[entity["id"]] = {
            "alert_id": entity["id"],
            "service_date": service_date_for(observed_at).isoformat(),
            "observed_at": observed_at.isoformat(),
            "version_status": "active",
            "content_hash": content_hash(alert),
            "active_period_start": _dt_iso(_ts(periods[0].get("start")) if periods else None),
            "n_active_periods": len(periods),
            "effect": alert.get("effect"),
            "source_translations": translations,
            "route_ids": route_ids,
            # '10T0031C2' -> 'C2'. Same derivation the static pipeline makes.
            "line_ids": sorted({r[len(MADRID_NUCLEO) + 4:] for r in madrid_routes}),
            "station_ids": [e["stopId"] for e in informed if e.get("stopId")],
            "trip_ids": [e["trip"]["tripId"] for e in informed
                         if (e.get("trip") or {}).get("tripId")],
            "source_payload": canonical_alert(alert),
        }
    return out


def ended_alert_row(alert_id: str, content_hash_value: str, observed_at: datetime) -> dict[str, Any]:
    """A tombstone: the publication in which an alert stopped appearing.

    Renfe publishes no end time on any alert, so this row is the only record that one
    finished. Every content column is NULL; the content is on the row before it.
    """
    return {
        "alert_id": alert_id,
        "service_date": service_date_for(observed_at).isoformat(),
        "observed_at": observed_at.isoformat(),
        "version_status": "ended",
        "content_hash": content_hash_value,
        "active_period_start": None, "n_active_periods": None, "effect": None,
        "source_translations": [], "route_ids": [], "line_ids": [],
        "station_ids": [], "trip_ids": [], "source_payload": None,
    }


def is_feed_missing_madrid(n_madrid: int, n_entities: int, expected_running: int) -> bool:
    """Whether this publication is missing Madrid rather than reporting a quiet network.

    Renfe serves incomplete national snapshots from some of its backends: a fresh header
    timestamp, its own ETag, a valid structure — and a whole nucleo absent. Measured, a
    full snapshot carries 270-320 entities of which 33-38% are Madrid; a partial carries
    170-215 of which *exactly zero* are. The nucleo is dropped whole, never partially.

    The yardstick is our own timetable, not the previous publication. An earlier version
    compared against the last publication, which catches a one-off partial and is blind to
    a sustained one: once Renfe stops sending Madrid entirely, every publication agrees
    with the one before it and a half-hour blackout at Wednesday rush hour reads as a
    quiet network. That happened on 2026-09-02, 7 publications out of 7.

    The schedule cannot be fooled that way and needs no hardcoded service hours: at 02:00
    it expects nothing, so an empty feed is correctly silent rather than an alarm.
    """
    return n_madrid == 0 and expected_running > 0 and n_entities > 0


def diff_alert_versions(
    current: dict[str, dict[str, Any]], previous: dict[str, str], observed_at: datetime,
) -> list[dict[str, Any]]:
    """New, changed and ended alert versions, given what we held before.

    A row is written only when an alert appears, when its canonical content changes, or
    when it leaves the feed. Everything else is the same alert saying the same thing.
    """
    rows = [row for alert_id, row in current.items()
            if previous.get(alert_id) != row["content_hash"]]
    rows.extend(ended_alert_row(alert_id, content_hash_value, observed_at)
                for alert_id, content_hash_value in previous.items()
                if alert_id not in current)
    return rows


def alerts_look_partial(current: dict[str, dict[str, Any]], previous: dict[str, str]) -> bool:
    """Whether an alerts payload naming no Madrid alert should be disbelieved.

    The same backend failure as is_partial_snapshot, but the cost here is asymmetric:
    acting on it would tombstone every open alert and resurrect them on the next
    publication, inventing a disruption that ended and restarted. Losing one sample of an
    alert that has not changed costs nothing at all.
    """
    return not current and bool(previous)

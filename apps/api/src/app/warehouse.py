"""The warehouse snapshot the API serves from.

Read once at startup and refreshed on a timer, never per request. BigQuery bills per
byte scanned, and /api/vehicles is on the hot path: a per-request query would make every
map poll cost money for data that changes once a day.

The three queries together read about 1 MB.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from google.cloud import bigquery

from .config import Config
from .models import Line, Station

log = logging.getLogger(__name__)

# Stations and their lines. 95 rows.
STATIONS_SQL = """
SELECT station_id, display_name, station_name, lat, lon, line_ids
FROM `{stations}`
ORDER BY display_name
"""

# Lines with their route geometry. 12 rows, each carrying up to two LineStrings.
# ST_ASGEOJSON rather than the default WKT so the coordinates arrive already parsed
# into the [lon, lat] order MapLibre expects.
LINES_SQL = """
SELECT l.line_id, l.line_name, l.color_hex,
       ARRAY_AGG(ST_ASGEOJSON(s.geometry) IGNORE NULLS ORDER BY s.shape_id) AS shapes
FROM `{lines}` l
LEFT JOIN `{line_shapes}` s USING (line_id)
GROUP BY l.line_id, l.line_name, l.color_hex
ORDER BY l.line_id
"""

# Every Madrid trip that could plausibly be running now: yesterday, today, tomorrow in
# Madrid local time. Yesterday is included because a train that departed before midnight
# can still be moving after it — 748 Madrid trips cross midnight. Partition-filtered on
# service_date, so this reads about 250 KB rather than the whole 37,608-row table.
#
# direction_towards_station_id is the terminus the pattern heads towards — what a
# platform sign means by "towards Humanes" — and is not the same as
# destination_station_id, which is where this particular train stops.
TRIPS_SQL = """
SELECT t.trip_id, t.train_number, t.line_id,
       p.destination_station_id,
       p.direction_towards_station_id,
       p.n_stops
FROM `{trips}` t
JOIN `{patterns}` p USING (stop_pattern_id)
WHERE t.service_date BETWEEN DATE_SUB(CURRENT_DATE('Europe/Madrid'), INTERVAL 1 DAY)
                         AND DATE_ADD(CURRENT_DATE('Europe/Madrid'), INTERVAL 1 DAY)
"""


@dataclass(frozen=True)
class Trip:
    """What we know about one scheduled trip, keyed by the trip_id the feed reports."""
    train_number: str
    line_id: str
    destination: str | None
    towards: str | None
    calls_at: int


@dataclass(frozen=True)
class Snapshot:
    stations: list[Station]
    lines: list[Line]
    trips: dict[str, Trip]
    station_names: dict[str, str] = field(default_factory=dict)
    loaded_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _linestring_coordinates(geojson: str) -> list[tuple[float, float]]:
    """Coordinates of one ST_ASGEOJSON LineString, as [lon, lat] pairs."""
    geom = json.loads(geojson)
    if geom.get("type") != "LineString":
        log.warning("shape is %s, not LineString — skipped", geom.get("type"))
        return []
    return [(float(x), float(y)) for x, y in geom["coordinates"]]


def read_snapshot(cfg: Config, client: bigquery.Client | None = None) -> Snapshot:
    """Read the whole network and the current trip lookup from BigQuery."""
    client = client or bigquery.Client(project=cfg.project)
    dim, fact = cfg.ds_dimensions, cfg.ds_facts

    station_rows = list(client.query(STATIONS_SQL.format(
        stations=cfg.table(dim, "cercanias_stations"))).result())
    stations = [
        Station(id=r.station_id,
                name=r.display_name or r.station_name,
                lat=r.lat, lon=r.lon,
                lines=sorted(r.line_ids or []))
        for r in station_rows if r.lat is not None and r.lon is not None
    ]
    dropped = len(station_rows) - len(stations)
    if dropped:
        log.warning("%d station(s) have no coordinates and cannot be drawn", dropped)
    station_names = {r.station_id: (r.display_name or r.station_name) for r in station_rows}

    line_rows = list(client.query(LINES_SQL.format(
        lines=cfg.table(dim, "cercanias_lines"),
        line_shapes=cfg.table(dim, "cercanias_line_shapes"))).result())
    lines = [
        Line(id=r.line_id, name=r.line_name, color=r.color_hex,
             shapes=[c for c in (_linestring_coordinates(g) for g in r.shapes) if c])
        for r in line_rows
    ]
    for line in lines:
        if not line.shapes:
            log.warning("line %s has no geometry — it will not be drawn", line.id)

    trip_rows = list(client.query(TRIPS_SQL.format(
        trips=cfg.table(fact, "cercanias_scheduled_trips"),
        patterns=cfg.table(dim, "cercanias_stop_patterns"))).result())
    trips = {
        r.trip_id: Trip(
            train_number=r.train_number,
            line_id=r.line_id,
            destination=station_names.get(r.destination_station_id),
            towards=station_names.get(r.direction_towards_station_id),
            calls_at=r.n_stops,
        )
        for r in trip_rows
    }
    if len(trips) != len(trip_rows):
        log.warning("%d duplicate trip_id(s) in the lookup window", len(trip_rows) - len(trips))

    log.info("snapshot: %d stations, %d lines, %d shapes, %d trips",
             len(stations), len(lines), sum(len(line.shapes) for line in lines), len(trips))
    return Snapshot(stations=stations, lines=lines, trips=trips, station_names=station_names)

"""Build the station and topology tables from CRTM's static sources.

Two sources, because neither alone is enough:

- **ParadasPorItinerario** (GeoJSON) is the network's shape: which stations each line
  calls at, in order, per direction. It is the only source that carries line 3 — the
  GTFS has the route with no trips at all — and the only one that names the branches
  (10a/10b, 7a/7b, 9A/9B, 12-1, R) rather than collapsing them into one route each.
  It also carries the fare zone and municipality.

- **The Metro GTFS** supplies coordinates and scheduled segment times, which
  ParadasPorItinerario has no notion of. Both are missing for line 3.

Run this before the poller: it writes the work list the poller reads.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from .config import METRO_GTFS_URL, MODE_METRO, PARADAS_URL, TIER1, USER_AGENT
from .names import format_station_name

log = logging.getLogger(__name__)

# '10b' -> '10', '9A' -> '9', '12-1' -> '12', 'R' -> 'R'. The live feed names lines
# without their branch, so this is what an observation's line_id will match.
BRANCH_SUFFIX = re.compile(r"^(?P<line>\d+|R)(?P<branch>[a-zA-Z]|-\d+)?$")


@dataclass(frozen=True)
class Station:
    station_id: str
    station_name: str
    formatted_station_name: str
    fare_zone: str | None
    municipality: str | None
    line_ids: list[str]
    poll_tier: int


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def line_of(branch_id: str) -> str:
    """The unbranched line an itinerary belongs to."""
    m = BRANCH_SUFFIX.match(branch_id)
    return m.group("line") if m else branch_id


def read_topology(payload: dict[str, Any]) -> tuple[list[dict], dict[str, dict]]:
    """(line_stops rows, station attributes) from ParadasPorItinerario.

    **CRTM codes platforms, not stations.** Sol is 4_12 on line 1, 4_35 on line 2 and
    4_48 on line 3 — 291 codes for 242 physical stations, with 37 stations carrying more
    than one. The live endpoint does not work that way: given any code at a station it
    returns every line calling there, verified at Plaza de Castilla where all three codes
    returned an identical 14 arrivals.

    So one code per station is both sufficient and necessary — polling the others would
    count the same arrivals two or three times. Stations are grouped by name (the only
    key available, since parent_station is set on 42 platforms of 290), the lowest code
    becomes the one we poll, and the rest are kept in source_station_ids so the choice
    stays auditable. Every id below, including in line_stops, is the canonical one, so a
    journey can change lines at Sol without changing station.
    """
    raw: list[dict[str, Any]] = []
    by_name: dict[str, set[str]] = {}
    for feature in payload.get("features") or []:
        p = feature.get("properties") or {}
        branch_id = str(p.get("NUMEROLINEAUSUARIO") or "").strip()
        code = str(p.get("CODIGOESTACION") or "").strip()
        name = (p.get("DENOMINACION") or "").strip()
        if not branch_id or not code or not name:
            continue
        platform_id = f"{MODE_METRO}_{code}"
        by_name.setdefault(name, set()).add(platform_id)
        raw.append({"branch_id": branch_id, "direction": int(p["SENTIDO"]),
                    "stop_number": int(p["NUMEROORDEN"]), "platform_id": platform_id,
                    "name": name, "zone": p.get("CORONATARIFARIA") or None,
                    "municipality": p.get("MUNICIPIO") or None})

    def sort_key(pid: str) -> tuple[int, str]:
        return (int(pid.split("_")[1]), pid) if pid.split("_")[1].isdigit() else (10**9, pid)

    canonical = {}
    for name, ids in by_name.items():
        chosen = min(ids, key=sort_key)
        for pid in ids:
            canonical[pid] = chosen

    stops: list[dict[str, Any]] = []
    stations: dict[str, dict[str, Any]] = {}
    for r in raw:
        station_id = canonical[r["platform_id"]]
        line_id = line_of(r["branch_id"])
        stops.append({
            "line_id": line_id,
            "branch_id": r["branch_id"],
            "direction": r["direction"],
            "stop_number": r["stop_number"],
            "station_id": station_id,
        })
        st = stations.setdefault(station_id, {
            "station_id": station_id,
            "station_name": r["name"],
            "formatted_station_name": format_station_name(r["name"]),
            "fare_zone": r["zone"],
            "municipality": r["municipality"],
            "line_ids": set(),
            "source_station_ids": sorted(by_name[r["name"]], key=sort_key),
        })
        st["line_ids"].add(line_id)
    for st in stations.values():
        st["line_ids"] = sorted(st["line_ids"], key=lambda x: (len(x), x))
    return stops, stations


def read_gtfs(zip_bytes: bytes) -> tuple[dict[str, dict], dict[tuple[str, str], int]]:
    """(coordinates by station_id, scheduled segment seconds by (from, to)).

    Segment times come from stop_times, which are arrival-to-arrival and so already
    include dwell — no dwell is modelled anywhere in this feed. Keyed by station pair
    rather than by line, because the same physical hop takes the same time whichever
    branch is running it, and this way line 3 can borrow nothing rather than guess.
    """
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))

    def rows(name: str) -> list[dict[str, str]]:
        with zf.open(name) as fh:
            return list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")))

    coords: dict[str, dict[str, Any]] = {}
    for s in rows("stops.txt"):
        if not s["stop_id"].startswith("par_"):
            continue
        coords[s["stop_id"].removeprefix("par_")] = {
            "lat": float(s["stop_lat"]) if s["stop_lat"] else None,
            "lon": float(s["stop_lon"]) if s["stop_lon"] else None,
            "source_stop_id": s["stop_id"],
            "crtm_parent_station": s.get("parent_station") or None,
        }

    def sec(t: str) -> int:
        h, m, s = (int(x) for x in t.split(":"))
        return h * 3600 + m * 60 + s

    by_trip: dict[str, list[tuple[int, str, int]]] = {}
    for r in rows("stop_times.txt"):
        by_trip.setdefault(r["trip_id"], []).append(
            (int(r["stop_sequence"]), r["stop_id"].removeprefix("par_"), sec(r["arrival_time"])))
    segments: dict[tuple[str, str], int] = {}
    for seq in by_trip.values():
        seq.sort()
        for (_, a, ta), (_, b, tb) in pairwise(seq):
            gap = tb - ta
            if gap > 0:
                segments.setdefault((a, b), gap)
    return coords, segments


def build(cercanias_by_parent: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    """Fetch both sources and return (station rows, line_stop rows), ready to load."""
    topology_payload = json.loads(_fetch(PARADAS_URL))
    stops, stations = read_topology(topology_payload)
    coords, segments = read_gtfs(_fetch(METRO_GTFS_URL))
    cercanias_by_parent = cercanias_by_parent or {}

    station_rows = []
    for station_id, st in stations.items():
        c = coords.get(station_id, {})
        parent = c.get("crtm_parent_station")
        station_rows.append({
            **st,
            "lat": c.get("lat"),
            "lon": c.get("lon"),
            "source_stop_id": c.get("source_stop_id"),
            "crtm_parent_station": parent,
            "cercanias_station_id": cercanias_by_parent.get(parent) if parent else None,
            "poll_tier": 1 if st["station_name"] in TIER1 else 2,
            "active": True,
        })

    # Attach the scheduled hop time to each stop from its predecessor on the same branch.
    stops.sort(key=lambda r: (r["branch_id"], r["direction"], r["stop_number"]))
    previous: tuple[str, int] | None = None
    prev_station = ""
    for row in stops:
        key = (row["branch_id"], row["direction"])
        row["scheduled_seconds_from_previous"] = (
            segments.get((prev_station, row["station_id"])) if key == previous else None)
        previous, prev_station = key, row["station_id"]

    n_tier1 = sum(1 for r in station_rows if r["poll_tier"] == 1)
    with_time = sum(1 for r in stops if r["scheduled_seconds_from_previous"] is not None)
    log.info("metro topology: %d stations (%d tier 1), %d line stops, %d with a "
             "scheduled hop time, %d lines",
             len(station_rows), n_tier1, len(stops), with_time,
             len({r["line_id"] for r in stops}))
    missing = sorted(TIER1 - {r["station_name"] for r in station_rows})
    if missing:
        log.error("tier 1 names not found in the source: %s", missing)
    return station_rows, stops

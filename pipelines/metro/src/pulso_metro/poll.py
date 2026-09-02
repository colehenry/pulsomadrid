"""Fetch and parse one station's arrivals.

Pure parsing is separated from fetching so it can be tested against committed real
responses rather than mocks.

Shape of the source, read from the live endpoint and not from documentation — there is
none:

    {"stopTimes": {
       "actualDate": "2026-09-01T21:53:15+02:00",
       "stop": {"codStop": "4_1", "name": "PLAZA DE CASTILLA"},
       "times": {"Time": [
         {"line": {"shortDescription": "10", "codLine": "4__10___"},
          "direction": 1, "destination": "PUERTA DEL SUR",
          "destinationStop": {"codStop": "4_205"},
          "time": "2026-09-01T21:53:47+02:00",
          "codVehicle": "", "codIssue": ""}]},
       "linesStatus": {"LineStatus": [{"line": {"shortDescription": "1"},
                                       "SAEStatus": true}]}}}
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import SERVICE_DAY_CUTOVER_HOUR, TIMEZONE

log = logging.getLogger(__name__)
MADRID = ZoneInfo(TIMEZONE)


def service_date_for(observed_at: datetime) -> str:
    """Madrid service date, cut at 03:00 rather than midnight.

    Metro runs to about 01:30, so a train at 00:40 belongs to the night that is still
    running. Same rule as the Cercanias recorder uses for alerts.
    """
    local = observed_at.astimezone(MADRID)
    return (local - timedelta(hours=SERVICE_DAY_CUTOVER_HOUR)).date().isoformat()


def _ts(value: str | None) -> str | None:
    """CRTM sends ISO 8601 with an offset; normalise to UTC for BigQuery."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC).isoformat()
    except ValueError:
        return None


def _as_list(value: Any) -> list[Any]:
    """Normalise CRTM's single-element collapse.

    The payload is XML rendered as JSON, so a collection holding exactly one item is
    serialised as that item rather than as a list of one — `times.Time` and
    `linesStatus.LineStatus` both do it. Iterating the result then yields dict *keys*,
    which fails as soon as a station happens to have one arrival or one line. Found by
    polling real stations, not by reading the payload.
    """
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def parse(payload: dict[str, Any], station_id: str, observed_at: datetime,
          poll_tier: int) -> dict[str, Any]:
    """One row: this station, at this tick.

    observed_at is OUR tick, not CRTM's clock. CRTM's actualDate changes on every request
    — it cannot be an idempotency key, because a retry would then look like a new
    observation rather than the same one.
    """
    stop_times = payload.get("stopTimes") or {}
    # `times` is an empty object, not an empty list, when a station has no upcoming
    # trains. Normal near closing time; a naive ["Time"] raises.
    times = _as_list((stop_times.get("times") or {}).get("Time"))

    arrivals = []
    for t in times:
        line = t.get("line") or {}
        arrivals.append({
            "line_id": line.get("shortDescription"),
            "direction": int(t["direction"]) if t.get("direction") is not None else None,
            "destination": t.get("destination"),
            "destination_station_id": (t.get("destinationStop") or {}).get("codStop"),
            "predicted_arrival": _ts(t.get("time")),
        })

    sae = [{"line_id": (s.get("line") or {}).get("shortDescription"),
            "ok": bool(s.get("SAEStatus"))}
           for s in _as_list((stop_times.get("linesStatus") or {}).get("LineStatus"))]

    return {
        "station_id": station_id,
        "service_date": service_date_for(observed_at),
        "observed_at": observed_at.isoformat(),
        "source_timestamp": _ts(stop_times.get("actualDate")),
        "arrivals": arrivals,
        "n_arrivals": len(times),
        "sae_status": sae,
        "poll_tier": poll_tier,
    }

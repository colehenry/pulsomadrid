"""Renfe's live vehicle feed, filtered to Madrid and enriched from the warehouse.

The feed is national: 363 vehicles on the sample taken while this was written, of which
126 were Madrid. The filter is a join on our own trip_id set, never a lat/lon bounding
box — the same reasoning that filters the static feed on route_id LIKE '10T%' rather than
on a line name. 'C1' exists in eleven Spanish networks, and a box around Madrid would
also catch long-distance services that happen to pass through.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .models import Vehicle
from .warehouse import Snapshot

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VehicleSnapshot:
    observed_at: datetime
    vehicles: list[Vehicle]
    fetched_at: datetime


def station_name(snapshot: Snapshot, stop_id: str | None) -> str | None:
    """Display name for a feed stop_id. NULL for a stop outside the Madrid network."""
    return snapshot.station_names.get(stop_id) if stop_id else None


def parse_feed(payload: dict, snapshot: Snapshot) -> VehicleSnapshot:
    """Turn one vehicle_positions.json body into Madrid vehicles.

    Shape of the source, read from the live feed rather than from documentation:

        {"header": {"gtfsRealtimeVersion": "2.0", "timestamp": "1788155211"},
         "entity": [{"id": "VP_C1-23515",
                     "vehicle": {"trip": {"tripId": "3041L23515C1"},
                                 "position": {"latitude": …, "longitude": …},
                                 "currentStatus": "STOPPED_AT",
                                 "timestamp": "1788155208",
                                 "stopId": "51200",
                                 "vehicle": {"id": "23515",
                                             "label": "C1-23515-PLATF.(4)"}}}]}

    The feed's own trip and vehicle ids are not trusted for line or destination: line
    comes from our schedule, because the label is free text and the destination is not
    in the feed at all.
    """
    header = payload.get("header") or {}
    observed_at = datetime.fromtimestamp(int(header.get("timestamp", 0)), UTC)

    vehicles: list[Vehicle] = []
    n_total = n_no_position = 0
    for entity in payload.get("entity") or []:
        v = entity.get("vehicle") or {}
        n_total += 1
        trip_id = (v.get("trip") or {}).get("tripId")
        trip = snapshot.trips.get(trip_id) if trip_id else None
        if trip is None:
            continue  # not a Madrid trip we hold, or a trip from another service date
        position = v.get("position") or {}
        lat, lon = position.get("latitude"), position.get("longitude")
        if lat is None or lon is None:
            n_no_position += 1  # one entity in the sample feed had no position
            continue
        vehicles.append(Vehicle(
            train_number=trip.train_number,
            line_id=trip.line_id,
            lat=float(lat),
            lon=float(lon),
            status=v.get("currentStatus") or "UNKNOWN",
            at_station=station_name(snapshot, v.get("stopId")),
            destination=trip.destination,
            towards=trip.towards,
            calls_at=trip.calls_at,
        ))

    log.info("feed %s: %d vehicles, %d in Madrid, %d dropped for no position",
             observed_at.isoformat(), n_total, len(vehicles), n_no_position)
    return VehicleSnapshot(observed_at=observed_at, vehicles=vehicles,
                           fetched_at=datetime.now(UTC))


class VehicleFeed:
    """Fetches the feed, with a short cache and a last-good fallback.

    Three protections, because the upstream is a third party we do not control and the
    whole map depends on it:

    - a short cache, so a burst of page loads is not a burst of upstream requests;
    - a single-flight lock, so concurrent requests that all miss the cache produce one
      fetch between them rather than one each;
    - a last-good fallback, so an outage degrades the map to stale data rather than
      breaking the page.

    The lock is the one that is easy to leave out and hard to notice missing. Without it
    the cache only helps requests that arrive after a fetch has finished: every request
    arriving *during* one still fetches. That is not a rare race — Cloud Run puts up to
    80 concurrent requests on an instance, the cache expires every few seconds, and a
    cold instance starts with nothing cached at all.
    """

    def __init__(self, url: str, *, timeout: float = 10.0, cache_seconds: float = 10.0) -> None:
        self._url = url
        self._cache_seconds = cache_seconds
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self._last: VehicleSnapshot | None = None
        self._upstream_ok = False
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def upstream_ok(self) -> bool:
        return self._upstream_ok

    def _cached(self) -> VehicleSnapshot | None:
        """The last snapshot if it is still inside the cache window, else None."""
        if self._last is None or not self._upstream_ok:
            return None
        age = (datetime.now(UTC) - self._last.fetched_at).total_seconds()
        return self._last if age < self._cache_seconds else None

    async def current(self, snapshot: Snapshot) -> VehicleSnapshot | None:
        """The current Madrid vehicles, or the last good snapshot if the fetch fails."""
        cached = self._cached()
        if cached is not None:
            return cached

        async with self._lock:
            # Check again: while this request waited for the lock, whoever held it has
            # very likely refreshed the snapshot already. Without this second check the
            # lock would serialise the fetches instead of collapsing them into one.
            cached = self._cached()
            if cached is not None:
                return cached
            await self._refresh(snapshot)

        return self._last

    async def _refresh(self, snapshot: Snapshot) -> None:
        """Fetch and parse once. Called only under the lock."""
        try:
            response = await self._client.get(self._url)
            response.raise_for_status()
            self._last = parse_feed(response.json(), snapshot)
            self._upstream_ok = True
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # Narrow on purpose: a transport error, a non-2xx, unparseable JSON, or a feed
            # whose shape changed. Anything else is our bug and should surface as a 500.
            self._upstream_ok = False
            log.warning("vehicle feed unavailable (%s: %s) — serving last known snapshot",
                        type(exc).__name__, exc)

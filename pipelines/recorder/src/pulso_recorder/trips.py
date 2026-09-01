"""The Madrid trip lookup, and the station set the observations are checked against.

This is the Madrid filter. The feed is national -- 116 entities at 05:30, 191 at 05:50 --
and the only safe way to select Madrid is to join the feed's trip_id against our own
schedule. Verified rather than assumed: of 90 feed trip_ids that did not join, not one
touched a Madrid station, and every stopId that appeared on a trip that did join was
already in dimensions.cercanias_stations.

A lat/lon box would be wrong for the same reason 'C1' is wrong: 'C1' exists in eleven
Spanish networks, and a box around Madrid also catches long-distance services passing
through.
"""
from __future__ import annotations

import logging
from datetime import date

from google.cloud import bigquery

from .config import Config, credentials
from .feeds import Trip

log = logging.getLogger(__name__)

# Yesterday, today and tomorrow in Madrid local time. Yesterday is included because a
# train that departed before midnight is still running after it -- 748 Madrid trips cross
# midnight. Partition-filtered, so this reads a few hundred KB rather than the table.
TRIPS_SQL = """
SELECT trip_id, train_number, line_id, service_date
FROM `{trips}`
WHERE service_date BETWEEN DATE_SUB(CURRENT_DATE('Europe/Madrid'), INTERVAL 1 DAY)
                       AND DATE_ADD(CURRENT_DATE('Europe/Madrid'), INTERVAL 1 DAY)
"""

STATIONS_SQL = "SELECT station_id FROM `{stations}`"


class TripLookup:
    """Trips and stations, reloaded on a timer.

    trip_ids are unique to one service date by construction, so a recorder that loaded
    this once at startup would silently stop matching every train the moment the date
    rolled over in Madrid -- no error, no exception, just a feed that suddenly contains
    no Madrid trains at all. It must be refreshed, and the refresh must be logged.
    """

    def __init__(self, cfg: Config, client: bigquery.Client | None = None) -> None:
        self._cfg = cfg
        self._client = client or bigquery.Client(project=cfg.project, credentials=credentials())
        self.trips: dict[str, Trip] = {}
        self.stations: set[str] = set()
        self.loaded_at: date | None = None

    def refresh(self) -> int:
        trips_table = self._cfg.table(self._cfg.ds_facts, "cercanias_scheduled_trips")
        stations_table = self._cfg.table(self._cfg.ds_dimensions, "cercanias_stations")
        rows = list(self._client.query(TRIPS_SQL.format(trips=trips_table)).result())
        trips = {
            r.trip_id: Trip(train_number=r.train_number, line_id=r.line_id,
                            service_date=r.service_date)
            for r in rows
        }
        stations = {r.station_id for r in
                    self._client.query(STATIONS_SQL.format(stations=stations_table)).result()}

        if not trips:
            # Keep whatever we already hold rather than replacing it with nothing: an
            # empty lookup would silently reclassify every Madrid train as not-Madrid.
            log.error("trip lookup query returned no rows — keeping the previous %d trips",
                      len(self.trips))
            return len(self.trips)

        self.trips, self.stations = trips, stations
        log.info("trip lookup refreshed: %d trips over %d service dates, %d stations",
                 len(trips), len({t.service_date for t in trips.values()}), len(stations))
        return len(trips)

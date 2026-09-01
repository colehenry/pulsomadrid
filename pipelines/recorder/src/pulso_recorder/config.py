"""Settings for the recorder, all overridable by environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from google.oauth2 import service_account

FEED_URLS = {
    "vehicle_positions": "https://gtfsrt.renfe.com/vehicle_positions.json",
    "trip_updates": "https://gtfsrt.renfe.com/trip_updates.json",
    "alerts": "https://gtfsrt.renfe.com/alerts.json",
}

# Madrid nucleo. Renfe route_ids start with a 3-character network code and 'C1' exists
# in eleven Spanish networks, so this prefix is the only safe filter. Trains are filtered
# by joining trip_id against our own schedule; this prefix is for alerts, which carry
# route ids rather than trips.
MADRID_NUCLEO = "10T"

TIMEZONE = "Europe/Madrid"

# The service day is cut at 03:00 Madrid rather than midnight, so an alert observed at
# 00:30 belongs to the night that is still running. Safe rather than arbitrary: across a
# full timetable Cercanias schedules 706 calls in hour 00, 2 in hour 01, none at all in
# hours 02 and 03, and 16 in hour 04. Any cut inside that empty window gives the same
# answers. Trains do not use this -- they take service_date from their trip.
SERVICE_DAY_CUTOVER_HOUR = 3

SOURCE = "renfe_gtfs_rt"


@dataclass(frozen=True)
class Config:
    project: str = field(default_factory=lambda: os.getenv("GCP_PROJECT_ID", "pulso-madrid"))
    bucket: str = field(default_factory=lambda: os.getenv("GCS_RAW_BUCKET", "pulso-madrid-raw"))
    ds_facts: str = field(default_factory=lambda: os.getenv("BQ_DATASET_FACTS", "facts"))
    ds_dimensions: str = field(
        default_factory=lambda: os.getenv("BQ_DATASET_DIMENSIONS", "dimensions"))
    ds_ops: str = field(default_factory=lambda: os.getenv("BQ_DATASET_OPS", "ops"))

    # Poll faster than the feed publishes and deduplicate on the header timestamp.
    # Publication is ~20s with 16-24s jitter and an occasional 32s gap, so a 20s poll
    # drops messages while a 10s poll cannot. Polling faster costs three cheap GETs and
    # writes zero extra rows, because a repeated header timestamp is discarded.
    poll_seconds: float = field(
        default_factory=lambda: float(os.getenv("RECORDER_POLL_SECONDS", "10")))

    # BigQuery allows 1,500 load jobs per table per day, counting failures and retries.
    # A 60s flush is 1,440/day, which is the ceiling with no room to retry; 120s is 720
    # and leaves the same headroom again. Latency does not argue for anything faster --
    # the API serves the live map from this process's memory, not from BigQuery.
    flush_seconds: float = field(
        default_factory=lambda: float(os.getenv("RECORDER_FLUSH_SECONDS", "120")))

    # The trip lookup is bounded by service date, so a recorder that loaded it once would
    # silently stop matching every train at midnight.
    trips_refresh_seconds: float = field(
        default_factory=lambda: float(os.getenv("RECORDER_TRIPS_REFRESH_SECONDS", "900")))

    upstream_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "15")))

    archive_enabled: bool = field(
        default_factory=lambda: os.getenv("RECORDER_ARCHIVE", "1") not in ("0", "false", ""))

    def table(self, dataset: str, name: str) -> str:
        return f"{self.project}.{dataset}.{name}"


def credentials() -> service_account.Credentials | None:
    """Explicit credentials from a JSON key in the environment, or None to use ADC.

    Railway supplies a service-account key as JSON in an environment variable, while
    Google's libraries expect GOOGLE_APPLICATION_CREDENTIALS to hold a *path to a file*.
    Parsing it here avoids writing a key to disk at all. Unset means ADC, so local
    development works with nothing configured. Same helper as pipelines/gtfs.
    """
    import json

    raw = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS_JSON is set but is not valid JSON. It should "
            "hold the whole service-account key file, not a path to it."
        ) from exc
    return service_account.Credentials.from_service_account_info(info)

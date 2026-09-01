"""Settings, all overridable by environment variable.

Mirrors pipelines/gtfs/src/pulso_gtfs/config.py so the two halves of the project read
their configuration the same way.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# Verified live, no auth, no key: 363 vehicles nationally on 2026-08-31T05:46Z.
# The same feed is also published as protobuf (.pb); the JSON form is used here
# because it needs no extra dependency to parse.
RENFE_VEHICLE_POSITIONS_URL = "https://gtfsrt.renfe.com/vehicle_positions.json"


@dataclass(frozen=True)
class Config:
    project: str = field(default_factory=lambda: os.getenv("GCP_PROJECT_ID", "pulso-madrid"))
    ds_dimensions: str = field(default_factory=lambda: os.getenv("BQ_DATASET_DIMENSIONS", "dimensions"))
    ds_facts: str = field(default_factory=lambda: os.getenv("BQ_DATASET_FACTS", "facts"))

    vehicle_positions_url: str = field(
        default_factory=lambda: os.getenv("RENFE_GTFS_RT_URL") or RENFE_VEHICLE_POSITIONS_URL)

    # How long a cached vehicle snapshot may be reused. The feed's own timestamp moves
    # every few seconds; the page polls every 30s, so anything below that is wasted.
    vehicles_cache_seconds: float = field(
        default_factory=lambda: float(os.getenv("VEHICLES_CACHE_SECONDS", "10")))

    # Seconds between refreshes of the warehouse snapshot. The static feed changes at
    # most daily, but the trip lookup is keyed by service date, so it must be rebuilt
    # before midnight rolls over. Each refresh reads about 250 KB.
    warehouse_refresh_seconds: float = field(
        default_factory=lambda: float(os.getenv("WAREHOUSE_REFRESH_SECONDS", "900")))

    upstream_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "10")))

    cors_origins: tuple[str, ...] = field(default_factory=lambda: tuple(
        o.strip() for o in os.getenv("API_CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()))

    # Netlify gives every pull request a deploy preview on a generated subdomain, so
    # those origins cannot be listed one by one. A regex covers them without opening the
    # API to any origin at all. Unset means production origins only.
    cors_origin_regex: str | None = field(
        default_factory=lambda: os.getenv("API_CORS_ORIGIN_REGEX") or None)

    def table(self, dataset: str, name: str) -> str:
        return f"{self.project}.{dataset}.{name}"

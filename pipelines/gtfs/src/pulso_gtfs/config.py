"""Settings, all overridable by environment variable."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

RENFE_GTFS_URL = "https://ssl.renfe.com/ftransit/Fichero_CER_FOMENTO/fomento_transit.zip"
CRTM_CERCANIAS_ITEM = "1a25440bf66f499bae2657ec7fb40144"
CRTM_GTFS_URL = f"https://www.arcgis.com/sharing/rest/content/items/{CRTM_CERCANIAS_ITEM}/data"

# Madrid nucleo. Renfe route_ids start with a 3-character network code and
# 'C1' exists in eleven Spanish networks, so this prefix is the only safe filter.
MADRID_NUCLEO = "10T"

TIMEZONE = "Europe/Madrid"

# Station names to show a user, where stripping the "Madrid-" prefix and " Cercanías"
# suffix is not enough. Kept explicit rather than rule-based: no rule can tell
# "Chamartín-Clara Campoamor" (one station, two names) from "Getafe-Centro" and
# "Getafe-Industrial" (two different stations).
STATION_DISPLAY_NAMES = {
    "17000": "Chamartín",   # Renfe: Madrid-Chamartín-Clara Campoamor
}


@dataclass(frozen=True)
class Config:
    project: str = field(default_factory=lambda: os.getenv("GCP_PROJECT_ID", "pulso-madrid"))
    bucket: str = field(default_factory=lambda: os.getenv("GCS_RAW_BUCKET", "pulso-madrid-raw"))
    ds_raw: str = field(default_factory=lambda: os.getenv("BQ_DATASET_RAW", "raw"))
    ds_facts: str = field(default_factory=lambda: os.getenv("BQ_DATASET_FACTS", "facts"))
    ds_dimensions: str = field(default_factory=lambda: os.getenv("BQ_DATASET_DIMENSIONS", "dimensions"))
    ds_marts: str = field(default_factory=lambda: os.getenv("BQ_DATASET_MARTS", "marts"))
    ds_ops: str = field(default_factory=lambda: os.getenv("BQ_DATASET_OPS", "ops"))

    def table(self, dataset: str, name: str) -> str:
        return f"{self.project}.{dataset}.{name}"

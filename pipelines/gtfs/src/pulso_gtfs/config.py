"""Settings, all overridable by environment variable."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from google.oauth2 import service_account

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


def credentials() -> service_account.Credentials | None:
    """Explicit credentials from a JSON key in the environment, or None to use ADC.

    Railway — like most platform hosts — supplies a service-account key as JSON in an
    environment variable, while Google's libraries expect GOOGLE_APPLICATION_CREDENTIALS
    to hold a *path to a file*. Point that variable at JSON and authentication fails with
    an error about a missing file.

    Parsing it here and handing the client explicit credentials avoids writing a key to
    disk at all: no temp file to create, secure, or clean up. When the variable is unset
    this returns None, the clients fall back to Application Default Credentials, and
    local development keeps working with nothing configured.
    """
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

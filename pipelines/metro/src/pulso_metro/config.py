"""Settings for the Metro poller, all overridable by environment variable."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from google.oauth2 import service_account

# Live arrivals. Undocumented: this is what CRTM's own site widgets call. No licence, no
# terms, no stability guarantee — see docs/data/metro-rt.md. Poll politely.
ARRIVALS_URL = "https://www.crtm.es/widgets/api/GetStopsTimes.php"

# Static sources, both from CRTM's open data portal.
METRO_GTFS_ITEM = "5c7f2951962540d69ffe8f640d94c246"
METRO_GTFS_URL = f"https://www.arcgis.com/sharing/rest/content/items/{METRO_GTFS_ITEM}/data"
# ParadasPorItinerario: the line topology. The only source that carries line 3 or names
# the branches (10a/10b, 7a/7b, 9A/9B) — the GTFS does neither.
PARADAS_URL = ("https://datos.crtm.es/api/download/v1/items/"
               "0a6c45e7bdd94679b67a2ae662c8838b/geojson?layers=5")

MODE_METRO = "4"          # CRTM mode code; station ids are '4_<n>'
TIMEZONE = "Europe/Madrid"
SERVICE_DAY_CUTOVER_HOUR = 3   # same rule as the Cercanias recorder
SOURCE = "crtm_metro"

# Identify ourselves. If CRTM objects to the traffic they should be able to reach us
# rather than silently blocking an anonymous client.
USER_AGENT = "pulso-madrid/0.1 (+https://pulsomadrid.es) metro-arrivals"


@dataclass(frozen=True)
class Config:
    project: str = field(default_factory=lambda: os.getenv("GCP_PROJECT_ID", "pulso-madrid"))
    bucket: str = field(default_factory=lambda: os.getenv("GCS_RAW_BUCKET", "pulso-madrid-raw"))
    ds_facts: str = field(default_factory=lambda: os.getenv("BQ_DATASET_FACTS", "facts"))
    ds_dimensions: str = field(
        default_factory=lambda: os.getenv("BQ_DATASET_DIMENSIONS", "dimensions"))
    ds_ops: str = field(default_factory=lambda: os.getenv("BQ_DATASET_OPS", "ops"))

    # One tick drives everything: every tier-1 station plus a rotating slice of tier 2.
    # Flat request rate, no bursts. 30s is set by the headway maths — at peak headways of
    # 2-4 min it pins each arrival to +/-30s, so a single measurement carries 12-25%
    # error, which averages out. 60s would need four times the samples for the same
    # precision and costs smoothness for the position layer.
    tick_seconds: float = field(
        default_factory=lambda: float(os.getenv("METRO_TICK_SECONDS", "30")))
    # How long one full pass over the tier-2 stations takes.
    sweep_seconds: float = field(
        default_factory=lambda: float(os.getenv("METRO_SWEEP_SECONDS", "300")))
    # BigQuery allows 1,500 load jobs per table per day counting retries; 120s is 720.
    flush_seconds: float = field(
        default_factory=lambda: float(os.getenv("METRO_FLUSH_SECONDS", "120")))
    stations_refresh_seconds: float = field(
        default_factory=lambda: float(os.getenv("METRO_STATIONS_REFRESH_SECONDS", "3600")))
    concurrency: int = field(default_factory=lambda: int(os.getenv("METRO_CONCURRENCY", "8")))
    timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "20")))
    archive_enabled: bool = field(
        default_factory=lambda: os.getenv("METRO_ARCHIVE", "1") not in ("0", "false", ""))

    def table(self, dataset: str, name: str) -> str:
        return f"{self.project}.{dataset}.{name}"


def credentials() -> service_account.Credentials | None:
    """Explicit credentials from a JSON key in the environment, or None for ADC.

    Railway hands a service-account key as JSON in a variable, while Google's libraries
    expect GOOGLE_APPLICATION_CREDENTIALS to name a *file*. Parsing it here avoids ever
    writing the key to disk. Same helper as pipelines/gtfs and pipelines/recorder.
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


# The 30-second set: 49 stations from a greedy cover over every line-direction, so no
# point on any line is more than ~11 minutes of travel behind a polled station — which is
# all a station can see, because the feed returns at most 3 upcoming trains. Termini are
# included deliberately: the final approach to a terminus is visible from nowhere else.
TIER1_COVER = {
    "ACACIAS", "AEROPUERTO T-4", "ALONSO MARTINEZ", "ARGÜELLES", "ARTILLEROS",
    "ARTURO SORIA", "AVENIDA DE AMERICA", "AVIACION ESPAÑOLA", "BARAJAS", "CAMPAMENTO",
    "CASA DE CAMPO", "CASA DEL RELOJ", "CUATRO CAMINOS", "EL CAPRICHO", "EL CARMEN",
    "ESTADIO METROPOLITANO", "GUZMAN EL BUENO", "JOAQUIN VILUMBRALES",
    "JUAN DE LA CIERVA", "LA ELIPA", "LA FORTUNA", "LA PESETA", "LACOMA", "LAS ROSAS",
    "LORANCA", "LUCERO", "MAR DE CRISTAL", "MIGUEL HERNANDEZ", "NUEVOS MINISTERIOS",
    "OPERA", "OPORTO", "PACIFICO", "PACO DE LUCIA", "PARQUE DE LOS ESTADOS",
    "PARQUE LISBOA", "PINAR DE CHAMARTIN", "PITIS", "PLAZA DE CASTILLA", "PLAZA ELIPTICA",
    "PRINCIPE DE VERGARA", "PRINCIPE PIO", "PUEBLO NUEVO", "PUERTA DE ARGANDA",
    "SAN NICASIO", "SIERRA DE GUADALUPE", "SOL", "TRES OLIVOS",
    "UNIVERSIDAD REY JUAN CARLOS", "VALDECARROS",
}

# Added by hand for what the cover could not see. LEGAZPI because line 3 has no trips in
# the GTFS, so the cover never considered it — with SOL it spans the line. The rest are
# the Cercanias interchanges, the only places both networks can be shown together.
# ATOCHA is included although it currently returns nothing, so we notice if that changes.
TIER1_EXTRA = {"LEGAZPI", "ATOCHA", "ALUCHE", "MENDEZ ALVARO"}

TIER1 = TIER1_COVER | TIER1_EXTRA

"""The raw archive: every publication we kept, as Renfe sent it, in GCS.

This is the answer to "what if the model is wrong". The modelled tables carry the fields
we chose; the archive carries the bytes, so a reconstruction rule that turns out to be
wrong can be recomputed from source rather than being a permanent loss.

Madrid-filtered, deliberately. Each entity is written exactly as published -- the bytes
of a kept entity are never edited -- but entities belonging to other networks are
dropped. That costs the ability to ever ask what the rest of Spain was doing on a given
day, including the non-Madrid legs of a disruption that spanned networks. Accepted on
2026-09-01: we do not need it, and it roughly halves the archive.

  gs://<bucket>/gtfsrt/<feed>/dt=YYYY-MM-DD/<feed>_<epoch>.json.gz
"""
from __future__ import annotations

import gzip
import json
import logging
from datetime import datetime
from typing import Any

from google.cloud import storage

from .config import Config, credentials

log = logging.getLogger(__name__)


class Archive:
    def __init__(self, cfg: Config, client: storage.Client | None = None) -> None:
        self._cfg = cfg
        self._client = client or storage.Client(project=cfg.project, credentials=credentials())
        self._bucket = self._client.bucket(cfg.bucket)

    def write(self, feed: str, payload: dict[str, Any], observed_at: datetime,
              service_date: str) -> str | None:
        """Store one publication. Returns the gs:// URI, or None if the write failed.

        A failed archive write must never stop the recorder: the BigQuery row is the
        thing that cannot be re-collected, and the archive is the backup of a backup.
        """
        name = f"gtfsrt/{feed}/dt={service_date}/{feed}_{int(observed_at.timestamp())}.json.gz"
        try:
            body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode())
            self._bucket.blob(name).upload_from_string(body, content_type="application/gzip")
            return f"gs://{self._cfg.bucket}/{name}"
        except Exception as exc:  # noqa: BLE001 — archiving is best-effort by design
            log.warning("archive write failed for %s (%s: %s) — continuing",
                        name, type(exc).__name__, exc)
            return None


def madrid_only(payload: dict[str, Any], keep: set[str], key: str) -> dict[str, Any]:
    """The publication with only the entities we keep, each one byte-for-byte unchanged.

    `key` is the entity id field to test against `keep`: trip ids for the vehicle feeds,
    alert ids for alerts.
    """
    entities = []
    for entity in payload.get("entity") or []:
        if key == "alert":
            if entity.get("id") in keep:
                entities.append(entity)
            continue
        holder = entity.get("vehicle") or entity.get("tripUpdate") or {}
        if ((holder.get("trip") or {}).get("tripId")) in keep:
            entities.append(entity)
    return {"header": payload.get("header"), "entity": entities}

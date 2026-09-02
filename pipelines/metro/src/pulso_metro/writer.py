"""Buffer observations, load them into BigQuery, archive the raw responses.

Batched load jobs rather than the Storage Write API, for the same reason as the
Cercanias recorder: load jobs are free, and the quota — 1,500 per table per day counting
retries — is what sets the flush interval at 120s (720/day). Nothing downstream waits on
the batch.

The archive differs from the recorder's, though. That writes one GCS object per feed
publication, which is 4,300 a day. Here it would be ~196,000 objects a day for no
benefit, so a flush writes a single gzipped NDJSON file holding every response in the
window: ~720 files a day, same bytes.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery, storage

from .config import SOURCE, Config, credentials

log = logging.getLogger(__name__)

STATIONS_SQL = """
SELECT station_id, station_name, poll_tier
FROM `{stations}`
WHERE active AND poll_tier IN (1, 2)
ORDER BY poll_tier, station_id
"""


class Writer:
    def __init__(self, cfg: Config, client: bigquery.Client | None = None) -> None:
        self._cfg = cfg
        self._client = client or bigquery.Client(project=cfg.project, credentials=credentials())
        self._table = cfg.table(cfg.ds_facts, "metro_observed_arrivals")
        self._storage: storage.Client | None = None
        self.rows: list[dict[str, Any]] = []
        self.raw: list[dict[str, Any]] = []

    @property
    def pending(self) -> int:
        return len(self.rows)

    def add(self, row: dict[str, Any], raw_payload: dict[str, Any] | None = None) -> None:
        self.rows.append(row)
        if raw_payload is not None and self._cfg.archive_enabled:
            self.raw.append({"station_id": row["station_id"],
                             "observed_at": row["observed_at"], "payload": raw_payload})

    def stations(self) -> list[dict[str, Any]]:
        """The work list. Read from the warehouse so the polling set changes without a deploy."""
        table = self._cfg.table(self._cfg.ds_dimensions, "metro_stations")
        rows = list(self._client.query(STATIONS_SQL.format(stations=table)).result())
        out = [{"station_id": r.station_id, "station_name": r.station_name,
                "poll_tier": r.poll_tier} for r in rows]
        n1 = sum(1 for r in out if r["poll_tier"] == 1)
        log.info("work list: %d stations (%d tier 1, %d tier 2)", len(out), n1, len(out) - n1)
        return out

    def load_dimensions(self, stations: list[dict], line_stops: list[dict], load_id: str) -> None:
        """Replace the station and topology tables. Small, and rebuilt whole each time."""
        now = datetime.now(UTC).isoformat()
        for name, rows in (("metro_stations", stations), ("metro_line_stops", line_stops)):
            target = self._cfg.table(self._cfg.ds_dimensions, name)
            body = io.BytesIO("\n".join(
                json.dumps({**r, "load_id": load_id, "load_time": now}, ensure_ascii=False)
                for r in rows).encode())
            job_config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                # Pin the schema. An inferred one on a truncating write replaces the table
                # definition: every description gone, every REQUIRED column silently
                # NULLABLE, no error and correct row counts.
                schema=self._client.get_table(target).schema,
            )
            self._client.load_table_from_file(body, target, job_config=job_config).result()
            log.info("loaded %-44s %6d rows", target, len(rows))

    def _archive(self, load_id: str) -> str | None:
        if not self.raw:
            return None
        if self._storage is None:
            self._storage = storage.Client(project=self._cfg.project, credentials=credentials())
        service_date = self.rows[0]["service_date"] if self.rows else "unknown"
        name = f"crtm_metro/dt={service_date}/{load_id}.ndjson.gz"
        try:
            body = gzip.compress("\n".join(
                json.dumps(r, ensure_ascii=False) for r in self.raw).encode())
            self._storage.bucket(self._cfg.bucket).blob(name).upload_from_string(
                body, content_type="application/gzip")
            return f"gs://{self._cfg.bucket}/{name}"
        except Exception as exc:  # noqa: BLE001 — archiving is best effort by design
            log.warning("archive write failed (%s: %s) — continuing", type(exc).__name__, exc)
            return None

    def flush(self, load_id: str, *, errors: int = 0) -> int:
        """Write everything buffered, then record the batch in ops.load_runs.

        Buffers clear only on success: a failed load keeps its rows for the next batch,
        which is the right trade for a feed that cannot be re-fetched.
        """
        started = datetime.now(UTC)
        loaded, status, message = 0, "succeeded", None
        archive_uri = self._archive(load_id)
        try:
            if self.rows:
                now = datetime.now(UTC).isoformat()
                body = io.BytesIO("\n".join(
                    json.dumps({**r, "load_id": load_id, "load_time": now}, ensure_ascii=False)
                    for r in self.rows).encode())
                job_config = bigquery.LoadJobConfig(
                    source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                    write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                    schema=self._client.get_table(self._table).schema,
                )
                job = self._client.load_table_from_file(body, self._table, job_config=job_config)
                job.result()
                loaded = job.output_rows or 0
                self.rows.clear()
            self.raw.clear()
        except Exception as exc:
            status, message = "failed", f"{type(exc).__name__}: {exc}"
            log.exception("batch %s failed to load — %d rows held", load_id, self.pending)

        self._record(load_id, started, status, message, loaded, errors, archive_uri)
        return loaded

    def _record(self, load_id: str, started: datetime, status: str, error: str | None,
                loaded: int, errors: int, archive_uri: str | None) -> None:
        table = self._cfg.table(self._cfg.ds_ops, "load_runs")
        try:
            self._client.query(
                f"""INSERT INTO `{table}`
                      (load_id, source, started_at, finished_at, status, source_url,
                       archive_uri, rows_read, rows_loaded, rows_rejected, error_message,
                       load_time)
                    VALUES (@lid, @src, @started, CURRENT_TIMESTAMP(), @status,
                            'https://www.crtm.es/widgets/api/GetStopsTimes.php', @uri,
                            @loaded, @loaded, @errors, @err, CURRENT_TIMESTAMP())""",
                job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("lid", "STRING", load_id),
                    bigquery.ScalarQueryParameter("src", "STRING", SOURCE),
                    bigquery.ScalarQueryParameter("started", "TIMESTAMP", started),
                    bigquery.ScalarQueryParameter("status", "STRING", status),
                    bigquery.ScalarQueryParameter("uri", "STRING", archive_uri),
                    bigquery.ScalarQueryParameter("loaded", "INT64", loaded),
                    bigquery.ScalarQueryParameter("errors", "INT64", errors),
                    bigquery.ScalarQueryParameter("err", "STRING", error),
                ])).result()
        except Exception as exc:  # noqa: BLE001 — bookkeeping never stops the poller
            log.warning("could not record batch %s (%s: %s)", load_id, type(exc).__name__, exc)

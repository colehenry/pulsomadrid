"""Buffer observations and load them into BigQuery in batches.

Write path: batched load jobs, not the Storage Write API and not insertAll.

Load jobs are free, which matters for something that runs forever, and this project
already knows how to pin a schema onto one. The cost is latency, and here there is none
to pay: the live map is served from this process's memory, so nothing downstream is
waiting on the batch. What the batch size is really set by is quota -- BigQuery allows
1,500 load jobs per table per day, counting retries and failures, so a 60s flush at
1,440/day would sit on the ceiling. 120s is 720/day and leaves the same headroom again.

The alternative worth being able to argue: the Storage Write API gives exactly-once
through stream offsets and has no such quota. We do not need it, because the idempotency
key is in the data. (trip_id, observed_at) is unique per publication, so a retry that
duplicates a batch is repairable by a deduplicating read, and a publication seen twice is
discarded before it ever reaches the buffer.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery

from .config import SOURCE, Config, credentials
from .feeds import Anomaly

log = logging.getLogger(__name__)

# Alert versions we already hold, so a restart does not rewrite the current version of
# every open alert as though it were new.
#
# Deliberately unbounded in time. A window looked obvious and was wrong: alerts live for
# months -- one in the first real batch had been running since 2026-03-27 -- and a row is
# written only when the content changes, so the newest version of a long-running alert can
# be far older than any window worth guessing. Getting it wrong is silent: the alert would
# be re-versioned on every restart and its history would show changes that never happened.
# The table takes a few hundred rows a day and this runs once per start, so the scan is
# cheap; it is also why this table does not set require_partition_filter.
LAST_ALERT_VERSIONS_SQL = """
SELECT alert_id, content_hash, version_status
FROM `{alerts}`
QUALIFY ROW_NUMBER() OVER (PARTITION BY alert_id ORDER BY observed_at DESC) = 1
"""


class Writer:
    def __init__(self, cfg: Config, client: bigquery.Client | None = None) -> None:
        self._cfg = cfg
        self._client = client or bigquery.Client(project=cfg.project, credentials=credentials())
        self.trains: list[dict[str, Any]] = []
        self.alerts: list[dict[str, Any]] = []
        self.anomalies: list[Anomaly] = []
        self._trains_table = cfg.table(cfg.ds_facts, "cercanias_observed_trains")
        self._alerts_table = cfg.table(cfg.ds_facts, "cercanias_observed_alerts")

    # ---------------------------------------------------------------- buffering

    def add_trains(self, rows: list[dict[str, Any]]) -> None:
        self.trains.extend(rows)

    def add_alerts(self, rows: list[dict[str, Any]]) -> None:
        self.alerts.extend(rows)

    def add_anomalies(self, anomalies: list[Anomaly]) -> None:
        self.anomalies.extend(anomalies)

    @property
    def pending(self) -> int:
        return len(self.trains) + len(self.alerts)

    # ---------------------------------------------------------------- loading

    def last_alert_versions(self) -> dict[str, str]:
        """content_hash of the newest version of every alert, for restart continuity.

        Without this, every restart writes a fresh 'active' row for every open alert and
        the version history fills with changes that never happened. Returns only alerts
        whose latest row is 'active': one already ended is closed and should be reopened
        as new if it comes back.
        """
        try:
            rows = list(self._client.query(
                LAST_ALERT_VERSIONS_SQL.format(alerts=self._alerts_table)).result())
        except Exception as exc:  # noqa: BLE001 — a cold table or a transient failure
            log.warning("could not read existing alert versions (%s: %s) — starting empty",
                        type(exc).__name__, exc)
            return {}
        state = {r.alert_id: r.content_hash for r in rows if r.version_status == "active"}
        log.info("resumed %d open alert(s) from the warehouse", len(state))
        return state

    def _load(self, table: str, rows: list[dict[str, Any]], load_id: str) -> int:
        """One load job of newline-delimited JSON, with the schema pinned.

        The schema is pinned to what the table already declares. Without it BigQuery
        infers one from the data, and an inferred schema on a truncating write replaces
        the table definition -- every column description gone, every REQUIRED column
        silently NULLABLE, no error, correct row counts. WRITE_APPEND is safer than that
        but pinning also catches a row whose shape has drifted, which is the failure we
        actually want to hear about.
        """
        now = datetime.now(UTC).isoformat()
        body = io.BytesIO("\n".join(
            json.dumps({**r, "load_id": load_id, "load_time": now}, ensure_ascii=False)
            for r in rows).encode())
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            schema=self._client.get_table(table).schema,
        )
        job = self._client.load_table_from_file(body, table, job_config=job_config)
        job.result()
        return job.output_rows or 0

    def flush(self, load_id: str, *, source_timestamp: datetime | None,
              feed_ok: bool, error: str | None = None,
              partial_publications: int = 0) -> dict[str, int]:
        """Write everything buffered, then record the batch in ops.load_runs.

        Buffers are cleared only after the load succeeds. A failed load leaves the rows
        in memory to go out with the next batch, which is the right trade for an
        ephemeral feed: a few minutes of memory against data that cannot be re-fetched.
        """
        counts = {"trains": 0, "alerts": 0, "anomalies": 0}
        started = datetime.now(UTC)
        status, message = "succeeded", error
        try:
            if self.trains:
                counts["trains"] = self._load(self._trains_table, self.trains, load_id)
                self.trains.clear()
            if self.alerts:
                counts["alerts"] = self._load(self._alerts_table, self.alerts, load_id)
                self.alerts.clear()
            if self.anomalies:
                counts["anomalies"] = self._load_anomalies(load_id)
                self.anomalies.clear()
        except Exception as exc:
            status, message = "failed", f"{type(exc).__name__}: {exc}"
            log.exception("batch %s failed to load — %d rows held for the next batch",
                          load_id, self.pending)
        if not feed_ok:
            status = "failed"

        self._record_run(load_id, started, status, message, counts, source_timestamp,
                         partial_publications)
        return counts

    def _load_anomalies(self, load_id: str) -> int:
        table = self._cfg.table(self._cfg.ds_ops, "rejected_rows")
        now = datetime.now(UTC).isoformat()
        rows = [{
            "load_id": load_id,
            "rejected_at": now,
            "source": SOURCE,
            "source_file": a.source_file,
            "raw_row": a.raw_row,
            "reason": a.reason,
        } for a in self.anomalies]
        return self._load(table, rows, load_id)

    def _record_run(self, load_id: str, started: datetime, status: str, error: str | None,
                    counts: dict[str, int], source_timestamp: datetime | None,
                    partial_publications: int = 0) -> None:
        """One ops.load_runs row per batch.

        Written after the fact rather than as a running/finished pair: a batch takes about
        a second, so there is no window in which "still running" is useful information,
        and the pair would double this table's write rate for nothing.

        source_timestamp is what separates the two ways of loading zero rows. The network
        sleeps for about five hours a night and the feed stays live and empty throughout,
        carrying a fresh header timestamp. An outage carries a stale one, or none.

        partial_publications separates a third way: Renfe serving an incomplete snapshot
        with Madrid missing entirely. Those look exactly like the network being asleep
        from inside this table, which is why the count is recorded rather than inferred.
        """
        table = self._cfg.table(self._cfg.ds_ops, "load_runs")
        loaded = counts["trains"] + counts["alerts"]
        try:
            self._client.query(
                f"""INSERT INTO `{table}`
                      (load_id, source, started_at, finished_at, status, source_url,
                       source_timestamp, partial_publications, rows_read, rows_loaded,
                       rows_rejected, error_message, load_time)
                    VALUES (@lid, @source, @started, CURRENT_TIMESTAMP(), @status,
                            'https://gtfsrt.renfe.com/', @src_ts, @partial, @read, @loaded,
                            @rejected, @err, CURRENT_TIMESTAMP())""",
                job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("lid", "STRING", load_id),
                    bigquery.ScalarQueryParameter("source", "STRING", SOURCE),
                    bigquery.ScalarQueryParameter("started", "TIMESTAMP", started),
                    bigquery.ScalarQueryParameter("status", "STRING", status),
                    bigquery.ScalarQueryParameter("src_ts", "TIMESTAMP", source_timestamp),
                    bigquery.ScalarQueryParameter("partial", "INT64", partial_publications),
                    bigquery.ScalarQueryParameter("read", "INT64", loaded),
                    bigquery.ScalarQueryParameter("loaded", "INT64", loaded),
                    bigquery.ScalarQueryParameter("rejected", "INT64", counts["anomalies"]),
                    bigquery.ScalarQueryParameter("err", "STRING", error),
                ])).result()
        except Exception as exc:  # noqa: BLE001 — never let bookkeeping stop the recorder
            log.warning("could not record batch %s in ops.load_runs (%s: %s)",
                        load_id, type(exc).__name__, exc)

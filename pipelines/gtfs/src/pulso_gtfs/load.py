"""Load Parquet into BigQuery.

Load jobs are free; queries are billed per byte scanned. So raw and dimensions are
loaded by truncate-and-replace, and only the facts tables use a small amount of DML
to refresh the dates this feed covers without discarding earlier ones.
"""
from __future__ import annotations

import logging
from pathlib import Path

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from .config import Config

log = logging.getLogger(__name__)

# parquet name -> (dataset attribute, table name)
RAW_TABLES = {
    "raw_renfe_gtfs_agency": "renfe_gtfs_agency",
    "raw_renfe_gtfs_routes": "renfe_gtfs_routes",
    "raw_renfe_gtfs_trips": "renfe_gtfs_trips",
    "raw_renfe_gtfs_stops": "renfe_gtfs_stops",
    "raw_renfe_gtfs_stop_times": "renfe_gtfs_stop_times",
    "raw_renfe_gtfs_calendar": "renfe_gtfs_calendar",
    "raw_renfe_gtfs_shapes": "renfe_gtfs_shapes",
    "raw_renfe_gtfs_transfers": "renfe_gtfs_transfers",
    "raw_crtm_gtfs_stops": "crtm_gtfs_stops",
    "raw_crtm_gtfs_routes": "crtm_gtfs_routes",
}
DIMENSION_TABLES = {
    "cercanias_stations": "cercanias_stations",
    "cercanias_lines": "cercanias_lines",
    "cercanias_stop_patterns": "cercanias_stop_patterns",
}
OPS_TABLES = {"rejected_rows": "rejected_rows"}
FACT_TABLES = {
    "cercanias_scheduled_trips": "cercanias_scheduled_trips",
    "cercanias_scheduled_stops": "cercanias_scheduled_stops",
}


def _load(client: bigquery.Client, path: Path, table: str, mode: str) -> int:
    """Load one Parquet file.

    The schema is pinned to whatever the target table already declares. Without this,
    WRITE_TRUNCATE replaces the table definition with the one inferred from the Parquet
    file, which silently discards every column description and turns every REQUIRED
    column NULLABLE. WRITE_APPEND does not discard them but fails outright on the mode
    mismatch, since DuckDB marks all Parquet columns nullable.
    """
    # Build ParquetOptions first, then assign. Reading job_config.parquet_options
    # returns a COPY, so mutating it through the property silently does nothing and
    # every ARRAY column loads as an empty RECORD.
    parquet_options = bigquery.format_options.ParquetOptions()
    parquet_options.enable_list_inference = True

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=mode,
    )
    job_config.parquet_options = parquet_options
    try:
        job_config.schema = client.get_table(table).schema
    except NotFound:
        pass  # staging tables are created by the load itself; let BigQuery infer
    with path.open("rb") as fh:
        job = client.load_table_from_file(fh, table, job_config=job_config)
    job.result()
    n = client.get_table(table).num_rows
    log.info("loaded %-45s %8d rows", table, n)
    return job.output_rows or 0


def load_all(cfg: Config, paths: dict[str, Path], client: bigquery.Client | None = None) -> dict[str, int]:
    client = client or bigquery.Client(project=cfg.project)
    counts: dict[str, int] = {}

    for key, tbl in RAW_TABLES.items():
        if key in paths:
            counts[f"raw.{tbl}"] = _load(client, paths[key], cfg.table(cfg.ds_raw, tbl),
                                         bigquery.WriteDisposition.WRITE_TRUNCATE)
    for key, tbl in DIMENSION_TABLES.items():
        if key in paths:
            counts[f"dimensions.{tbl}"] = _load(client, paths[key], cfg.table(cfg.ds_dimensions, tbl),
                                                bigquery.WriteDisposition.WRITE_TRUNCATE)

    for key, tbl in OPS_TABLES.items():
        if key in paths:
            counts[f"ops.{tbl}"] = _load(client, paths[key], cfg.table(cfg.ds_ops, tbl),
                                         bigquery.WriteDisposition.WRITE_APPEND)

    # Facts: the feed is a rolling ~30-day window, so replacing the whole table would
    # discard dates we already hold. Replace only the dates this feed covers.
    for key, tbl in FACT_TABLES.items():
        if key not in paths:
            continue
        target = cfg.table(cfg.ds_facts, tbl)
        staging = f"{target}__staging"
        _load(client, paths[key], staging, bigquery.WriteDisposition.WRITE_TRUNCATE)
        client.query(f"""
            DELETE FROM `{target}`
            WHERE service_date IN (SELECT DISTINCT service_date FROM `{staging}`)
        """).result()
        client.query(f"INSERT INTO `{target}` SELECT * FROM `{staging}`").result()
        client.delete_table(staging, not_found_ok=True)
        counts[f"facts.{tbl}"] = client.get_table(target).num_rows
        log.info("merged %-45s %8d rows total", target, counts[f"facts.{tbl}"])

    return counts


def sweep_stale_runs(cfg: Config, older_than_hours: int = 6,
                     client: bigquery.Client | None = None) -> int:
    """Mark runs abandoned if they are still 'running' long after they started.

    A process killed mid-flight leaves its row at 'running' forever, which makes any
    freshness check that reads status wrong. Runs here take about four minutes, so
    anything still running after six hours is dead.
    """
    client = client or bigquery.Client(project=cfg.project)
    job = client.query(
        f"""UPDATE `{cfg.table(cfg.ds_ops, 'load_runs')}`
            SET status = 'abandoned',
                error_message = 'no completion recorded; process presumed killed'
            WHERE status = 'running'
              AND started_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @h HOUR)""",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("h", "INT64", older_than_hours)]))
    job.result()
    n = job.num_dml_affected_rows or 0
    if n:
        log.warning("swept %d stale load_run(s) to 'abandoned'", n)
    return n


def start_run(cfg: Config, load_id: str, source: str, url: str,
              client: bigquery.Client | None = None) -> None:
    """Record the run as started.

    Inserted with DML rather than a streaming insert: rows in BigQuery's streaming
    buffer cannot be UPDATEd or DELETEd for roughly 90 minutes, so finish_run would
    fail with "would affect rows in the streaming buffer".
    """
    client = client or bigquery.Client(project=cfg.project)
    client.query(
        f"""INSERT INTO `{cfg.table(cfg.ds_ops, 'load_runs')}`
              (load_id, source, started_at, status, source_url, load_time)
            VALUES (@lid, @src, CURRENT_TIMESTAMP(), 'running', @url, CURRENT_TIMESTAMP())""",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("lid", "STRING", load_id),
            bigquery.ScalarQueryParameter("src", "STRING", source),
            bigquery.ScalarQueryParameter("url", "STRING", url),
        ])).result()


def finish_run(cfg: Config, load_id: str, *, status: str, sha: str | None = None,
               archive_uri: str | None = None, rows_read: int = 0, rows_loaded: int = 0,
               rows_rejected: int = 0, error: str | None = None,
               client: bigquery.Client | None = None) -> None:
    client = client or bigquery.Client(project=cfg.project)
    client.query(
        f"""UPDATE `{cfg.table(cfg.ds_ops, 'load_runs')}`
            SET finished_at = CURRENT_TIMESTAMP(), status = @status,
                source_file_hash = @sha, archive_uri = @uri,
                rows_read = @read, rows_loaded = @loaded, rows_rejected = @rejected,
                error_message = @err
            WHERE load_id = @lid""",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("sha", "STRING", sha),
            bigquery.ScalarQueryParameter("uri", "STRING", archive_uri),
            bigquery.ScalarQueryParameter("read", "INT64", rows_read),
            bigquery.ScalarQueryParameter("loaded", "INT64", rows_loaded),
            bigquery.ScalarQueryParameter("rejected", "INT64", rows_rejected),
            bigquery.ScalarQueryParameter("err", "STRING", error),
            bigquery.ScalarQueryParameter("lid", "STRING", load_id),
        ])).result()


def previous_hash(cfg: Config, source: str, client: bigquery.Client | None = None) -> str | None:
    """Hash of the last file we successfully loaded, for skipping unchanged feeds."""
    client = client or bigquery.Client(project=cfg.project)
    rows = list(client.query(
        f"""SELECT source_file_hash FROM `{cfg.table(cfg.ds_ops, 'load_runs')}`
            WHERE source = @s AND status = 'succeeded' AND source_file_hash IS NOT NULL
            ORDER BY started_at DESC LIMIT 1""",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("s", "STRING", source)])).result())
    return rows[0].source_file_hash if rows else None

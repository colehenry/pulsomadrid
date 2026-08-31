"""Orchestrates one ingestion run.

  download -> archive to GCS -> transform in DuckDB -> Parquet -> BigQuery
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from . import download, load, transform
from .config import CRTM_GTFS_URL, RENFE_GTFS_URL, Config

log = logging.getLogger("pulso_gtfs")
SOURCE = "renfe_gtfs"


def run(*, dry_run: bool = False, force: bool = False, workdir: Path | None = None) -> int:
    cfg = Config()
    load_id = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    tmp = workdir or Path(tempfile.mkdtemp(prefix="pulso-gtfs-"))
    tmp.mkdir(parents=True, exist_ok=True)
    log.info("load_id=%s workdir=%s dry_run=%s", load_id, tmp, dry_run)

    if not dry_run:
        load.sweep_stale_runs(cfg)
        load.start_run(cfg, load_id, SOURCE, RENFE_GTFS_URL)

    try:
        renfe_zip, sha = download.fetch(RENFE_GTFS_URL, tmp / "renfe.zip")
        crtm_zip, _ = download.fetch(CRTM_GTFS_URL, tmp / "crtm.zip")

        if not dry_run and not force and load.previous_hash(cfg, SOURCE) == sha:
            log.info("feed unchanged (sha256 %s) — nothing to do", sha[:12])
            load.finish_run(cfg, load_id, status="succeeded", sha=sha)
            return 0

        archive_uri = None
        if not dry_run:
            archive_uri = download.archive(renfe_zip, cfg.bucket, f"{SOURCE}/madrid")
            download.archive(crtm_zip, cfg.bucket, "crtm_gtfs/cercanias")

        renfe_dir, crtm_dir = tmp / "renfe", tmp / "crtm"
        download.extract(renfe_zip, renfe_dir)
        download.extract(crtm_zip, crtm_dir)

        con = transform.connect(renfe_dir, crtm_dir)
        transform.build_raw(con)
        transform.build_trimmed(con)
        transform.build_patterns(con)
        transform.build_station_join(con)
        transform.build_outputs(con, load_id, sha)
        transform.build_rejects(con, load_id)
        paths = transform.export(con, tmp / "parquet")

        rows_read = con.execute("SELECT COUNT(*) FROM raw_renfe_gtfs_stop_times").fetchone()[0]
        rejected = con.execute("SELECT COUNT(*) FROM out_rejected_rows").fetchone()[0]
        summarise(con)

        if dry_run:
            log.info("dry run — %d parquet files in %s, nothing loaded", len(paths), tmp / "parquet")
            return 0

        counts = load.load_all(cfg, paths)
        load.finish_run(cfg, load_id, status="succeeded", sha=sha, archive_uri=archive_uri,
                        rows_read=rows_read, rows_loaded=sum(counts.values()),
                        rows_rejected=rejected)
        log.info("done: %d tables, %d rows", len(counts), sum(counts.values()))
        return 0

    except Exception as exc:
        log.exception("ingestion failed")
        if not dry_run:
            load.finish_run(cfg, load_id, status="failed", error=str(exc)[:1000])
        return 1


def summarise(con) -> None:
    """Log the shape of what we built, before anything is loaded."""
    for label, sql in [
        ("madrid trips", "SELECT COUNT(*) FROM out_cercanias_scheduled_trips"),
        ("madrid stops", "SELECT COUNT(*) FROM out_cercanias_scheduled_stops"),
        ("stations", "SELECT COUNT(*) FROM out_cercanias_stations"),
        ("lines", "SELECT COUNT(*) FROM out_cercanias_lines"),
        ("stop patterns", "SELECT COUNT(*) FROM out_cercanias_stop_patterns"),
        ("line shapes", "SELECT COUNT(*) FROM out_cercanias_line_shapes"),
        ("stations w/ CRTM zone", "SELECT COUNT(crtm_zone_id) FROM out_cercanias_stations"),
        ("REJECTED", "SELECT COUNT(*) FROM out_rejected_rows"),
    ]:
        log.info("  %-16s %8d", label, con.execute(sql).fetchone()[0])


def cli() -> int:
    p = argparse.ArgumentParser(description="Ingest Madrid Cercanias static GTFS")
    p.add_argument("--dry-run", action="store_true", help="build Parquet, load nothing")
    p.add_argument("--force", action="store_true", help="load even if the feed is unchanged")
    p.add_argument("--workdir", type=Path, help="keep intermediate files here")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    return run(dry_run=a.dry_run, force=a.force, workdir=a.workdir)


if __name__ == "__main__":
    sys.exit(cli())

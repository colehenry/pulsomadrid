"""The recorder: poll Renfe's realtime feeds forever, append every observation.

A long-lived process, not a scheduled job. Railway cron has a five-minute minimum and a
train stands in a station for 30-60 seconds, so a cron recorder would miss most
STOPPED_AT events -- the arrival evidence the whole reliability model rests on. (Cron is
still the right shape for the static GTFS pipeline, which runs four minutes and exits.)

  poll every 10s -> new publication? -> parse -> buffer -> load every 120s

Why 10s against a 20s publication: the interval jitters between 16 and 24 seconds and a
32s gap was observed, so a 20s poll drifts against it and drops publications. Polling
faster costs three cheap GETs and writes nothing extra, because a header timestamp we
have already seen is discarded before parsing.

Run it: `uv run pulso-recorder` (add --once to do a single cycle, --dry-run to parse and
log without writing anything).
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from . import feeds
from .archive import Archive, madrid_only
from .config import FEED_URLS, Config
from .feeds import (
    alerts_look_partial,
    diff_alert_versions,
    header_timestamp,
    is_partial_snapshot,
    parse_alerts,
    parse_trains,
)
from .trips import TripLookup
from .writer import Writer

log = logging.getLogger("pulso_recorder")

# A publication older than this is a feed that has stopped updating, not a quiet network.
# The feeds publish every ~20s around the clock, including through the five hours a night
# when the network sleeps and they carry a valid header with no entities at all.
STALE_AFTER_SECONDS = 300


def _configure_logging() -> None:
    """Send this package's log lines to the console.

    Configured on the package logger directly rather than through basicConfig, which does
    nothing when the root logger already has handlers. The same omission once made every
    log line in the API disappear.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s %(message)s", datefmt="%H:%M:%S"))
    package = logging.getLogger(__name__.split(".")[0])
    package.handlers.clear()
    package.addHandler(handler)
    package.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    package.propagate = False


class Recorder:
    def __init__(self, cfg: Config, *, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.http = httpx.Client(timeout=cfg.upstream_timeout_seconds, follow_redirects=True,
                                 headers={"User-Agent": "pulso-madrid-recorder/0.1"})
        self.lookup = TripLookup(cfg)
        self.writer = Writer(cfg)
        self.archive = Archive(cfg) if cfg.archive_enabled and not dry_run else None
        self.alert_state: dict[str, str] = {}
        self.last_header: datetime | None = None
        self.publications = 0
        self.partial_publications = 0
        self.last_madrid_trains = 0
        self._stopping = False

    def start(self) -> None:
        self.lookup.refresh()
        if not self.dry_run:
            self.alert_state = self.writer.last_alert_versions()

    def stop(self, *_: Any) -> None:
        """Flush on SIGTERM rather than dropping the buffer.

        Railway sends SIGTERM on every redeploy. Without this, up to two minutes of
        observations are lost on each one -- and unlike a schedule, they cannot be
        fetched again afterwards.
        """
        log.info("shutdown requested — flushing %d buffered row(s)", self.writer.pending)
        self._stopping = True

    # ---------------------------------------------------------------- one cycle

    def fetch(self, name: str) -> dict[str, Any] | None:
        try:
            response = self.http.get(FEED_URLS[name])
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("%s fetch failed (%s: %s)", name, type(exc).__name__, exc)
            return None

    def cycle(self) -> bool:
        """Fetch once. Returns whether the upstream looked healthy."""
        payloads = {name: self.fetch(name) for name in FEED_URLS}
        vehicles, updates, alerts = (payloads["vehicle_positions"], payloads["trip_updates"],
                                     payloads["alerts"])
        if vehicles is None and updates is None:
            return False

        observed_at = header_timestamp(vehicles or updates or {})
        if observed_at is None:
            log.warning("publication carried no header timestamp — skipped")
            return False

        age = (datetime.now(UTC) - observed_at).total_seconds()
        if age > STALE_AFTER_SECONDS:
            log.error("feed is stale: header is %.0fs old — treating as an outage", age)
            return False

        if observed_at == self.last_header:
            return True  # already recorded: the same publication, not a new one
        self.last_header = observed_at
        self.publications += 1

        rows, anomalies = parse_trains(vehicles, updates, self.lookup.trips,
                                       self.lookup.stations, observed_at)
        n_entities = len((vehicles or {}).get("entity") or [])

        # Renfe serves incomplete national snapshots from some of its backends. The whole
        # publication is skipped, not just its trains, because the alerts in it come from
        # the same backend. See feeds.is_partial_snapshot for the measurements.
        if is_partial_snapshot(len(rows), n_entities, self.last_madrid_trains):
            self.partial_publications += 1
            log.warning("%s: partial snapshot — %d entities, 0 of them Madrid, "
                        "previous publication had %d. Skipped",
                        observed_at.isoformat(timespec="seconds"), n_entities,
                        self.last_madrid_trains)
            return True

        self.last_madrid_trains = len(rows)
        alert_rows = self._alert_rows(alerts, observed_at)

        cancelled = sum(1 for r in rows if r.get("schedule_relationship") == "CANCELED")
        stopped = sum(1 for r in rows if r.get("current_status") == "STOPPED_AT")
        if not rows and n_entities == 0:
            # A live feed with no entities is the network asleep, not an outage. The
            # header timestamp above is what proves the difference.
            log.info("%s: feed live and empty — no trains running",
                     observed_at.isoformat(timespec="seconds"))
        else:
            log.info("%s: %d madrid of %d entities (%d stopped, %d cancelled), "
                     "%d alert version(s), %d anomal(ies)",
                     observed_at.isoformat(timespec="seconds"), len(rows), n_entities,
                     stopped, cancelled, len(alert_rows), len(anomalies))

        if self.dry_run:
            return True

        self.writer.add_trains(rows)
        self.writer.add_alerts(alert_rows)
        self.writer.add_anomalies(anomalies)
        self._archive(payloads, rows, alert_rows, observed_at)
        return True

    def _alert_rows(self, payload: dict[str, Any] | None,
                    observed_at: datetime) -> list[dict[str, Any]]:
        """New, changed and ended alert versions since the last publication."""
        if payload is None:
            return []
        current = parse_alerts(payload, observed_at)

        if alerts_look_partial(current, self.alert_state):
            log.warning("alerts payload named no Madrid alert while %d are open — "
                        "treating as a partial snapshot, not as endings",
                        len(self.alert_state))
            return []

        rows = diff_alert_versions(current, self.alert_state, observed_at)
        self.alert_state = {a: r["content_hash"] for a, r in current.items()}
        for row in rows:
            log.info("alert %s %s: %s", row["alert_id"], row["version_status"],
                     ",".join(row["line_ids"]) or "-")
        return rows

    def _archive(self, payloads: dict[str, Any], rows: list[dict[str, Any]],
                 alert_rows: list[dict[str, Any]], observed_at: datetime) -> None:
        if self.archive is None:
            return
        trip_ids = {r["trip_id"] for r in rows}
        service_date = feeds.service_date_for(observed_at).isoformat()
        for name, key, keep in (("vehicle_positions", "trip", trip_ids),
                                ("trip_updates", "trip", trip_ids),
                                ("alerts", "alert", {r["alert_id"] for r in alert_rows})):
            payload = payloads.get(name)
            if payload is None or not keep:
                continue
            self.archive.write(name, madrid_only(payload, keep, key), observed_at, service_date)

    # ---------------------------------------------------------------- the loop

    def run(self, *, once: bool = False) -> int:
        self.start()
        last_flush = last_refresh = time.monotonic()
        feed_ok = True

        while True:
            cycle_started = time.monotonic()
            feed_ok = self.cycle()

            now = time.monotonic()
            if not self.dry_run and (self._stopping or once
                                     or now - last_flush >= self.cfg.flush_seconds):
                counts = self.writer.flush(
                    load_id=f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}",
                    source_timestamp=self.last_header, feed_ok=feed_ok,
                    partial_publications=self.partial_publications)
                log.info("batch loaded: %d train row(s), %d alert row(s), %d anomal(ies), "
                         "%d publication(s) since last batch, %d partial snapshot(s) skipped",
                         counts["trains"], counts["alerts"], counts["anomalies"],
                         self.publications, self.partial_publications)
                self.publications = 0
                self.partial_publications = 0
                last_flush = now

            if self._stopping or once:
                return 0

            if now - last_refresh >= self.cfg.trips_refresh_seconds:
                self.lookup.refresh()
                last_refresh = now

            time.sleep(max(0.0, self.cfg.poll_seconds - (time.monotonic() - cycle_started)))


def cli(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Record Renfe GTFS-RT observations for Madrid")
    parser.add_argument("--once", action="store_true", help="one cycle, then flush and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and parse, write nothing to BigQuery or GCS")
    args = parser.parse_args(argv)

    cfg = Config()
    recorder = Recorder(cfg, dry_run=args.dry_run)
    signal.signal(signal.SIGTERM, recorder.stop)
    signal.signal(signal.SIGINT, recorder.stop)
    log.info("recorder starting: poll=%.0fs flush=%.0fs dry_run=%s archive=%s",
             cfg.poll_seconds, cfg.flush_seconds, args.dry_run, cfg.archive_enabled)
    try:
        return recorder.run(once=args.once)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(cli())

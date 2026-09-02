"""The Metro poller: ask CRTM what is coming, at every station, forever.

One tick drives everything. Every 30 seconds it polls all 53 tier-1 stations plus a
rotating slice of tier 2, so the request rate is flat — about 2.3/second — rather than
bursting. Tier 2 is a cursor that advances; a full sweep takes 5 minutes.

Why not one loop per tier: two loops would collide and produce bursts, and the whole
point of the rate is to be a quiet guest on an endpoint nobody documented for us.

  tick 30s -> poll tier 1 + slice of tier 2 -> buffer -> load every 120s

Run it: `uv run pulso-metro` (--once for a single tick, --dry-run to parse and log
without writing, load-stations to rebuild the work list).
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import ARRIVALS_URL, USER_AGENT, Config
from .poll import parse
from .stations import build
from .writer import Writer

log = logging.getLogger("pulso_metro")

# Metro runs roughly 06:00-01:30. Outside that the endpoint answers with empty stations,
# which is true but not worth 2.3 requests a second all night.
SERVICE_START_HOUR, SERVICE_END_HOUR = 5, 2


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s %(message)s", datefmt="%H:%M:%S"))
    package = logging.getLogger(__name__.split(".")[0])
    package.handlers.clear()
    package.addHandler(handler)
    package.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    package.propagate = False


class MetroPoller:
    def __init__(self, cfg: Config, *, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self.http = httpx.Client(timeout=cfg.timeout_seconds, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT})
        self.writer = Writer(cfg)
        self.tier1: list[dict[str, Any]] = []
        self.tier2: list[dict[str, Any]] = []
        self.cursor = 0
        self.errors = 0
        self._stopping = False

    def stop(self, *_: Any) -> None:
        log.info("shutdown requested — flushing %d buffered row(s)", self.writer.pending)
        self._stopping = True

    def refresh_stations(self) -> None:
        stations = self.writer.stations()
        self.tier1 = [s for s in stations if s["poll_tier"] == 1]
        self.tier2 = [s for s in stations if s["poll_tier"] == 2]
        self.cursor = 0

    def _slice_size(self) -> int:
        """How many tier-2 stations to cover per tick so a sweep finishes on time."""
        ticks = max(1, int(self.cfg.sweep_seconds / self.cfg.tick_seconds))
        return max(1, -(-len(self.tier2) // ticks))   # ceiling division

    def fetch(self, station: dict[str, Any]) -> tuple[dict, dict | None]:
        try:
            r = self.http.get(ARRIVALS_URL, params={
                "codStop": station["station_id"], "type": 1,
                "orderBy": 2, "stopTimesByIti": 3})
            r.raise_for_status()
            return station, r.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("%s (%s) failed: %s: %s", station["station_id"],
                        station["station_name"], type(exc).__name__, exc)
            return station, None

    def tick(self) -> None:
        if not self.tier1 and not self.tier2:
            return
        size = self._slice_size()
        batch = self.tier1 + [self.tier2[(self.cursor + i) % len(self.tier2)]
                              for i in range(min(size, len(self.tier2)))] if self.tier2 \
            else list(self.tier1)
        self.cursor = (self.cursor + size) % max(1, len(self.tier2))

        observed_at = datetime.now(UTC).replace(microsecond=0)
        ok = failed = arrivals = 0
        with ThreadPoolExecutor(max_workers=self.cfg.concurrency) as pool:
            for station, payload in pool.map(self.fetch, batch):
                if payload is None:
                    failed += 1
                    continue
                row = parse(payload, station["station_id"], observed_at, station["poll_tier"])
                arrivals += row["n_arrivals"]
                ok += 1
                if not self.dry_run:
                    self.writer.add(row, payload)
        self.errors += failed
        log.info("%s: %d stations polled, %d arrivals, %d failed",
                 observed_at.isoformat(timespec="seconds"), ok, arrivals, failed)

    def in_service_hours(self) -> bool:
        from .poll import MADRID
        hour = datetime.now(UTC).astimezone(MADRID).hour
        return hour >= SERVICE_START_HOUR or hour < SERVICE_END_HOUR

    def run(self, *, once: bool = False) -> int:
        self.refresh_stations()
        last_flush = last_refresh = time.monotonic()
        while True:
            started = time.monotonic()
            if once or self.in_service_hours():
                self.tick()
            now = time.monotonic()
            if not self.dry_run and (self._stopping or once
                                     or now - last_flush >= self.cfg.flush_seconds):
                if self.writer.pending or self.errors:
                    loaded = self.writer.flush(
                        load_id=f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}",
                        errors=self.errors)
                    log.info("batch loaded: %d row(s), %d failed request(s)", loaded, self.errors)
                    self.errors = 0
                last_flush = now
            if self._stopping or once:
                return 0
            if now - last_refresh >= self.cfg.stations_refresh_seconds:
                self.refresh_stations()
                last_refresh = now
            time.sleep(max(0.0, self.cfg.tick_seconds - (time.monotonic() - started)))


def cli(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Record CRTM Metro arrivals for Madrid")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "load-stations"],
                        help="'load-stations' rebuilds the station and topology tables")
    parser.add_argument("--once", action="store_true", help="one tick, then flush and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and parse, write nothing")
    args = parser.parse_args(argv)
    cfg = Config()

    if args.command == "load-stations":
        writer = Writer(cfg)
        stations, line_stops = build()
        if args.dry_run:
            log.info("dry run: %d stations, %d line stops — nothing written",
                     len(stations), len(line_stops))
            return 0
        writer.load_dimensions(
            stations, line_stops,
            load_id=f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}")
        return 0

    poller = MetroPoller(cfg, dry_run=args.dry_run)
    signal.signal(signal.SIGTERM, poller.stop)
    signal.signal(signal.SIGINT, poller.stop)
    log.info("metro poller starting: tick=%.0fs sweep=%.0fs flush=%.0fs dry_run=%s",
             cfg.tick_seconds, cfg.sweep_seconds, cfg.flush_seconds, args.dry_run)
    try:
        return poller.run(once=args.once)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(cli())

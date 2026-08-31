"""The read API behind the map.

  GET /api/network   the network: 95 stations, 12 lines, route geometry. Changes daily.
  GET /api/vehicles  live Cercanias trains, filtered to Madrid and named from the schedule.
  GET /health        whether the warehouse snapshot loaded.

Run it with: scripts/dev.sh api   →   http://localhost:8000/docs
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from .config import Config
from .models import NetworkResponse, VehiclesResponse
from .realtime import VehicleFeed
from .warehouse import Snapshot, read_snapshot

log = logging.getLogger("pulso_api")

cfg = Config()


async def _refresh_forever(app: FastAPI) -> None:
    """Rebuild the warehouse snapshot on a timer.

    The static feed changes at most daily, but the trip lookup is bounded by service
    date, so it must be rebuilt before the date rolls over in Madrid.
    """
    while True:
        await asyncio.sleep(cfg.warehouse_refresh_seconds)
        try:
            app.state.snapshot = await asyncio.to_thread(read_snapshot, cfg)
        except Exception:
            log.exception("snapshot refresh failed — keeping the previous one")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.snapshot = None
    app.state.feed = VehicleFeed(cfg.vehicle_positions_url,
                                 timeout=cfg.upstream_timeout_seconds,
                                 cache_seconds=cfg.vehicles_cache_seconds)
    try:
        # Blocking client, so off the event loop. A failure here must not stop the
        # process starting: /health then says why, instead of a container that crash-loops.
        app.state.snapshot = await asyncio.to_thread(read_snapshot, cfg)
    except Exception:
        log.exception("could not read the warehouse snapshot at startup")
    task = asyncio.create_task(_refresh_forever(app))
    try:
        yield
    finally:
        task.cancel()
        await app.state.feed.close()


app = FastAPI(
    title="Pulso Madrid API",
    description="Static Cercanias network and live vehicle positions for the Madrid map.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(cfg.cors_origins),
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _snapshot() -> Snapshot:
    snapshot = app.state.snapshot
    if snapshot is None:
        raise HTTPException(status_code=503,
                            detail="warehouse snapshot unavailable; check BigQuery credentials")
    return snapshot


@app.get("/health")
def health() -> dict[str, object]:
    snapshot: Snapshot | None = app.state.snapshot
    return {
        "status": "ok" if snapshot else "degraded",
        "stations": len(snapshot.stations) if snapshot else 0,
        "lines": len(snapshot.lines) if snapshot else 0,
        "trips": len(snapshot.trips) if snapshot else 0,
        "snapshot_loaded_at": snapshot.loaded_at.isoformat() if snapshot else None,
    }


@app.get("/api/network", response_model=NetworkResponse,
         summary="The Cercanias network: stations, lines and route geometry")
def network(response: Response) -> NetworkResponse:
    snapshot = _snapshot()
    # Static for a day. Cached hard so panning the map never costs a query.
    response.headers["Cache-Control"] = "public, max-age=3600"
    return NetworkResponse(stations=snapshot.stations, lines=snapshot.lines,
                           loaded_at=snapshot.loaded_at)


@app.get("/api/vehicles", response_model=VehiclesResponse,
         summary="Live Cercanias trains in Madrid")
async def vehicles(response: Response) -> VehiclesResponse:
    snapshot = _snapshot()
    feed = app.state.feed
    current = await feed.current(snapshot)
    response.headers["Cache-Control"] = "no-store"
    if current is None:
        # Upstream has never answered since this process started. 200 with an empty list,
        # not an error: the page should still draw the network and say the data is old.
        return VehiclesResponse(observed_at=None, vehicles=[], upstream_ok=False)
    return VehiclesResponse(observed_at=current.observed_at, vehicles=current.vehicles,
                            upstream_ok=feed.upstream_ok)

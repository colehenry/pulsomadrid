# API — the read layer behind the map

FastAPI. Serves the static Cercanías network and live train positions.

```bash
scripts/dev.sh api        # http://localhost:8000/docs
```

| Endpoint | What it is | Cache |
|---|---|---|
| `GET /api/network` | 95 stations, 12 lines, 23 route geometries | `max-age=3600` |
| `GET /api/vehicles` | Live Madrid trains, named from our schedule | `no-store` |
| `GET /health` | Whether the warehouse snapshot loaded | — |

## Why nothing queries BigQuery per request

BigQuery bills per byte scanned. `/api/vehicles` is polled every 30 seconds by every
open tab, so a query on that path would turn page views into money. Instead the whole
warehouse snapshot — stations, lines, geometry, and a `trip_id → line/destination`
lookup — is read once at startup and refreshed every 15 minutes (`WAREHOUSE_REFRESH_SECONDS`).

The three startup queries read about 1 MB together. The trip lookup is bounded to
yesterday–tomorrow in Madrid local time and BigQuery prunes to those partitions:
250 KB, not the whole 37,608-row table. Yesterday is in the window because 748 Madrid
trips cross midnight, so a train that departed before it can still be moving after it.

## Why the Madrid filter is a join, not a bounding box

Renfe's realtime feed is national — 363 vehicles in the sample taken while this was
written, of which 126 were Madrid. Vehicles are kept only when their `trip_id` is in our
own trip set, which is the same reasoning that filters the static feed on
`route_id LIKE '10T%'` rather than on a line name: `C1` exists in eleven Spanish
networks, and a box around Madrid also contains services that are not ours.

## What comes from where

The feed carries a position, a status, a `stop_id` and a free-text label. It does **not**
carry a destination, and its label is not a data contract. So line, destination and
`calls_at` all come from `facts.cercanias_scheduled_trips` joined to
`dimensions.cercanias_stop_patterns`; only the position, the status and the `stop_id`
come from Renfe.

`at_station` is the display name of the feed's `stop_id` — the station the train is
standing at when `status` is `STOPPED_AT`, and the one it is heading for otherwise.

## Protecting a third-party feed we do not own

The whole map depends on one Renfe endpoint, so `/api/vehicles` has three guards.

**A 10-second cache**, so a burst of page loads is not a burst of upstream requests.

**A single-flight lock**, so concurrent requests that all miss the cache produce one
fetch between them rather than one each. This is the one that is easy to leave out and
hard to notice missing: a cache on its own only helps requests arriving *after* a fetch
has finished, and every request arriving during one still fetches. Not a rare race —
Cloud Run puts up to 80 concurrent requests on an instance, the cache expires every ten
seconds, and a cold instance starts with nothing cached at all. It was missing until a
log line showed three identical parses of the same feed timestamp.

**A last-good fallback**, so an outage replays the previous snapshot with its original
`observed_at` and `upstream_ok: false` rather than failing. The page draws the network
and says the data is old. If the upstream has never answered since the process started,
the response is `200` with an empty list, not an error.

## `destination` and `towards` are different questions

`destination` is the last station of this trip's stopping pattern — where this train
stops. `towards` is `dimensions.cercanias_stop_patterns.direction_towards_station_id`,
the terminus its direction heads for, which is what a platform sign means by "towards
Humanes". A C5 terminating at Fuenlabrada is still heading towards Humanes, and that gap
is the informative case, so both are served.

## Configuration

Everything has a working default; `.env` at the repo root overrides.

| Variable | Default |
|---|---|
| `GCP_PROJECT_ID` | `pulso-madrid` |
| `RENFE_GTFS_RT_URL` | `https://gtfsrt.renfe.com/vehicle_positions.json` |
| `WAREHOUSE_REFRESH_SECONDS` | `900` |
| `VEHICLES_CACHE_SECONDS` | `10` |
| `UPSTREAM_TIMEOUT_SECONDS` | `10` |
| `API_CORS_ORIGINS` | `http://localhost:3000` |

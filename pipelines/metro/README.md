# pulso-metro

Polls CRTM for Metro arrivals at every station in Madrid and appends them to BigQuery.

Metro has no GTFS-RT and no timetable to be late against — it is frequency-based — so
the metric is **headway and wait time**, not delay. Findings, measurements and the
reasoning behind every number here: `docs/data/metro-rt.md`.

```bash
uv run pulso-metro load-stations --dry-run   # rebuild the work list, write nothing
uv run pulso-metro load-stations             # …for real; run this first
uv run pulso-metro --dry-run                 # poll and parse, write nothing
uv run pulso-metro --once                    # one tick, flush, exit
uv run pulso-metro                           # the real thing
```

## What it writes

| Table | Grain |
|---|---|
| `dimensions.metro_stations` | one row per physical station (242) — also the poller's work list |
| `dimensions.metro_line_stops` | one row per (branch, direction, position) — the network's shape (592) |
| `facts.metro_observed_arrivals` | one row per station per poll |
| `ops.load_runs` | one row per batch, `source = 'crtm_metro'` |
| `gs://…/crtm_metro/` | one gzipped NDJSON per batch, holding every raw response |

## The numbers, and where they came from

Measured on 2026-09-01 and 02, not documented — there is no documentation.

- **A station returns at most 3 upcoming trains** per (line, direction, destination). A
  count cap, not a time window, so at peak headways of 2–4 min it sees only ~11 minutes
  of track — and peak is therefore the binding case, not the easy one.
- **A station only sees the track behind it.** So polled stations must be ≤11 minutes of
  travel apart, and **termini must be polled**: the final approach to one is visible from
  nowhere else. 49 stations cover 98% of the network; 4 more are added by hand.
- **Tier 2 sweeps every other station every 5 minutes**, which is enough to measure wait
  times everywhere because headway is readable from a single poll — consecutive
  predictions *are* the gap.
- **~2.3 requests/second, ~165,000/day.** Polling all 242 at 30s would be ~700,000.

## Things that will surprise you

- **CRTM codes platforms, not stations.** 291 codes for 242 stations; Sol is `4_12`,
  `4_35` and `4_48`. The endpoint aggregates by station, so exactly one code per station
  is polled — the others would count the same arrivals two or three times.
- **Predictions have one-minute resolution.** Every gap measured was a multiple of 60s.
  Two trains listed at the same instant are two real trains under a minute apart, not a
  duplicate. Treat a zero gap as censored at <60s.
- **Line 3 has no trips in the Metro GTFS at all** — the route exists with nothing in it.
  ParadasPorItinerario carries it, which is why the topology comes from there.
- **`times` is an empty object, not an empty list**, when a station has no upcoming
  trains. Normal near closing.
- **Station 4_16 (Atocha) returns nothing**, twice, twelve hours apart, while every other
  station works. Unexplained; it is polled anyway so we notice if it changes.
- **The idempotency key is ours, not theirs.** CRTM's `actualDate` changes on every
  request, so a retry would look like a new observation. The key is `(station_id, tick)`.

## Being a guest

This endpoint is undocumented, unlicensed and unsupported. The poller identifies itself
in its User-Agent with a contact URL, keeps a flat request rate rather than bursting,
and archives raw responses from day one in case the shape changes without warning.

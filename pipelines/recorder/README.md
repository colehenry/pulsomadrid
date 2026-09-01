# pulso-recorder

Polls Renfe's GTFS-RT feeds and appends every Madrid observation to BigQuery, forever.

Renfe publishes a live snapshot with no archive and no backfill. A day not recorded is a
day that cannot be bought back at any price — see `docs/decisions/0004`.

```bash
uv run pulso-recorder --dry-run    # fetch and parse, write nothing
uv run pulso-recorder --once       # one cycle, flush, exit
uv run pulso-recorder              # the real thing
```

## What it writes

| Table | Grain |
|---|---|
| `facts.cercanias_observed_trains` | one row per Madrid train per feed publication |
| `facts.cercanias_observed_alerts` | one row per version of one Madrid alert |
| `ops.load_runs` | one row per batch |
| `ops.rejected_rows` | one row per anomaly, with the observation still kept |
| `gs://…/gtfsrt/…` | every kept publication, Madrid entities byte-for-byte |

Schema and the reasoning behind every column: `ddls.sql`.

## The numbers, and where they came from

All measured against the live feed on 2026-09-01, over 65 publications and 3,938 Madrid
trip-observations. None of them are guesses.

- **Publication is ~20s**, jitter 16–24s, one 32s gap. So **poll at 10s** and deduplicate
  on the header timestamp. A 20s poll drifts against the jitter and drops publications;
  extra polls are free because a repeated header timestamp never reaches the buffer.
- **Flush every 120s.** BigQuery allows 1,500 load jobs per table per day, counting
  retries and failures. 60s would be 1,440/day — the ceiling, with no room to retry.
- **All three feeds share one header timestamp**, 65 of 65, which is why the two vehicle
  feeds merge into one table.
- **The Madrid filter is the `trip_id` join.** Of 90 feed trip_ids that did not join,
  none touched a Madrid station. Never a lat/lon box: `C1` exists in eleven networks.
- **The trip lookup must be refreshed** (900s). `trip_id` is unique to one service date,
  so a recorder that loaded it once would silently match nothing after midnight.

## Things the feed does that will surprise you

- `trip_updates` carries the **next stop only**, never the rest of the journey. Actual
  arrivals are reconstructed across publications, and only 58% of scheduled intermediate
  calls ever produce a `STOPPED_AT`.
- `vehicle.id` is the **train number**, not a physical unit. No rolling-stock analysis.
- `vehicle.label` carries the **platform** — data in no static source we hold.
- Positions are **not GPS**: 622 distinct coordinates across 3,824 observations, p90 of
  1.9 km from the station the same message names.
- Alerts publish **no end time**. An alert ends by vanishing, which is what the `ended`
  version rows record.
- Alert `translation` arrays **reorder between publications** — 13 of 71 alerts flipped
  back and forth. Hashing the payload as delivered would write a new version every 20s.
- An empty feed at 02:00 is **the network asleep**, not an outage. The header timestamp
  is what tells them apart, and it is recorded in `ops.load_runs.source_timestamp`.

## Anomalies are recorded, never dropped

The static pipeline refuses a bad row outright. This one keeps it and writes the oddity
to `ops.rejected_rows`. The static feed can be downloaded again; this one cannot, so
losing an observation over an unfamiliar enum would destroy a fact permanently.

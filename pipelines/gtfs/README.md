# GTFS ingestion — Madrid Cercanías

Loads the Renfe Cercanías timetable and CRTM network attributes into BigQuery.

```
download ──► GCS archive ──► DuckDB transform ──► Parquet ──► BigQuery
```

## Why DuckDB in the middle

BigQuery bills per byte scanned by a **query**, but **load jobs are free**. Doing every
transform locally in DuckDB and loading finished Parquet means an ingestion run costs
essentially nothing. The only DML is the small `DELETE`/`INSERT` that refreshes the
dates in the fact tables.

## Running it

```bash
uv run pulso-gtfs --dry-run     # build Parquet, load nothing. Prints row counts
uv run pulso-gtfs               # full run; skips if the feed hash is unchanged
uv run pulso-gtfs --force       # load even when unchanged
uv run pulso-gtfs --workdir DIR # keep the intermediate files for inspection
```

A run takes about two minutes, most of it downloading the 16.5 MB Renfe ZIP.

## What it writes

| Dataset | Tables | Write mode |
|---|---|---|
| `raw` | 10, mirroring the source files | truncate and replace |
| `dimensions` | stations, lines, routes, stop patterns, station join | truncate and replace |
| `facts` | scheduled trips, scheduled stops | replace only the dates this feed covers |
| `ops` | load runs, rejected rows | append |

`facts` is not truncated because the feed is a rolling ~30-day window: replacing the
whole table would discard every date already held.

## Idempotency

Each run hashes the downloaded ZIP and compares it with the last successful run in
`ops.load_runs`. An unchanged hash exits in seconds without touching BigQuery.

## Rejected rows

Rows that fail validation go to `ops.rejected_rows` with a reason, never dropped.
Current rules: a trip with no `stop_times`, a `service_id` absent from `calendar.txt`,
a `stop_id` absent from `stops.txt`, and an unparseable time.

The first of those is not hypothetical — 69 Madrid trips across 3 routes have no
`stop_times` at all. Without the rule they would vanish at the join that attaches a
stopping pattern, and the row counts would still look reasonable.

## Tests

```bash
uv run pytest -q
```

They cover the decisions that would be silently wrong rather than loudly broken:
times past `24:00:00`, DST offsets in August versus December, pattern-id stability,
and that the Madrid filter uses the `10T` prefix rather than the line name (`C1`
exists in eleven Spanish networks).

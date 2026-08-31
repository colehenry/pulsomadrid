#!/usr/bin/env bash
# Post-load audit. Row counts come from BigQuery table metadata (free) and every
# analytical check runs in DuckDB over the Parquet we loaded (also free), so this
# costs nothing to run.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
PARQUET="${1:?usage: scripts/audit-gtfs.sh <parquet-dir>}"

section "Schema integrity — do the live tables still match the DDL?"
# A Parquet load can silently replace a table definition, discarding every column
# description and turning REQUIRED columns NULLABLE. This catches that.
uv --directory pipelines/gtfs run python - "$REPO_ROOT/pipelines/gtfs/ddls.sql" <<'SCHEMA'
import re, subprocess, json, sys
ddl = open(sys.argv[1]).read()
bad = 0
n = 0
for m in re.finditer(r'CREATE TABLE IF NOT EXISTS `pulso-madrid\.([^`]+)` \((.*?)\n\)', ddl, re.S):
    name, body = m.group(1), m.group(2)
    cols = [l.strip() for l in body.split("\n")
            if re.match(r'^\s+\w+\s+', l) and "PRIMARY KEY" not in l
            and not l.strip().startswith(("ids ", "names ", ">"))]
    ndesc = sum(1 for c in cols if "description=" in c)
    n += 1
    out = subprocess.run(["bq","show","--format=json",f"pulso-madrid:{name}"],
                         capture_output=True, text=True).stdout
    if not out.strip():
        print(f"  FAIL {name} is missing"); bad += 1; continue
    f = json.loads(out)["schema"]["fields"]
    got = sum(1 for x in f if x.get("description"))
    if got < ndesc - 2:
        print(f"  FAIL {name}: {got} descriptions, DDL declares {ndesc} — reload replaced the schema")
        bad += 1
print(f"  OK   all {n} table schemas match the DDL" if not bad else f"  {bad} of {n} table(s) drifted")
sys.exit(1 if bad else 0)
SCHEMA
[ $? -ne 0 ] && exit 1

section "BigQuery row counts (from table metadata — no query cost)"
for spec in \
  "raw:renfe_gtfs_routes" "raw:renfe_gtfs_trips" "raw:renfe_gtfs_stop_times" \
  "raw:renfe_gtfs_stops" "raw:renfe_gtfs_calendar" "raw:crtm_gtfs_stops" \
  "dimensions:cercanias_stations" "dimensions:cercanias_lines" \
  "dimensions:cercanias_stop_patterns" "dimensions:cercanias_line_shapes" \
  "facts:cercanias_scheduled_trips" "facts:cercanias_scheduled_stops" \
  "ops:load_runs" "ops:rejected_rows"; do
  d="${spec%%:*}"; t="${spec##*:}"
  n=$(bq show --format=json "${GCP_PROJECT_ID}:${d}.${t}" 2>/dev/null \
      | python3 -c "import sys,json;print(json.load(sys.stdin).get('numRows','?'))")
  printf '  %-42s %10s\n' "$d.$t" "$n"
done

section "Integrity and content checks (DuckDB over the loaded Parquet)"
uv --directory pipelines/gtfs run python - "$PARQUET" <<'PY'
import sys, duckdb
from pathlib import Path
p = Path(sys.argv[1]); con = duckdb.connect(); con.execute("SET TimeZone='UTC'")
for f in p.glob("*.parquet"):
    con.execute(f"CREATE VIEW {f.stem} AS SELECT * FROM read_parquet('{f}')")

checks = [
 ("trips with a pattern that does not exist",
  "SELECT COUNT(*) FROM cercanias_scheduled_trips t LEFT JOIN cercanias_stop_patterns p "
  "USING(stop_pattern_id) WHERE p.stop_pattern_id IS NULL", 0),
 ("stops whose trip does not exist",
  "SELECT COUNT(*) FROM cercanias_scheduled_stops s LEFT JOIN cercanias_scheduled_trips t "
  "USING(trip_id) WHERE t.trip_id IS NULL", 0),
 ("stops at a station that does not exist",
  "SELECT COUNT(*) FROM cercanias_scheduled_stops s LEFT JOIN cercanias_stations st "
  "ON st.station_id = s.station_id WHERE st.station_id IS NULL", 0),
 ("duplicate trip_id",
  "SELECT COUNT(*) FROM (SELECT trip_id FROM cercanias_scheduled_trips GROUP BY 1 HAVING COUNT(*)>1)", 0),
 ("duplicate (trip_id, stop_number)",
  "SELECT COUNT(*) FROM (SELECT trip_id, stop_number FROM cercanias_scheduled_stops "
  "GROUP BY 1,2 HAVING COUNT(*)>1)", 0),
 ("trips whose stop_number does not start at 1",
  "SELECT COUNT(*) FROM (SELECT trip_id FROM cercanias_scheduled_stops GROUP BY 1 "
  "HAVING MIN(stop_number)<>1)", 0),
 ("null scheduled_arrival on a stop",
  "SELECT COUNT(*) FROM cercanias_scheduled_stops WHERE scheduled_arrival IS NULL", 0),
 ("day_offset outside 0..1",
  "SELECT COUNT(*) FROM cercanias_scheduled_stops WHERE day_offset NOT IN (0,1)", 0),
 ("arrival after departure within a stop",
  "SELECT COUNT(*) FROM cercanias_scheduled_stops WHERE scheduled_departure < scheduled_arrival", 0),
 ("times going backwards within a trip",
  "SELECT COUNT(*) FROM (SELECT trip_id FROM (SELECT trip_id, scheduled_arrival, "
  "LAG(scheduled_arrival) OVER (PARTITION BY trip_id ORDER BY stop_number) prev "
  "FROM cercanias_scheduled_stops) WHERE prev IS NOT NULL AND scheduled_arrival < prev GROUP BY 1)", 0),
 ("patterns belonging to more than one line",
  "SELECT COUNT(*) FROM (SELECT stop_pattern_id FROM cercanias_scheduled_trips "
  "GROUP BY 1 HAVING COUNT(DISTINCT line_id)>1)", 0),
 ("line shapes with fewer than 2 points",
  "SELECT COUNT(*) FROM cercanias_line_shapes WHERE n_points < 2", 0),
 ("line shapes whose line does not exist",
  "SELECT COUNT(*) FROM cercanias_line_shapes s LEFT JOIN cercanias_lines l USING(line_id) "
  "WHERE l.line_id IS NULL", 0),
 ("stations with a CRTM match further than 1km away",
  "SELECT COUNT(*) FROM cercanias_stations WHERE crtm_match_distance_m > 1000", 0),
]
bad = 0
for label, sql, want in checks:
    got = con.execute(sql).fetchone()[0]
    ok = got == want
    bad += 0 if ok else 1
    print(f"  {'OK ' if ok else 'FAIL'}  {label:52s} {got}")

print("\n  Known facts, recomputed from what was loaded:")
for label, sql in [
 ("distinct stop patterns", "SELECT COUNT(*) FROM cercanias_stop_patterns"),
 ("lines", "SELECT COUNT(*) FROM cercanias_lines"),
 ("C5 trips reaching Humanes 35012 (%)",
  "SELECT ROUND(100.0*COUNTIF(list_contains(p.stations.ids,'35012'))/COUNT(*),1) "
  "FROM cercanias_scheduled_trips t JOIN cercanias_stop_patterns p USING(stop_pattern_id) "
  "WHERE t.line_id='C5'"),
 ("trips that skip >=1 station (%)",
  "SELECT ROUND(100.0*COUNTIF(len(p.skipped.ids)>0)/COUNT(*),1) "
  "FROM cercanias_scheduled_trips t JOIN cercanias_stop_patterns p USING(stop_pattern_id)"),
 ("service dates covered", "SELECT COUNT(DISTINCT service_date) FROM cercanias_scheduled_trips"),
 ("trips crossing midnight", "SELECT COUNTIF(crosses_midnight) FROM cercanias_scheduled_trips"),
 ("stations without a CRTM fare zone",
  "SELECT COUNTIF(crtm_zone_id IS NULL) FROM cercanias_stations"),
]:
    print(f"  {label:52s} {con.execute(sql).fetchone()[0]}")
sys.exit(1 if bad else 0)
PY
rc=$?
[ $rc -eq 0 ] && printf '\n%saudit passed%s\n' "$G" "$X" || printf '\n%saudit found problems%s\n' "$R" "$X"
exit $rc

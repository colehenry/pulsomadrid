"""Everything between the downloaded files and the Parquet we load into BigQuery.

All of it runs in DuckDB on the local machine: BigQuery charges per byte scanned by a
query, but load jobs are free, so doing the work here and loading finished Parquet
costs nothing.
"""
from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from .config import MADRID_NUCLEO, STATION_DISPLAY_NAMES, TIMEZONE

log = logging.getLogger(__name__)

# Files we take from each source. CRTM's trips/stop_times/shapes are header-only.
RENFE_FILES = ["agency", "routes", "trips", "stops", "stop_times", "calendar", "shapes", "transfers"]
CRTM_FILES = ["stops", "routes"]

# Seconds since midnight of the service day. Hours run past 24 in GTFS.
_SECS = """
    CAST(split_part({c}, ':', 1) AS BIGINT) * 3600
  + CAST(split_part({c}, ':', 2) AS BIGINT) * 60
  + CAST(split_part({c}, ':', 3) AS BIGINT)
"""


def _ts(secs_expr: str) -> str:
    """Local wall-clock on the service day -> absolute instant."""
    return f"timezone('{TIMEZONE}', service_date::TIMESTAMP + INTERVAL ({secs_expr}) SECOND)"


def connect(renfe_dir: Path, crtm_dir: Path) -> duckdb.DuckDBPyConnection:
    """Open a connection with every source file exposed as an all-VARCHAR view."""
    con = duckdb.connect()
    con.execute("INSTALL icu; LOAD icu;")
    # Render timestamps in UTC regardless of the machine's locale. The stored
    # instant is the same either way, but this keeps output reproducible.
    con.execute("SET TimeZone = 'UTC'")
    for name in RENFE_FILES:
        con.execute(
            f"CREATE VIEW src_renfe_{name} AS "
            f"SELECT * FROM read_csv('{renfe_dir / f'{name}.txt'}', header=true, all_varchar=true)"
        )
    for name in CRTM_FILES:
        con.execute(
            f"CREATE VIEW src_crtm_{name} AS "
            f"SELECT * FROM read_csv('{crtm_dir / f'{name}.txt'}', header=true, all_varchar=true)"
        )
    con.execute("CREATE TABLE w_display_override (station_id VARCHAR, display_name VARCHAR)")
    for station_id, display_name in STATION_DISPLAY_NAMES.items():
        con.execute("INSERT INTO w_display_override VALUES (?, ?)", [station_id, display_name])
    return con


def build_raw(con: duckdb.DuckDBPyConnection) -> None:
    """Madrid rows only, values exactly as published. No trimming, no casting."""
    con.execute(f"""
        CREATE TABLE madrid_route_ids AS
        SELECT route_id FROM src_renfe_routes WHERE trim(route_id) LIKE '{MADRID_NUCLEO}%'
    """)
    con.execute("""
        CREATE TABLE madrid_trip_ids AS
        SELECT t.trip_id FROM src_renfe_trips t
        JOIN madrid_route_ids r ON trim(t.route_id) = trim(r.route_id)
    """)

    con.execute("CREATE TABLE raw_renfe_gtfs_agency AS SELECT * FROM src_renfe_agency")
    con.execute("""
        CREATE TABLE raw_renfe_gtfs_routes AS SELECT s.* FROM src_renfe_routes s
        JOIN madrid_route_ids m ON trim(s.route_id) = trim(m.route_id)
    """)
    con.execute("""
        CREATE TABLE raw_renfe_gtfs_trips AS SELECT s.* FROM src_renfe_trips s
        JOIN madrid_route_ids m ON trim(s.route_id) = trim(m.route_id)
    """)
    con.execute("""
        CREATE TABLE raw_renfe_gtfs_stop_times AS SELECT s.* FROM src_renfe_stop_times s
        JOIN madrid_trip_ids m ON trim(s.trip_id) = trim(m.trip_id)
    """)
    # Stations and calendar entries reachable from Madrid trips.
    con.execute("""
        CREATE TABLE raw_renfe_gtfs_stops AS SELECT DISTINCT s.* FROM src_renfe_stops s
        WHERE trim(s.stop_id) IN (
            SELECT DISTINCT trim(st.stop_id) FROM src_renfe_stop_times st
            JOIN madrid_trip_ids m ON trim(st.trip_id) = trim(m.trip_id))
    """)
    con.execute("""
        CREATE TABLE raw_renfe_gtfs_calendar AS SELECT DISTINCT c.* FROM src_renfe_calendar c
        WHERE trim(c.service_id) IN (
            SELECT DISTINCT trim(t.service_id) FROM src_renfe_trips t
            JOIN madrid_route_ids m ON trim(t.route_id) = trim(m.route_id))
    """)
    con.execute("""
        CREATE TABLE raw_renfe_gtfs_shapes AS SELECT s.* FROM src_renfe_shapes s
        WHERE trim(s.shape_id) IN (
            SELECT DISTINCT trim(t.shape_id) FROM src_renfe_trips t
            JOIN madrid_route_ids m ON trim(t.route_id) = trim(m.route_id))
    """)
    con.execute("""
        CREATE TABLE raw_renfe_gtfs_transfers AS SELECT s.* FROM src_renfe_transfers s
        WHERE trim(s.from_stop_id) IN (SELECT trim(stop_id) FROM raw_renfe_gtfs_stops)
           OR trim(s.to_stop_id)   IN (SELECT trim(stop_id) FROM raw_renfe_gtfs_stops)
    """)
    con.execute("CREATE TABLE raw_crtm_gtfs_stops  AS SELECT * FROM src_crtm_stops")
    con.execute("CREATE TABLE raw_crtm_gtfs_routes AS SELECT * FROM src_crtm_routes")


def build_trimmed(con: duckdb.DuckDBPyConnection) -> None:
    """Trimmed, typed working tables. Everything below is built from these."""
    con.execute("""
        CREATE TABLE w_routes AS
        SELECT trim(route_id) AS route_id, trim(route_short_name) AS line_id,
               trim(regexp_replace(regexp_replace(route_long_name,
                     '\\s{2,}-', ' - '), '\\s+', ' ', 'g')) AS long_name,
               trim(route_color) AS color_hex,
               CAST(regexp_extract(trim(route_id), '10T0*(\\d+)', 1) AS BIGINT) AS route_seq
        FROM raw_renfe_gtfs_routes
    """)
    con.execute("""
        CREATE TABLE w_trips AS
        SELECT trim(t.trip_id) AS trip_id, trim(t.service_id) AS service_id,
               trim(t.route_id) AS route_id, r.line_id,
               regexp_extract(trim(t.trip_id), '^([A-Z0-9]+?)(\\d{5})([A-Za-z0-9]+)$', 2) AS train_number
        FROM raw_renfe_gtfs_trips t JOIN w_routes r ON r.route_id = trim(t.route_id)
    """)
    con.execute("""
        CREATE TABLE w_calendar AS
        SELECT trim(service_id) AS service_id,
               strptime(trim(start_date), '%Y%m%d')::DATE AS service_date
        FROM raw_renfe_gtfs_calendar
    """)
    con.execute("""
        CREATE TABLE w_stops AS
        SELECT trim(stop_id) AS station_id, trim(stop_name) AS station_name,
               trim(regexp_replace(regexp_replace(trim(stop_name),
                     '^Madrid-', ''), ' Cercan[ií]as$', '')) AS display_name_auto,
               TRY_CAST(trim(stop_lat) AS DOUBLE) AS lat,
               TRY_CAST(trim(stop_lon) AS DOUBLE) AS lon
        FROM raw_renfe_gtfs_stops
    """)
    con.execute(f"""
        CREATE TABLE w_stop_times AS
        SELECT trim(st.trip_id) AS trip_id,
               trim(st.stop_id) AS station_id,
               CAST(trim(st.stop_sequence) AS BIGINT) AS source_stop_sequence,
               trim(st.arrival_time)   AS source_arrival_time,
               trim(st.departure_time) AS source_departure_time,
               {_SECS.format(c="trim(st.arrival_time)")}   AS arr_secs,
               {_SECS.format(c="trim(st.departure_time)")} AS dep_secs,
               ROW_NUMBER() OVER (PARTITION BY trim(st.trip_id)
                                  ORDER BY CAST(trim(st.stop_sequence) AS BIGINT)) AS stop_number
        FROM raw_renfe_gtfs_stop_times st
    """)


def build_patterns(con: duckdb.DuckDBPyConnection) -> None:
    """A stop pattern is the ordered list of stations a trip calls at."""
    con.execute("""
        CREATE TABLE w_trip_pattern AS
        SELECT st.trip_id,
               substr(sha256(ANY_VALUE(t.line_id) || ':' ||
                             string_agg(st.station_id, '>' ORDER BY st.stop_number)), 1, 12)
                   AS stop_pattern_id,
               string_agg(st.station_id, '>' ORDER BY st.stop_number) AS pattern_key,
               COUNT(*) AS n_stops
        FROM w_stop_times st
        JOIN w_trips t ON t.trip_id = st.trip_id
        GROUP BY st.trip_id
    """)
    # One row per pattern, with the line and direction of the trips that use it.
    con.execute("""
        CREATE TABLE w_pattern_base AS
        SELECT tp.stop_pattern_id,
               ANY_VALUE(t.line_id) AS line_id,
               ANY_VALUE(tp.pattern_key) AS pattern_key,
               ANY_VALUE(tp.n_stops) AS n_stops,
               COUNT(*) AS trip_count
        FROM w_trip_pattern tp
        JOIN w_trips t ON t.trip_id = tp.trip_id
        GROUP BY tp.stop_pattern_id
    """)
    con.execute("""
        CREATE TABLE w_pattern_all_station AS
        SELECT b.stop_pattern_id, b.line_id, u.station_id, u.ord
        FROM w_pattern_base b,
             UNNEST(str_split(b.pattern_key, '>')) WITH ORDINALITY AS u(station_id, ord)
    """)
    # One reference ordering per line: the pattern with the most stops.
    con.execute("""
        CREATE TABLE w_line_reference AS
        SELECT line_id, stop_pattern_id AS ref_pattern_id FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY line_id
                     ORDER BY n_stops DESC, trip_count DESC, stop_pattern_id) AS rn
          FROM w_pattern_base) WHERE rn = 1
    """)
    con.execute("""
        CREATE TABLE w_pattern AS
        WITH ref AS (
          SELECT lr.line_id, ps.station_id, ps.ord AS ref_ord
          FROM w_line_reference lr
          JOIN w_pattern_all_station ps ON ps.stop_pattern_id = lr.ref_pattern_id),
        oriented AS (
          SELECT b.stop_pattern_id,
                 MIN_BY(r.ref_ord, ps.ord) AS ref_at_start,
                 MAX_BY(r.ref_ord, ps.ord) AS ref_at_end
          FROM w_pattern_base b
          JOIN w_pattern_all_station ps ON ps.stop_pattern_id = b.stop_pattern_id
          JOIN ref r ON r.line_id = b.line_id AND r.station_id = ps.station_id
          GROUP BY b.stop_pattern_id)
        SELECT b.stop_pattern_id, b.line_id, b.pattern_key, b.n_stops, b.trip_count,
               CASE WHEN o.ref_at_start <= o.ref_at_end THEN 1 ELSE 2 END AS direction_int
        FROM w_pattern_base b LEFT JOIN oriented o USING (stop_pattern_id)
    """)
    con.execute("""
        CREATE TABLE w_pattern_station AS
        SELECT p.stop_pattern_id, u.station_id, u.ord
        FROM w_pattern p,
             UNNEST(str_split(p.pattern_key, '>')) WITH ORDINALITY AS u(station_id, ord)
    """)
    # The longest pattern on each line+direction is the baseline for "skipped".
    con.execute("""
        CREATE TABLE w_pattern_full AS
        SELECT line_id, direction_int, stop_pattern_id AS full_pattern_id
        FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY line_id, direction_int
                                           ORDER BY n_stops DESC, trip_count DESC) AS rn
              FROM w_pattern) WHERE rn = 1
    """)
    # The terminus each direction heads towards: the last station of that direction's
    # full-length pattern. This is what "C5 towards Humanes" means on a platform sign.
    con.execute("""
        CREATE TABLE w_direction_terminus AS
        SELECT f.line_id, f.direction_int,
               (SELECT ps.station_id FROM w_pattern_station ps
                 WHERE ps.stop_pattern_id = f.full_pattern_id
                 ORDER BY ps.ord DESC LIMIT 1) AS towards_station_id
        FROM w_pattern_full f
    """)

    # Skipped = in the full pattern, positioned between this pattern's own first and
    # last station, but not called at.
    con.execute("""
        CREATE TABLE w_pattern_baseline AS
        SELECT p.stop_pattern_id, f.full_pattern_id AS baseline_pattern_id
        FROM w_pattern p
        JOIN w_pattern_full f ON f.line_id = p.line_id AND f.direction_int = p.direction_int
        WHERE NOT EXISTS (
          SELECT 1 FROM w_pattern_station ps
          WHERE ps.stop_pattern_id = p.stop_pattern_id
            AND NOT EXISTS (SELECT 1 FROM w_pattern_station fs
                            WHERE fs.stop_pattern_id = f.full_pattern_id
                              AND fs.station_id = ps.station_id))
    """)
    con.execute("""
        CREATE TABLE w_pattern_skipped AS
        WITH span AS (
          SELECT p.stop_pattern_id, f.full_pattern_id,
                 MIN(fs.ord) AS lo, MAX(fs.ord) AS hi
          FROM w_pattern p
          JOIN w_pattern_baseline b ON b.stop_pattern_id = p.stop_pattern_id
          JOIN w_pattern_full f ON f.full_pattern_id = b.baseline_pattern_id
          JOIN w_pattern_station ps ON ps.stop_pattern_id = p.stop_pattern_id
          JOIN w_pattern_station fs ON fs.stop_pattern_id = f.full_pattern_id
                                   AND fs.station_id = ps.station_id
          GROUP BY 1, 2)
        SELECT s.stop_pattern_id, fs.station_id, fs.ord
        FROM span s
        JOIN w_pattern_station fs ON fs.stop_pattern_id = s.full_pattern_id
                                 AND fs.ord BETWEEN s.lo AND s.hi
        LEFT JOIN w_pattern_station own ON own.stop_pattern_id = s.stop_pattern_id
                                       AND own.station_id = fs.station_id
        WHERE own.station_id IS NULL
    """)


def build_station_join(con: duckdb.DuckDBPyConnection) -> None:
    """Match Renfe stations to CRTM stations: normalised name first, then distance.

    Name matching alone reaches about two thirds; the rest disagree on articles and
    hyphens ('El Escorial', 'Getafe-Centro'), so coordinates finish the job.
    """
    norm = ("upper(strip_accents(regexp_replace(regexp_replace(regexp_replace({c},"
            "'^Madrid-',''),' Cercan.*$',''),'[^A-Za-z0-9]','','g')))")
    con.execute(f"""
        CREATE TABLE w_crtm AS
        SELECT trim(stop_id) AS crtm_stop_id, trim(parent_station) AS crtm_station_id,
               trim(stop_name) AS nm, trim(zone_id) AS crtm_zone_id,
               TRY_CAST(trim(stop_lat) AS DOUBLE) AS lat,
               TRY_CAST(trim(stop_lon) AS DOUBLE) AS lon,
               {norm.format(c="trim(stop_name)")} AS k
        FROM raw_crtm_gtfs_stops
    """)
    con.execute(f"""
        CREATE TABLE w_renfe_named AS
        SELECT station_id, station_name, lat, lon,
               {norm.format(c="station_name")} AS k
        FROM w_stops
    """)
    # 6371000 m earth radius; plain haversine avoids needing the spatial extension.
    con.execute("""
        CREATE TABLE w_station_join AS
        WITH by_name AS (
          SELECT r.station_id AS renfe_stop_id, c.crtm_stop_id, c.crtm_station_id,
                 'normalised_name' AS match_method,
                 2*6371000*asin(sqrt(pow(sin(radians(c.lat-r.lat)/2),2)
                   + cos(radians(r.lat))*cos(radians(c.lat))*pow(sin(radians(c.lon-r.lon)/2),2)))
                   AS match_distance_m
          FROM w_renfe_named r JOIN w_crtm c ON c.k = r.k),
        remaining AS (
          SELECT * FROM w_renfe_named r
          WHERE r.station_id NOT IN (SELECT renfe_stop_id FROM by_name)),
        by_distance AS (
          SELECT renfe_stop_id, crtm_stop_id, crtm_station_id, match_method, match_distance_m
          FROM (
            SELECT r.station_id AS renfe_stop_id, c.crtm_stop_id, c.crtm_station_id,
                   'coordinate' AS match_method,
                   2*6371000*asin(sqrt(pow(sin(radians(c.lat-r.lat)/2),2)
                     + cos(radians(r.lat))*cos(radians(c.lat))*pow(sin(radians(c.lon-r.lon)/2),2)))
                     AS match_distance_m,
                   ROW_NUMBER() OVER (PARTITION BY r.station_id ORDER BY
                     2*6371000*asin(sqrt(pow(sin(radians(c.lat-r.lat)/2),2)
                       + cos(radians(r.lat))*cos(radians(c.lat))*pow(sin(radians(c.lon-r.lon)/2),2)))) AS rn
            FROM remaining r CROSS JOIN w_crtm c
            WHERE r.lat IS NOT NULL AND c.lat IS NOT NULL)
          WHERE rn = 1 AND match_distance_m <= 1000),
        all_matches AS (
          SELECT * FROM by_name UNION ALL SELECT * FROM by_distance)
        SELECT renfe_stop_id, crtm_stop_id, crtm_station_id, match_method, match_distance_m
        FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY renfe_stop_id
                ORDER BY match_distance_m NULLS LAST) AS rn FROM all_matches)
        WHERE rn = 1
    """)


def build_outputs(con: duckdb.DuckDBPyConnection, load_id: str, sha: str) -> None:
    """Final tables, column-for-column as BigQuery expects them."""
    now = "now()::TIMESTAMP"
    meta = f"'{load_id}' AS load_id, '{sha}' AS source_file_hash"

    for name in RENFE_FILES:
        con.execute(f"""
            CREATE TABLE out_raw_renfe_gtfs_{name} AS
            SELECT *, {meta}, '{name}.txt' AS source_file, {now} AS load_time
            FROM raw_renfe_gtfs_{name}
        """)
    for name in CRTM_FILES:
        con.execute(f"""
            CREATE TABLE out_raw_crtm_gtfs_{name} AS
            SELECT *, {meta}, '{name}.txt' AS source_file, {now} AS load_time
            FROM raw_crtm_gtfs_{name}
        """)

    con.execute(f"""
        CREATE TABLE out_cercanias_stations AS
        SELECT s.station_id, s.station_name,
               COALESCE(o.display_name, s.display_name_auto) AS display_name,
               CASE WHEN s.lon IS NOT NULL AND s.lat IS NOT NULL
                    THEN 'POINT(' || s.lon || ' ' || s.lat || ')' END AS location,
               s.lat, s.lon,
               j.crtm_stop_id, c.crtm_zone_id, j.crtm_station_id, j.match_distance_m AS crtm_match_distance_m,
               (SELECT list_sort(list_distinct(list(t.line_id)))
                  FROM w_stop_times st JOIN w_trips t ON t.trip_id = st.trip_id
                 WHERE st.station_id = s.station_id) AS line_ids,
               '{load_id}' AS load_id, {now} AS load_time
        FROM w_stops s
        LEFT JOIN w_display_override o ON o.station_id = s.station_id
        LEFT JOIN w_station_join j ON j.renfe_stop_id = s.station_id
        LEFT JOIN w_crtm c ON c.crtm_stop_id = j.crtm_stop_id
    """)
    # Routes with no trips are stale definitions in the feed: 69 of 118 Madrid routes
    # have none. They are excluded so line counts stay honest. There is no routes output
    # table -- route_id is a degenerate dimension, carried on the fact and nowhere else.
    con.execute("""
        CREATE TABLE w_live_routes AS
        SELECT r.* FROM w_routes r
        WHERE EXISTS (SELECT 1 FROM w_trips t WHERE t.route_id = r.route_id)
    """)
    con.execute(f"""
        CREATE TABLE out_cercanias_lines AS
        SELECT r.line_id,
               ANY_VALUE(r.long_name) AS line_name,
               COALESCE(ANY_VALUE(cr.color_hex), ANY_VALUE(r.color_hex)) AS color_hex,
               (SELECT COUNT(*) FROM w_pattern p WHERE p.line_id = r.line_id) AS n_patterns,
               '{load_id}' AS load_id, {now} AS load_time
        FROM w_live_routes r
        LEFT JOIN (SELECT trim(route_short_name) AS line_id, trim(route_color) AS color_hex
                     FROM raw_crtm_gtfs_routes) cr ON cr.line_id = r.line_id
        GROUP BY r.line_id
    """)
    # Route geometry for drawing the network. shape_id is '10_<line>' and
    # '10_<line>_INV', so it encodes line and direction but NOT stopping pattern —
    # 23 shapes for 119 patterns. It draws the track, not what any train does.
    #
    # Emitted as WKT text; BigQuery parses it into GEOGRAPHY on load, the same way
    # cercanias_stations.location works.
    con.execute(f"""
        CREATE TABLE out_cercanias_line_shapes AS
        WITH pts AS (
          SELECT trim(shape_id) AS shape_id,
                 CAST(trim(shape_pt_sequence) AS BIGINT) AS seq,
                 TRY_CAST(trim(shape_pt_lon) AS DOUBLE) AS lon,
                 TRY_CAST(trim(shape_pt_lat) AS DOUBLE) AS lat
          FROM raw_renfe_gtfs_shapes
          WHERE TRY_CAST(trim(shape_pt_lat) AS DOUBLE) IS NOT NULL
            AND TRY_CAST(trim(shape_pt_lon) AS DOUBLE) IS NOT NULL)
        SELECT p.shape_id,
               regexp_extract(p.shape_id, '^10_([A-Za-z0-9]+?)(_INV)?$', 1) AS line_id,
               'LINESTRING(' || string_agg(p.lon || ' ' || p.lat, ', ' ORDER BY p.seq) || ')'
                   AS geometry,
               COUNT(*) AS n_points,
               '{load_id}' AS load_id, {now} AS load_time
        FROM pts p GROUP BY p.shape_id
        HAVING COUNT(*) >= 2
    """)
    con.execute(f"""
        CREATE TABLE out_cercanias_stop_patterns AS
        WITH stations AS (
          SELECT ps.stop_pattern_id,
                 list(ps.station_id ORDER BY ps.ord) AS ids,
                 list(s.station_name ORDER BY ps.ord) AS names,
                 min_by(ps.station_id, ps.ord) AS origin_station_id,
                 max_by(ps.station_id, ps.ord) AS destination_station_id
          FROM w_pattern_station ps LEFT JOIN w_stops s ON s.station_id = ps.station_id
          GROUP BY 1),
        skipped AS (
          SELECT sk.stop_pattern_id,
                 list(sk.station_id ORDER BY sk.ord) AS ids,
                 list(s.station_name ORDER BY sk.ord) AS names
          FROM w_pattern_skipped sk LEFT JOIN w_stops s ON s.station_id = sk.station_id
          GROUP BY 1)
        SELECT p.stop_pattern_id, p.line_id,
               dt.towards_station_id AS direction_towards_station_id,
               {{'ids': st.ids, 'names': st.names}} AS stations,
               {{'ids': COALESCE(sk.ids, []), 'names': COALESCE(sk.names, [])}} AS skipped,
               p.n_stops, st.origin_station_id, st.destination_station_id,
               (f.full_pattern_id = p.stop_pattern_id) AS is_full_length,
               p.trip_count, b.baseline_pattern_id,
               '{load_id}' AS load_id, {now} AS load_time
        FROM w_pattern p
        JOIN stations st ON st.stop_pattern_id = p.stop_pattern_id
        LEFT JOIN skipped sk ON sk.stop_pattern_id = p.stop_pattern_id
        LEFT JOIN w_pattern_baseline b ON b.stop_pattern_id = p.stop_pattern_id
        LEFT JOIN w_direction_terminus dt ON dt.line_id = p.line_id AND dt.direction_int = p.direction_int
        LEFT JOIN w_pattern_full f ON f.line_id = p.line_id AND f.direction_int = p.direction_int
    """)
    con.execute(f"""
        CREATE TABLE out_cercanias_scheduled_trips AS
        WITH ends AS (
          SELECT st.trip_id, MIN(st.stop_number) AS lo, MAX(st.stop_number) AS hi
          FROM w_stop_times st GROUP BY 1)
        SELECT t.trip_id, t.train_number, t.service_id, c.service_date, t.route_id, t.line_id,
               tp.stop_pattern_id,
               p.origin_station_id, p.destination_station_id,
               {_ts("first_dep.dep_secs")} AS scheduled_departure,
               {_ts("last_arr.arr_secs")}  AS scheduled_arrival,
               (last_arr.arr_secs >= 86400) AS crosses_midnight,
               '{load_id}' AS load_id, {now} AS load_time
        FROM w_trips t
        JOIN w_calendar c   ON c.service_id = t.service_id
        JOIN w_trip_pattern tp ON tp.trip_id = t.trip_id
        JOIN out_cercanias_stop_patterns p ON p.stop_pattern_id = tp.stop_pattern_id
        JOIN ends e ON e.trip_id = t.trip_id
        JOIN w_stop_times first_dep ON first_dep.trip_id = t.trip_id AND first_dep.stop_number = e.lo
        JOIN w_stop_times last_arr  ON last_arr.trip_id  = t.trip_id AND last_arr.stop_number  = e.hi
    """)
    con.execute(f"""
        CREATE TABLE out_cercanias_scheduled_stops AS
        SELECT st.trip_id, c.service_date, st.stop_number, st.station_id,
               {_ts("st.arr_secs")} AS scheduled_arrival,
               {_ts("st.dep_secs")} AS scheduled_departure,
               CAST(st.dep_secs / 86400 AS BIGINT) AS day_offset,
               st.source_arrival_time, st.source_departure_time, st.source_stop_sequence,
               '{load_id}' AS load_id, {now} AS load_time
        FROM w_stop_times st
        JOIN w_trips t    ON t.trip_id = st.trip_id
        JOIN w_calendar c ON c.service_id = t.service_id
    """)


def export(con: duckdb.DuckDBPyConnection, out_dir: Path) -> dict[str, Path]:
    """Write every out_ table to Parquet. BigQuery load jobs are free; queries are not."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'out_%'"
    ).fetchall()]
    paths: dict[str, Path] = {}
    for t in tables:
        p = out_dir / f"{t.removeprefix('out_')}.parquet"
        con.execute(f"COPY {t} TO '{p}' (FORMAT PARQUET)")
        paths[t.removeprefix("out_")] = p
    return paths


def build_rejects(con: duckdb.DuckDBPyConnection, load_id: str) -> None:
    """Rows we refuse to load, with the reason. Nothing is dropped silently.

    Without this, a trip with no stop_times would simply vanish at the join that
    attaches a pattern, and the row counts would still look plausible.
    """
    con.execute(f"""
        CREATE TABLE out_rejected_rows AS
        WITH
        no_stops AS (
          SELECT t.trip_id, 'trips.txt' AS source_file,
                 to_json({{'trip_id': t.trip_id, 'route_id': t.route_id,
                          'service_id': t.service_id}}) AS raw_row,
                 'trip has no rows in stop_times.txt' AS reason
          FROM w_trips t
          WHERE NOT EXISTS (SELECT 1 FROM w_stop_times st WHERE st.trip_id = t.trip_id)),
        no_calendar AS (
          SELECT t.trip_id, 'trips.txt',
                 to_json({{'trip_id': t.trip_id, 'service_id': t.service_id}}),
                 'service_id not present in calendar.txt'
          FROM w_trips t
          WHERE NOT EXISTS (SELECT 1 FROM w_calendar c WHERE c.service_id = t.service_id)),
        unknown_station AS (
          SELECT st.trip_id, 'stop_times.txt',
                 to_json({{'trip_id': st.trip_id, 'stop_id': st.station_id,
                          'stop_sequence': st.source_stop_sequence}}),
                 'stop_id not present in stops.txt'
          FROM w_stop_times st
          WHERE NOT EXISTS (SELECT 1 FROM w_stops s WHERE s.station_id = st.station_id)),
        bad_time AS (
          SELECT st.trip_id, 'stop_times.txt',
                 to_json({{'trip_id': st.trip_id, 'arrival_time': st.source_arrival_time,
                          'departure_time': st.source_departure_time}}),
                 'arrival or departure time could not be parsed'
          FROM w_stop_times st
          WHERE st.arr_secs IS NULL OR st.dep_secs IS NULL)
        SELECT '{load_id}' AS load_id, now()::TIMESTAMP AS rejected_at,
               'renfe_gtfs' AS source, source_file, raw_row::VARCHAR AS raw_row, reason,
               now()::TIMESTAMP AS load_time
        FROM (SELECT * FROM no_stops UNION ALL SELECT * FROM no_calendar
              UNION ALL SELECT * FROM unknown_station UNION ALL SELECT * FROM bad_time)
    """)

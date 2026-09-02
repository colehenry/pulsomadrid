-- ============================================================================
-- Pulso Madrid — Metro (Stage 2)
--
-- Sources : CRTM widget endpoint (live arrivals, undocumented, one call per station)
--           CRTM Metro GTFS               (coordinates, scheduled segment times)
--           CRTM ParadasPorItinerario     (line topology — the only source with L3)
-- Context : docs/data/metro-rt.md. Every number below was measured, not documented.
--
-- Metro is frequency-based: there is no timetable for a train to be late against, so
-- the metric is headway and wait time, not delay. See docs/metrics/reliability.md.
--
-- The idempotency key differs from the Cercanias recorder's, and the difference is the
-- point. Renfe publishes a header timestamp, so "the same publication twice" is free to
-- detect. CRTM returns actualDate — its own clock, different on every request — so a
-- retry would look like a new observation. The key here must be ours: the poll tick.
-- ============================================================================


-- Every station on the network, not only the ones polled.
-- Also the poller's work list: it reads poll_tier from here, so the polling set changes
-- with an UPDATE rather than a deploy.
CREATE TABLE IF NOT EXISTS `pulso-madrid.dimensions.metro_stations` (
  station_id             STRING    NOT NULL OPTIONS(description="CRTM stop code, e.g. '4_57'. Passed as codStop to the live endpoint and carried by every observation. Equals the GTFS stop_id with the 'par_' prefix stripped, and equals '4_' plus CODIGOESTACION from ParadasPorItinerario — verified on four randomly chosen stops, names matching the API exactly"),
  station_name           STRING             OPTIONS(description="Name exactly as CRTM publishes it, e.g. 'NUÑEZ DE BALBOA'. Upper case, and only partially accented: ñ and ü survive but acute accents do not, on 30 of 592 source rows"),
  formatted_station_name STRING    NOT NULL OPTIONS(description="What to show a user, e.g. 'Núñez de Balboa'. Title case with Spanish particles left lower ('de', 'del', 'la', 'los', 'y'), plus accents restored from the ACCENTS table in pipelines/metro/src/pulso_metro/names.py — data rather than logic, because acute accents cannot be derived from an unaccented source by rule. NOT NULL: a station with no readable name is a loader bug, not a valid row"),
  lat                    FLOAT64            OPTIONS(description="Latitude, WGS84, from the CRTM Metro GTFS"),
  lon                    FLOAT64            OPTIONS(description="Longitude, WGS84"),
  fare_zone              STRING             OPTIONS(description="CRTM fare zone, e.g. 'A' or 'B1', from ParadasPorItinerario. The same zoning as dimensions.cercanias_stations.crtm_zone_id"),
  municipality           STRING             OPTIONS(description="Municipality, e.g. 'MADRID' or 'ALCOBENDAS'. Much of the network is outside the city proper, which matters once barrio profiles arrive"),
  line_ids               ARRAY<STRING>      OPTIONS(description="Lines calling here as the live feed names them, e.g. ['4','5','10'] — unbranched, because that is what arrivals carry. Branch detail lives in dimensions.metro_line_stops"),
  source_station_ids     ARRAY<STRING>      OPTIONS(description="Every CRTM code for this physical station, e.g. ['4_12','4_35','4_48'] for Sol. CRTM codes platforms, not stations: 291 codes for 242 stations, 38 of which have more than one. The live endpoint aggregates by station — any code returns every line calling there, verified at Plaza de Castilla where all three codes returned an identical 14 arrivals — so station_id is the lowest of these and the only one polled. Polling the others would count the same arrivals two or three times"),
  source_stop_id         STRING             OPTIONS(description="GTFS stop_id exactly as published, e.g. 'par_4_57', so the station_id derivation stays auditable. NULL for stations absent from the GTFS, which includes every line 3 station"),
  crtm_parent_station    STRING             OPTIONS(description="CRTM parent station id, e.g. 'est_90_54'. Set on 42 of 290 platforms, the multi-modal interchanges. NULL for most stations, which is correct"),
  cercanias_station_id   STRING             OPTIONS(description="Matching station in dimensions.cercanias_stations, joined on crtm_parent_station. Non-NULL for seven stations — Sol, Atocha, Nuevos Ministerios, Principe Pio, Aluche, Mendez Alvaro, Aeropuerto T4 — the only places both networks can be shown together"),
  poll_tier              INT64     NOT NULL OPTIONS(description="1 = polled every 30s, the 49 stations chosen by greedy cover so no point on any line is more than 11 minutes of travel behind one, which is what the position layer needs. 2 = swept every 5 minutes, enough to measure wait times because headway is readable from a single poll. Every station is one or the other: nothing is never polled"),

  active                 BOOL               OPTIONS(description="True if this station is in the most recent source. Kept despite nearly always being true, because Madrid closes stations for works for months at a time and this is how a closure is recorded rather than a row disappearing. Rows are never deleted, matching every other dimension table here"),

  load_id                STRING    NOT NULL OPTIONS(description="Identifier of the run that wrote this row"),
  load_time              TIMESTAMP NOT NULL OPTIONS(description="When this row was written"),

  PRIMARY KEY (station_id) NOT ENFORCED
)
CLUSTER BY station_id;


-- The network's shape: which stations a line calls at, in order. ~592 rows.
--
-- Without this there is no segment time, no journey time and no position estimate —
-- line_ids on the station says a line stops there, not what comes next.
CREATE TABLE IF NOT EXISTS `pulso-madrid.dimensions.metro_line_stops` (
  line_id            STRING    NOT NULL OPTIONS(description="Line as the live feed names it, e.g. '10'. Unbranched, so this joins directly to the line_id on an observation"),
  branch_id          STRING    NOT NULL OPTIONS(description="The itinerary as CRTM names it, e.g. '10b', '7a', '9A', '12-1', 'R'. Branches are real routes with different stop sequences, but the live feed does not name them — a train's branch is identified by its destination instead. This is the only source that distinguishes them: the GTFS collapses L7, L9 and L10 into one route each and omits line 3 entirely"),
  direction          INT64     NOT NULL OPTIONS(description="CRTM SENTIDO, 1 or 2. Matches the direction on an observation directly, and equals the GTFS direction_id plus one — verified across six lines"),
  stop_number        INT64     NOT NULL OPTIONS(description="Position along this branch, numbered from 1. From ParadasPorItinerario NUMEROORDEN"),
  station_id         STRING    NOT NULL OPTIONS(description="Station called at, joins to dimensions.metro_stations"),

  scheduled_seconds_from_previous INT64 OPTIONS(description="Scheduled travel time from the previous stop, from GTFS stop_times, which are arrival-to-arrival and therefore already include dwell — no dwell is modelled separately on any of 2,216 rows. Distribution: min 42s, p25 90s, p50 108s, p75 136s, max 384s. NULL at the first stop of a branch, and NULL for all of line 3, which has no trips in the GTFS. Both are bootstrap values only: we measure our own from observations and this is the baseline to compare against"),

  load_id            STRING    NOT NULL OPTIONS(description="Identifier of the run that wrote this row"),
  load_time          TIMESTAMP NOT NULL OPTIONS(description="When this row was written"),

  PRIMARY KEY (branch_id, direction, stop_number) NOT ENFORCED
)
CLUSTER BY line_id, station_id;


-- One row per station per poll.
--
-- Arrivals stay an array rather than one row per predicted train: ~196k rows/day this
-- way against roughly ten times that flattened, and the array is the response as
-- delivered, so nothing is lost.
CREATE TABLE IF NOT EXISTS `pulso-madrid.facts.metro_observed_arrivals` (
  station_id        STRING    NOT NULL OPTIONS(description="Station polled, joins to dimensions.metro_stations"),
  service_date      DATE      NOT NULL OPTIONS(description="Madrid service date, with 00:00-02:59 counted as the previous date — the same 03:00 cutover used for Cercanias alerts. Metro runs to about 01:30, so a late train belongs to the night it started"),
  observed_at       TIMESTAMP NOT NULL OPTIONS(description="The poll tick this observation belongs to — OUR clock, rounded to the tick boundary, not CRTM's. With station_id this is the idempotency key: a retried request lands on the same row instead of creating a second one"),
  source_timestamp  TIMESTAMP          OPTIONS(description="The payload's actualDate, CRTM's server clock when it answered. Different on every request, which is exactly why it cannot be the key. Kept because the gap between the two measures clock drift"),

  arrivals          ARRAY<STRUCT<
                      line_id                STRING,
                      direction              INT64,
                      destination            STRING,
                      destination_station_id STRING,
                      predicted_arrival      TIMESTAMP
                    >>                 OPTIONS(description="Every upcoming train listed. Capped at 3 per (line, direction, destination) — a count limit, not a time window, so the horizon shrinks to about 11 minutes at peak as headways shorten. destination_station_id joins to dimensions.metro_stations, verified on nine of nine sampled, and destination is what identifies which branch a train is taking. TRAP: predicted_arrival is mostly but not always minute-aligned. Measured over 589 gaps in one network-wide tick, 91.5% were exact multiples of 60s, and the share varies by line — L2, L3, L4, L5, L8, L10 and L11 were 100% aligned while L7 was 74% and L12 80%. So most headways carry about +/-60s of resolution, but genuine second-level gaps do occur (6s, 9s and 25s were all observed on L1). A zero gap is rare, 3 of 589, and means two trains predicted at the same second: treat it as censored at <60s rather than as a literal zero, and never delete it, because bunching is the signal worth having"),
  n_arrivals        INT64              OPTIONS(description="How many entries were returned, kept as a drift check and not derivable once the array is filtered. Zero is normal at a quiet station near closing; the endpoint returns an empty object rather than an empty list in that case"),
  sae_status        ARRAY<STRUCT<
                      line_id STRING,
                      ok      BOOL
                    >>                 OPTIONS(description="linesStatus.SAEStatus per line: whether CRTM's vehicle-location system is reporting. A data-quality signal, not service alerts. True on every line sampled so far, including while one-minute rounding was producing apparent duplicate arrivals, so it does not explain those"),
  poll_tier         INT64              OPTIONS(description="1 for the 30-second set, 2 for the 5-minute sweep. Recorded per row rather than joined from the station, because tiers will change as we learn and a row must say what resolution it was actually captured at"),

  load_id           STRING    NOT NULL OPTIONS(description="Identifier of the batch that wrote this row, joins to ops.load_runs"),
  load_time         TIMESTAMP NOT NULL OPTIONS(description="When this row was written. load_time minus observed_at is the ingestion lag"),

  PRIMARY KEY (station_id, observed_at) NOT ENFORCED
)
PARTITION BY service_date
CLUSTER BY station_id
OPTIONS(require_partition_filter = TRUE);

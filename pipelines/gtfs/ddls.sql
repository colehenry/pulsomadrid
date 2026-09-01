-- ============================================================================
-- Pulso Madrid — static schedule schema (M1)
--
-- Sources : Renfe Cercanías (timetable) + CRTM (network attributes)
-- Scope   : Madrid only — route_id LIKE '10T%'. All 12 lines.
-- Context : docs/data/schema-m1.md, docs/data/gtfs.md
--
-- Naming  : raw       prefixed by source  (renfe_, crtm_); mirrors the source files
--           dimensions  the network: stations, lines, routes, patterns
--           facts       the schedule: trips and stops
--           ops       pipeline run records
--
--           source_*  a value exactly as the publisher wrote it
--           load_time last column on every table
-- ============================================================================


-- ============================================================================
-- raw — one table per source file, Madrid rows only, every column STRING
-- ============================================================================
-- STRING because the files are space-padded and contain times like '24:05:00'.
-- Type inference corrupts both. Trimming and casting happen on the way to clean.
-- Holds only the current load: truncate and replace each run. Full history is
-- the original ZIPs in GCS.

CREATE TABLE IF NOT EXISTS `pulso-madrid.raw.renfe_gtfs_routes` (
  route_id          STRING OPTIONS(description="Route identifier, e.g. '10T0017C5'"),
  route_short_name  STRING OPTIONS(description="Line name, e.g. 'C5'"),
  route_long_name   STRING OPTIONS(description="Origin and destination as text"),
  route_type        STRING OPTIONS(description="GTFS vehicle type. '2' = rail"),
  route_color       STRING OPTIONS(description="Line colour, 6-digit hex, no '#'"),
  route_text_color  STRING OPTIONS(description="Text colour for use on route_color"),

  load_id           STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  source_file_hash  STRING             OPTIONS(description="SHA256 of the downloaded ZIP, used to detect whether the feed changed"),
  source_file       STRING             OPTIONS(description="File this row came from, e.g. 'routes.txt'"),
  load_time         TIMESTAMP NOT NULL OPTIONS(description="When this row was written")
)
CLUSTER BY route_id;


CREATE TABLE IF NOT EXISTS `pulso-madrid.raw.renfe_gtfs_trips` (
  route_id               STRING OPTIONS(description="Route this trip belongs to"),
  service_id             STRING OPTIONS(description="Calendar entry, one per date, e.g. '1035M'"),
  trip_id                STRING OPTIONS(description="Trip identifier, e.g. '1035M19795C5'"),
  trip_headsign          STRING OPTIONS(description="Destination shown on the train front and platform displays. Empty for 98.7% of Madrid trips"),
  wheelchair_accessible  STRING OPTIONS(description="GTFS accessibility code"),
  block_id               STRING OPTIONS(description="GTFS field for linking consecutive trips of one vehicle. 35,389 distinct values across 37,677 trips, so it does not link anything here"),
  shape_id               STRING OPTIONS(description="Route geometry reference. 23 distinct values for all Madrid: one per line per direction"),

  load_id                STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  source_file_hash       STRING             OPTIONS(description="SHA256 of the downloaded ZIP"),
  source_file            STRING             OPTIONS(description="File this row came from"),
  load_time              TIMESTAMP NOT NULL OPTIONS(description="When this row was written")
)
CLUSTER BY trip_id, route_id;


CREATE TABLE IF NOT EXISTS `pulso-madrid.raw.renfe_gtfs_stop_times` (
  trip_id           STRING OPTIONS(description="Trip this call belongs to"),
  arrival_time      STRING OPTIONS(description="HH:MM:SS local. Hours run past 24 for calls after midnight; 4,481 Madrid rows use hour 24"),
  departure_time    STRING OPTIONS(description="HH:MM:SS local. Differs from arrival_time on 9.7% of Madrid rows"),
  stop_id           STRING OPTIONS(description="Station this call is at"),
  stop_sequence     STRING OPTIONS(description="Renfe's position number. Starts at 1 for 35,216 trips and above 1 for 2,392, so it is not consistently the position on the route"),

  load_id           STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  source_file_hash  STRING             OPTIONS(description="SHA256 of the downloaded ZIP"),
  source_file       STRING             OPTIONS(description="File this row came from"),
  load_time         TIMESTAMP NOT NULL OPTIONS(description="When this row was written")
)
CLUSTER BY trip_id, stop_id;


CREATE TABLE IF NOT EXISTS `pulso-madrid.raw.renfe_gtfs_stops` (
  stop_id           STRING OPTIONS(description="Station identifier, e.g. '18000'"),
  stop_code         STRING OPTIONS(description="Public-facing station code"),
  stop_name         STRING OPTIONS(description="Station name, e.g. 'Madrid-Atocha Cercanías'"),
  stop_desc         STRING OPTIONS(description="Free-text description"),
  stop_lat          STRING OPTIONS(description="Latitude, WGS84 decimal degrees"),
  stop_lon          STRING OPTIONS(description="Longitude, WGS84 decimal degrees"),
  zone_id           STRING OPTIONS(description="Fare zone"),
  stop_url          STRING OPTIONS(description="Station web page"),
  location_type     STRING OPTIONS(description="GTFS code: 0 = stop, 1 = station"),
  parent_station    STRING OPTIONS(description="Parent station id where this is a platform"),

  load_id           STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  source_file_hash  STRING             OPTIONS(description="SHA256 of the downloaded ZIP"),
  source_file       STRING             OPTIONS(description="File this row came from"),
  load_time         TIMESTAMP NOT NULL OPTIONS(description="When this row was written")
)
CLUSTER BY stop_id;


CREATE TABLE IF NOT EXISTS `pulso-madrid.raw.renfe_gtfs_calendar` (
  service_id        STRING OPTIONS(description="Calendar entry identifier, e.g. '1035M'"),
  monday            STRING OPTIONS(description="1 if this entry runs on Mondays. Renfe sets one day flag per entry because start_date equals end_date"),
  tuesday           STRING OPTIONS(description="1 if this entry runs on Tuesdays"),
  wednesday         STRING OPTIONS(description="1 if this entry runs on Wednesdays"),
  thursday          STRING OPTIONS(description="1 if this entry runs on Thursdays"),
  friday            STRING OPTIONS(description="1 if this entry runs on Fridays"),
  saturday          STRING OPTIONS(description="1 if this entry runs on Saturdays"),
  sunday            STRING OPTIONS(description="1 if this entry runs on Sundays"),
  start_date        STRING OPTIONS(description="First date, YYYYMMDD. Equals end_date in this feed"),
  end_date          STRING OPTIONS(description="Last date, YYYYMMDD. Equals start_date in this feed"),

  load_id           STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  source_file_hash  STRING             OPTIONS(description="SHA256 of the downloaded ZIP"),
  source_file       STRING             OPTIONS(description="File this row came from"),
  load_time         TIMESTAMP NOT NULL OPTIONS(description="When this row was written")
)
CLUSTER BY service_id;


CREATE TABLE IF NOT EXISTS `pulso-madrid.raw.renfe_gtfs_shapes` (
  shape_id             STRING OPTIONS(description="Shape identifier, e.g. '10_C5'"),
  shape_pt_lat         STRING OPTIONS(description="Latitude of this point, WGS84"),
  shape_pt_lon         STRING OPTIONS(description="Longitude of this point, WGS84"),
  shape_pt_sequence    STRING OPTIONS(description="Order of this point within the shape"),
  shape_dist_traveled  STRING OPTIONS(description="Distance along the shape at this point"),

  load_id              STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  source_file_hash     STRING             OPTIONS(description="SHA256 of the downloaded ZIP"),
  source_file          STRING             OPTIONS(description="File this row came from"),
  load_time            TIMESTAMP NOT NULL OPTIONS(description="When this row was written")
)
CLUSTER BY shape_id;


CREATE TABLE IF NOT EXISTS `pulso-madrid.raw.renfe_gtfs_transfers` (
  from_stop_id       STRING OPTIONS(description="Station a passenger transfers from"),
  to_stop_id         STRING OPTIONS(description="Station a passenger transfers to"),
  transfer_type      STRING OPTIONS(description="GTFS code describing how the transfer works"),
  min_transfer_time  STRING OPTIONS(description="Minimum seconds needed for the transfer"),

  load_id            STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  source_file_hash   STRING             OPTIONS(description="SHA256 of the downloaded ZIP"),
  source_file        STRING             OPTIONS(description="File this row came from"),
  load_time          TIMESTAMP NOT NULL OPTIONS(description="When this row was written")
);


CREATE TABLE IF NOT EXISTS `pulso-madrid.raw.renfe_gtfs_agency` (
  agency_id         STRING OPTIONS(description="Operator identifier"),
  agency_name       STRING OPTIONS(description="Operator name, 'Renfe Cercanias'"),
  agency_url        STRING OPTIONS(description="Operator web site"),
  agency_timezone   STRING OPTIONS(description="Timezone all times in this feed are expressed in: 'Europe/Madrid'"),
  agency_lang       STRING OPTIONS(description="Language code"),
  agency_phone      STRING OPTIONS(description="Customer phone number"),

  load_id           STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  source_file_hash  STRING             OPTIONS(description="SHA256 of the downloaded ZIP"),
  source_file       STRING             OPTIONS(description="File this row came from"),
  load_time         TIMESTAMP NOT NULL OPTIONS(description="When this row was written")
);


-- CRTM's Cercanías feed has no timetable: its trips, stop_times and shapes files
-- contain headers only. We take stations and lines from it.
CREATE TABLE IF NOT EXISTS `pulso-madrid.raw.crtm_gtfs_stops` (
  stop_id              STRING OPTIONS(description="CRTM station identifier, e.g. 'par_5_11'"),
  stop_code            STRING OPTIONS(description="CRTM station code"),
  stop_name            STRING OPTIONS(description="Station name in CRTM's spelling, e.g. 'ATOCHA'"),
  stop_desc            STRING OPTIONS(description="Street address"),
  stop_lat             STRING OPTIONS(description="Latitude, WGS84"),
  stop_lon             STRING OPTIONS(description="Longitude, WGS84"),
  zone_id              STRING OPTIONS(description="CRTM fare zone, e.g. 'A'. Renfe's feed does not carry this"),
  stop_url             STRING OPTIONS(description="Station web page"),
  location_type        STRING OPTIONS(description="GTFS code: 0 = stop, 1 = station"),
  parent_station       STRING OPTIONS(description="Parent station id, e.g. 'est_90_54'"),
  stop_timezone        STRING OPTIONS(description="Timezone, where set"),
  wheelchair_boarding  STRING OPTIONS(description="GTFS accessibility code"),

  load_id              STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  source_file_hash     STRING             OPTIONS(description="SHA256 of the downloaded ZIP"),
  source_file          STRING             OPTIONS(description="File this row came from"),
  load_time            TIMESTAMP NOT NULL OPTIONS(description="When this row was written")
)
CLUSTER BY stop_id;


CREATE TABLE IF NOT EXISTS `pulso-madrid.raw.crtm_gtfs_routes` (
  route_id          STRING OPTIONS(description="CRTM route identifier, e.g. '5__C5___'"),
  agency_id         STRING OPTIONS(description="Operator identifier, 'CRTM'"),
  route_short_name  STRING OPTIONS(description="Line name, e.g. 'C5'"),
  route_long_name   STRING OPTIONS(description="Line description"),
  route_desc        STRING OPTIONS(description="Free-text description"),
  route_type        STRING OPTIONS(description="GTFS vehicle type. '2' = rail"),
  route_url         STRING OPTIONS(description="Line web page"),
  route_color       STRING OPTIONS(description="Official CRTM line colour, 6-digit hex"),
  route_text_color  STRING OPTIONS(description="Text colour for use on route_color"),

  load_id           STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  source_file_hash  STRING             OPTIONS(description="SHA256 of the downloaded ZIP"),
  source_file       STRING             OPTIONS(description="File this row came from"),
  load_time         TIMESTAMP NOT NULL OPTIONS(description="When this row was written")
)
CLUSTER BY route_id;


-- ============================================================================
-- dimensions — the things that exist: stations, lines, routes, patterns
-- ============================================================================

CREATE TABLE IF NOT EXISTS `pulso-madrid.dimensions.cercanias_lines` (
  line_id     STRING    NOT NULL OPTIONS(description="Line name as used publicly, e.g. 'C5'. Not unique nationally: 11 Spanish networks have a line called C1"),
  line_name   STRING             OPTIONS(description="Origin and destination as text, e.g. 'Aeropuerto-T4 - Principe Pio'. Renfe pads each field to a fixed width, so the raw value carries long runs of spaces between the two names; those are collapsed here. Describes the corridor, not any individual train: 59% of trips run it end to end"),
  color_hex   STRING             OPTIONS(description="Line colour, 6-digit hex. From CRTM where the station join succeeded, otherwise Renfe"),
  n_patterns  INT64              OPTIONS(description="Count of distinct stopping patterns on this line"),

  active     BOOL               OPTIONS(description="True if this row is present in the most recent feed. Rows are never deleted: a pattern that ran last month is a fact about what ran, and the dimension is everything ever seen rather than only what runs today. On a row where active is false, every feed-relative column is as of load_time"),

  load_id     STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  load_time   TIMESTAMP NOT NULL OPTIONS(description="When this row was written"),

  PRIMARY KEY (line_id) NOT ENFORCED
);


CREATE TABLE IF NOT EXISTS `pulso-madrid.dimensions.cercanias_stations` (
  station_id    STRING    NOT NULL OPTIONS(description="Renfe stop_id, e.g. '18000'. Used as the station key everywhere in this project"),
  station_name  STRING             OPTIONS(description="Station name exactly as Renfe writes it, e.g. 'Madrid-Atocha Cercanías'"),
  display_name  STRING             OPTIONS(description="What to show a user, e.g. 'Atocha'. The 'Madrid-' prefix and ' Cercanías' suffix are stripped automatically; anything the rule cannot get right is listed in pipelines/gtfs/station_display_names.csv, because no rule can tell 'Chamartín-Clara Campoamor' (one station, two names) from 'Getafe-Centro' and 'Getafe-Industrial' (two stations)"),
  location      GEOGRAPHY          OPTIONS(description="Station point, WGS84"),
  lat           FLOAT64            OPTIONS(description="Latitude, WGS84 decimal degrees"),
  lon           FLOAT64            OPTIONS(description="Longitude, WGS84 decimal degrees"),
  crtm_stop_id  STRING             OPTIONS(description="Matching CRTM station id from dimensions.cercanias_station_join. NULL where no match was made"),
  crtm_zone_id  STRING             OPTIONS(description="CRTM fare zone, e.g. 'A'. NULL where no CRTM match was made"),
  crtm_station_id STRING           OPTIONS(description="CRTM parent station id, e.g. 'est_90_54'. CRTM sets this on only 8 of its 97 stops, the multi-modal interchanges, so it is NULL for most stations"),
  crtm_match_distance_m FLOAT64    OPTIONS(description="Metres between the Renfe and CRTM coordinates for this station. Renfe and CRTM use different ids and spellings, so matches are made on normalised name first and nearest coordinate second; this records how good the match was. Worst is 119 m. NULL where unmatched"),
  line_ids      ARRAY<STRING>      OPTIONS(description="Lines that call at this station"),

  active     BOOL               OPTIONS(description="True if this row is present in the most recent feed. Rows are never deleted: a pattern that ran last month is a fact about what ran, and the dimension is everything ever seen rather than only what runs today. On a row where active is false, every feed-relative column is as of load_time"),

  load_id       STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  load_time     TIMESTAMP NOT NULL OPTIONS(description="When this row was written"),

  PRIMARY KEY (station_id) NOT ENFORCED
)
CLUSTER BY station_id;


-- Route geometry, for drawing the network. About 23 rows.
CREATE TABLE IF NOT EXISTS `pulso-madrid.dimensions.cercanias_line_shapes` (
  shape_id   STRING    NOT NULL OPTIONS(description="Renfe's shape identifier, e.g. '10_C5' or '10_C5_INV'. Encodes the line and the direction, but not the stopping pattern: there are 23 shapes for 119 patterns, because a shape is the track a train runs on rather than the stations it calls at"),
  line_id    STRING    NOT NULL OPTIONS(description="Line this geometry belongs to, e.g. 'C5'. Parsed from shape_id"),
  geometry   GEOGRAPHY          OPTIONS(description="The route as a LINESTRING in WGS84, points ordered by Renfe's shape_pt_sequence"),
  n_points   INT64              OPTIONS(description="Number of coordinate pairs in the line. Shapes with fewer than 2 points are dropped, since they cannot be drawn"),

  active     BOOL               OPTIONS(description="True if this row is present in the most recent feed. Rows are never deleted: a pattern that ran last month is a fact about what ran, and the dimension is everything ever seen rather than only what runs today. On a row where active is false, every feed-relative column is as of load_time"),

  load_id    STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  load_time  TIMESTAMP NOT NULL OPTIONS(description="When this row was written"),

  PRIMARY KEY (shape_id) NOT ENFORCED
)
CLUSTER BY line_id;


-- One row per distinct ordered list of stations. About 119 rows for Madrid.
CREATE TABLE IF NOT EXISTS `pulso-madrid.dimensions.cercanias_stop_patterns` (
  stop_pattern_id   STRING    NOT NULL OPTIONS(description="First 12 hex characters of the SHA256 of line_id and the ordered station_id list, joined as 'line:a>b>c'. The same line and stations always produce the same id, including across feed republishes. The line is part of the input on purpose: two lines running an identical sequence of stops are different services to a passenger"),
  line_id           STRING    NOT NULL OPTIONS(description="Line these trips run on, e.g. 'C5'"),
  direction_towards_station_id STRING      OPTIONS(description="The terminus this pattern heads towards, as station_id -- what a platform sign means by 'towards Humanes'. Taken from the last station of the full-length pattern running the same way along the line. Not the same as destination_station_id, which is where THIS pattern actually stops: a C5 terminating at Fuenlabrada is still heading towards Humanes"),

  stations          STRUCT<
                      ids    ARRAY<STRING>,
                      names  ARRAY<STRING>
                    >                  OPTIONS(description="Every station a train on this pattern calls at, in order. ids and names are parallel arrays"),

  skipped           STRUCT<
                      ids    ARRAY<STRING>,
                      names  ARRAY<STRING>
                    >                  OPTIONS(description="Stations a train on this pattern passes without stopping, compared with the full-length pattern on the same line and direction. Empty for most patterns"),

  n_stops           INT64              OPTIONS(description="Number of stations called at"),
  origin_station_id STRING             OPTIONS(description="First station"),
  destination_station_id STRING        OPTIONS(description="Last station. This is the destination to display, because Renfe leaves trip_headsign empty"),
  is_full_length    BOOL               OPTIONS(description="True where this is the longest pattern on its line and direction, which is the baseline used to compute skipped stations"),
  trip_count        INT64              OPTIONS(description="Trips using this pattern in the current feed"),
  baseline_pattern_id STRING           OPTIONS(description="The full-length pattern that skipped was measured against. NULL where this pattern calls at a station the full-length one does not, meaning it runs a different physical alignment and skipped cannot be computed. C2 is the case in point: 280 trips run direct to Chamartin via Fuente de la Mora instead of the southern loop through Atocha"),

  active     BOOL               OPTIONS(description="True if this row is present in the most recent feed. Rows are never deleted: a pattern that ran last month is a fact about what ran, and the dimension is everything ever seen rather than only what runs today. On a row where active is false, every feed-relative column is as of load_time"),

  load_id           STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  load_time         TIMESTAMP NOT NULL OPTIONS(description="When this row was written"),

  PRIMARY KEY (stop_pattern_id) NOT ENFORCED
)
CLUSTER BY line_id;


-- ============================================================================
-- facts — the things that happen: what runs, and when
-- ============================================================================

-- One row per train, per date. About 37,600 rows per feed.
-- Which stations it serves is not here: that is stop_pattern_id.
CREATE TABLE IF NOT EXISTS `pulso-madrid.facts.cercanias_scheduled_trips` (
  trip_id                 STRING    NOT NULL OPTIONS(description="Renfe's trip identifier, e.g. '1035M19795C5'. Composed of service_id, train_number and line_id. Unique to one service date"),
  train_number            STRING    NOT NULL OPTIONS(description="Renfe's train number, e.g. '19795'. Shown on platform displays. The same number is used every day the journey runs, so group history by this rather than trip_id. Identifies a scheduled journey, not a physical train"),
  service_id              STRING    NOT NULL OPTIONS(description="Renfe's calendar entry, e.g. '1035M'. One per date"),
  service_date            DATE      NOT NULL OPTIONS(description="Date this train runs"),
  route_id                STRING    NOT NULL OPTIONS(description="Route this trip belongs to. Gives line and direction only"),
  line_id                 STRING    NOT NULL OPTIONS(description="Line, e.g. 'C5'"),
  stop_pattern_id         STRING    NOT NULL OPTIONS(description="Every station this train calls at, via dimensions.cercanias_stop_patterns. Stored per trip rather than per train number, because 138 train numbers run two different patterns"),

  origin_station_id       STRING             OPTIONS(description="Station this train starts from. Copied from the pattern so trips can be filtered without a join"),
  destination_station_id  STRING             OPTIONS(description="Station this train terminates at. Copied from the pattern"),
  scheduled_departure     TIMESTAMP          OPTIONS(description="Departure from the origin station as an absolute instant, converted from local time in Europe/Madrid"),
  scheduled_arrival       TIMESTAMP          OPTIONS(description="Arrival at the destination station as an absolute instant"),
  crosses_midnight        BOOL               OPTIONS(description="True where the train arrives on the day after service_date. 748 Madrid trips do"),

  load_id                 STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  load_time               TIMESTAMP NOT NULL OPTIONS(description="When this row was written"),

  PRIMARY KEY (trip_id) NOT ENFORCED
)
PARTITION BY service_date
CLUSTER BY line_id, train_number
OPTIONS(require_partition_filter = TRUE);


-- One row per station a train stops at. About 550,000 rows per feed.
CREATE TABLE IF NOT EXISTS `pulso-madrid.facts.cercanias_scheduled_stops` (
  trip_id               STRING    NOT NULL OPTIONS(description="Train this stop belongs to"),
  service_date          DATE      NOT NULL OPTIONS(description="Date this train runs. Same as the parent trip"),
  stop_number           INT64     NOT NULL OPTIONS(description="Position of this stop within this trip, numbered 1 upward. Computed by us, because Renfe's own numbering starts above 1 for 2,392 trips and is not consistent"),
  station_id            STRING    NOT NULL OPTIONS(description="Station, joins to dimensions.cercanias_stations"),

  scheduled_arrival     TIMESTAMP          OPTIONS(description="Arrival as an absolute instant, converted from local time in Europe/Madrid"),
  scheduled_departure   TIMESTAMP          OPTIONS(description="Departure as an absolute instant. Differs from arrival on 9.7% of Madrid rows"),
  day_offset            INT64              OPTIONS(description="0 if this stop is on service_date, 1 if it falls on the following day. Based on the departure. Renfe writes times such as 24:05 rather than 00:05, so this states the day for display as '00:05 +1'. On 12 Madrid rows a train arrives before midnight and departs after; read scheduled_arrival and scheduled_departure directly if that distinction matters"),

  source_arrival_time   STRING             OPTIONS(description="arrival_time exactly as Renfe published it, e.g. '24:05:00'"),
  source_departure_time STRING             OPTIONS(description="departure_time exactly as Renfe published it"),
  source_stop_sequence  INT64              OPTIONS(description="Renfe's own stop_sequence value, kept so stop_number can be checked against it"),

  load_id               STRING    NOT NULL OPTIONS(description="Identifier of the pipeline run that wrote this row"),
  load_time             TIMESTAMP NOT NULL OPTIONS(description="When this row was written"),

  PRIMARY KEY (trip_id, stop_number) NOT ENFORCED
)
PARTITION BY service_date
CLUSTER BY station_id, trip_id
OPTIONS(require_partition_filter = TRUE);


-- ============================================================================
-- ops — pipeline run records. Every source writes here, so no source prefix.
-- ============================================================================

CREATE TABLE IF NOT EXISTS `pulso-madrid.ops.load_runs` (
  load_id           STRING    NOT NULL OPTIONS(description="Identifier for this run. Written to load_id on every row the run produces"),
  source            STRING    NOT NULL OPTIONS(description="Which source was loaded: 'renfe_gtfs', 'crtm_gtfs', 'padron' and so on"),
  started_at        TIMESTAMP NOT NULL OPTIONS(description="When the run started"),
  finished_at       TIMESTAMP          OPTIONS(description="When the run finished. NULL while running"),
  status            STRING             OPTIONS(description="'running', 'succeeded', 'failed', or 'abandoned' where a run was killed before it could record an outcome and a later run swept it"),
  source_url        STRING             OPTIONS(description="URL the file was downloaded from"),
  source_file_hash  STRING             OPTIONS(description="SHA256 of the downloaded file. If it matches the previous run, the feed has not changed and the load can be skipped"),
  archive_uri       STRING             OPTIONS(description="gs:// path of the unmodified original file"),
  rows_read         INT64              OPTIONS(description="Rows read from the source, after filtering to Madrid"),
  rows_loaded       INT64              OPTIONS(description="Rows written to raw"),
  rows_rejected     INT64              OPTIONS(description="Rows that failed validation and went to ops.rejected_rows"),
  error_message     STRING             OPTIONS(description="Failure detail where status is 'failed'"),
  source_timestamp  TIMESTAMP          OPTIONS(description="Feed header timestamp of the last publication in this batch. Distinguishes a feed that is live and empty, which is the network asleep, from a feed that has stopped updating, which is an outage. NULL for sources that are files rather than feeds, which is every source but renfe_gtfs_rt"),

  load_time         TIMESTAMP NOT NULL OPTIONS(description="When this row was written"),

  PRIMARY KEY (load_id) NOT ENFORCED
)
PARTITION BY DATE(started_at)
CLUSTER BY source;


-- Rows the loader refused, with the reason. Normally empty.
-- Rows are never dropped silently: a row that fails validation lands here.
CREATE TABLE IF NOT EXISTS `pulso-madrid.ops.rejected_rows` (
  load_id      STRING    NOT NULL OPTIONS(description="Run that rejected this row, joins to ops.load_runs"),
  rejected_at  TIMESTAMP NOT NULL OPTIONS(description="When the row was rejected"),
  source       STRING    NOT NULL OPTIONS(description="Which source the row came from, e.g. 'renfe_gtfs'"),
  source_file  STRING             OPTIONS(description="File the row came from, e.g. 'stop_times.txt'"),
  raw_row      STRING             OPTIONS(description="The row exactly as read, as JSON text, so it can be inspected or replayed. STRING rather than JSON because DuckDB writes JSON into Parquet as BYTES and BigQuery will not load that into a JSON column. Use PARSE_JSON() to query inside it"),
  reason       STRING    NOT NULL OPTIONS(description="Which validation failed, e.g. 'stop_id not present in stops.txt'"),

  load_time    TIMESTAMP NOT NULL OPTIONS(description="When this row was written"),

  PRIMARY KEY (load_id, rejected_at) NOT ENFORCED
)
PARTITION BY DATE(rejected_at)
CLUSTER BY source;

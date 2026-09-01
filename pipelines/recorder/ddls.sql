-- ============================================================================
-- Pulso Madrid — observed schema (Stage 2, recorder)
--
-- Source  : Renfe GTFS-RT — vehicle_positions.json, trip_updates.json,
--           alerts.json, all three published under one header timestamp every
--           ~20s (16-24s jitter, one 32s gap, measured over 65 publications).
-- Scope   : Madrid only, everywhere, including the GCS archive. The filter is a
--           join on our own trip_id set, never a lat/lon box: of 90 feed
--           trip_ids that did not join, zero touched a Madrid station.
-- Context : docs/architecture/stage-2-recorder-plan.md, docs/decisions/0003, 0004
--
-- Parallel names, per conventions.md §2 — cercanias_scheduled_trips is the
-- comparison partner of cercanias_observed_trains, so both lead with the same
-- five columns in the same order and every reliability metric is a comparison
-- across that pair.
--
-- Two clocks on every row, on purpose: observed_at is when Renfe published the
-- observation, load_time is when we wrote it. Their difference is the ingestion
-- lag SLO, and they fail distinguishably -- fresh observed_at with stale
-- load_time is a recorder falling behind, the reverse is a backfill.
-- ============================================================================


-- ============================================================================
-- New tables
-- ============================================================================

-- One row per Madrid train per feed publication.
--
-- The two vehicle feeds are merged into this one table because they are one
-- publication: all 65 sampled publications carried a byte-identical header
-- timestamp in both, 2,095 Madrid trips appeared in both, 37 in trip_updates
-- only (every one CANCELED), and none in vehicle_positions only.
--
-- (trip_id, observed_at) is the idempotency key: unique on 3,387 of 3,387
-- observations, stable across retries and restarts, and it makes "we polled
-- twice" and "the feed did not update" the same harmless event. Poll at 10s
-- and deduplicate on it; publication jitter means a 20s poll drops messages.
--
-- ~90 trains x ~3,510 active publications ~= 316k rows/day, 115M/year, ~27 GB.
CREATE TABLE IF NOT EXISTS `pulso-madrid.facts.cercanias_observed_trains` (
  trip_id               STRING    NOT NULL OPTIONS(description="Renfe's trip identifier, joins to facts.cercanias_scheduled_trips. Unique to one service date"),
  service_date          DATE      NOT NULL OPTIONS(description="Service date of the trip, taken from facts.cercanias_scheduled_trips rather than from the observation clock. A train running after midnight belongs to the previous service date: 748 Madrid trips cross midnight, and dating them by when they were observed would split every one across two partitions"),
  observed_at           TIMESTAMP NOT NULL OPTIONS(description="Feed header timestamp of the publication this observation came from -- when Renfe published it, not when we stored it. All three Renfe realtime feeds publish under one identical header timestamp, verified on 65 of 65 publications, so this is the publication instant for the whole system rather than for one feed"),
  train_number          STRING    NOT NULL OPTIONS(description="Renfe's train number. Copied from the schedule rather than joined, against conventions.md §4, because this table reaches 115M rows a year and every historical comparison groups by train_number: the join would be paid on every one of them. stop_number is deliberately NOT copied -- reconstruction already joins to facts.cercanias_scheduled_stops for scheduled_arrival, and stop_number rides along in that join for free"),
  line_id               STRING    NOT NULL OPTIONS(description="Line, e.g. 'C5'. Copied from the schedule for the same reason as train_number, and it is the first clustering column"),

  station_id            STRING             OPTIONS(description="The station the feed's stopId names. Its meaning depends on current_status: the station the train is standing at, approaching, or heading towards. Every stopId seen on a Madrid trip was present in dimensions.cercanias_stations. NULL where this publication carried no vehicle entity for the trip"),
  current_status        STRING             OPTIONS(description="'STOPPED_AT', 'INCOMING_AT' or 'IN_TRANSIT_TO'. Observed 2,868 / 495 / 514 over a morning sample. STOPPED_AT is the arrival evidence the whole reconstruction rests on, and it is incomplete: only 58% of scheduled intermediate calls ever produced one. NULL where this publication carried no vehicle entity for the trip"),
  lat                   FLOAT64            OPTIONS(description="Latitude as published, WGS84. Not a GPS fix: only 622 distinct coordinates appeared across 3,824 observations, and the position sits a median 30 m from some station but a p90 of 1.9 km from the station this same message names in station_id. Never infer arrival from coordinates. Stored as a plain pair rather than GEOGRAPHY: at 115M rows a year the geography type is the most expensive column in the project, spent on the least trustworthy value in it. Use ST_GEOGPOINT(lon, lat) on the rare query that needs it. NULL for a cancelled trip, and for the 3 of 3,104 vehicle entities that carried no position"),
  lon                   FLOAT64            OPTIONS(description="Longitude as published, WGS84. Same caveats as lat"),
  platform_number       STRING             OPTIONS(description="Platform, parsed from vehicle.label, e.g. '4' from 'C5-19507-PLATF.(4)'. The label matched that form on 3,877 of 3,877 Madrid observations. Platform appears in no static source we hold, and it is live rather than fixed: 19 of 71 trips changed platform inside one 12-minute window. STRING because the value is Renfe's, not ours -- '0' occurs on 21 observations and its meaning is unverified. NULL where this publication carried no vehicle entity for the trip, or where the label did not parse"),
  source_vehicle_label  STRING             OPTIONS(description="vehicle.label exactly as Renfe published it, e.g. 'C5-19507-PLATF.(4)', so the platform parse stays auditable. vehicle.id is not stored: it equalled train_number on 71 of 71 Madrid trips, so it names the service and not a physical unit, and no rolling-stock analysis is possible from this feed"),
  vehicle_timestamp     TIMESTAMP          OPTIONS(description="vehicle.timestamp -- Renfe's own clock for when this vehicle was observed, as distinct from observed_at, when the feed published it. Ran 0-1s behind observed_at in every sample, so the two are not yet known to diverge usefully. Kept because a growing gap is exactly what a stale-vehicle bug would look like. NULL where this publication carried no vehicle entity for the trip"),

  schedule_relationship STRING             OPTIONS(description="'SCHEDULED' or 'CANCELED', as published. 61 of 3,938 Madrid observations were CANCELED, and every one carried no stopTimeUpdate and no vehicle entity, which is how a cancellation is distinguished from a train that merely stopped being reported. NULL only where the trip update fetch failed for this publication"),
  next_station_id       STRING             OPTIONS(description="Station of the single stopTimeUpdate that carries a delay. The feed publishes the next stop only, never the rest of the journey: on 3,877 of 3,877 Madrid observations exactly one entry carried a delay and it was always the first. Actual arrival times therefore have to be reconstructed across publications, not read off. NULL for a cancelled trip"),
  predicted_arrival     TIMESTAMP          OPTIONS(description="arrival.time for next_station_id, Renfe's predicted arrival. Not derivable from source_delay_seconds and the schedule, which is why both are stored: over 4,460 checks, predicted_arrival minus the delay matched our scheduled_arrival 92.2% of the time, scheduled_departure 7.7%, and neither on 4 rows. Stored only as a timestamp and not also as a source string, unlike the static feed's source_arrival_time, because the source is epoch seconds with no timezone or 24:05:00 trap to audit. NULL for a cancelled trip"),
  source_delay_seconds  INT64              OPTIONS(description="arrival.delay exactly as Renfe published it, signed, negative meaning early. Two traps: it is quantised to whole minutes, every observed value being a multiple of 60; and its baseline is inconsistent, being measured from scheduled arrival on 92.2% of rows and from scheduled departure on 7.7%. Compute delay from the schedule rather than trusting this. NULL for a cancelled trip"),
  skipped_station_ids   ARRAY<STRING>      OPTIONS(description="Stations this publication marked SKIPPED. Every stopTimeUpdate entry after the first was SKIPPED, 778 of 778. Empty for almost all observations: only 101 of 3,938 carried more than one entry. Trap: next_station_id itself appears in this list on 74 of those 101, so a train's own next stop can be marked skipped"),
  n_stop_time_updates   INT64              OPTIONS(description="How many stopTimeUpdate entries the feed sent, kept as a drift check rather than as data, and not derivable from anything else here because only the first entry is modelled. The value was 1 on 3,778 observations, 0 on the 61 cancellations, and 2, 4, 10 or 15 on the rest. If this distribution moves, the feed's shape changed and next_station_id stopped meaning what it says. NULL only where the trip update fetch failed for this publication"),
  wheelchair_accessible BOOL               OPTIONS(description="TRUE where tripUpdate.vehicle.wheelchairAccessible was 'WHEELCHAIR_ACCESSIBLE' (56 observations), FALSE where 'WHEELCHAIR_INACCESSIBLE' (3,308). NULL on 574 observations, where the feed omits the field entirely. Trap: GTFS defines a third value, UNKNOWN_ACCESSIBILITY, which Renfe has not been seen to send -- an unmapped value is recorded in ops.rejected_rows rather than coerced, or 'the operator says unknown' becomes indistinguishable from 'the operator said nothing'. This is an observation, not a trip attribute: 18 of 90 trips carried more than one value, one flipping accessible to inaccessible mid-journey"),

  load_id               STRING    NOT NULL OPTIONS(description="Identifier of the recorder batch that wrote this row, joins to ops.load_runs"),
  load_time             TIMESTAMP NOT NULL OPTIONS(description="When this row was written. load_time minus observed_at is the ingestion lag, which is the feed-freshness SLO"),

  PRIMARY KEY (trip_id, observed_at) NOT ENFORCED
)
PARTITION BY service_date
CLUSTER BY line_id, trip_id, station_id
OPTIONS(require_partition_filter = TRUE);


-- One row per version of one Madrid alert.
--
-- A version is written when an alert first appears, each time its canonical
-- content changes, and once more when it leaves the feed. That last row is the
-- only record of when an alert ended: Renfe publishes activePeriod.start on all
-- 71 alerts sampled and an end on none of them, so an alert ends by vanishing.
-- The validity window of a version is derived with LEAD(observed_at), which
-- keeps this table append-only like every other fact table here.
--
-- 9 of 71 alerts were Madrid at the time of writing. A few hundred rows a day.
CREATE TABLE IF NOT EXISTS `pulso-madrid.facts.cercanias_observed_alerts` (
  alert_id             STRING    NOT NULL OPTIONS(description="Renfe's alert identifier, e.g. 'AVISO_510970'. Stable across publications"),
  service_date         DATE      NOT NULL OPTIONS(description="Service date this version was observed in, so that alerts partition-align with facts.cercanias_observed_trains on every join. An alert has no trip to take a date from, so it is derived by clock: the Madrid date of observed_at, with 00:00-02:59 counted as the previous date. That cut is safe rather than arbitrary -- across a full timetable, Cercanias schedules 706 calls in hour 00, 2 in hour 01, none at all in hours 02 and 03, and 16 in hour 04, so any cut drawn inside that empty two-hour window produces identical answers"),
  observed_at          TIMESTAMP NOT NULL OPTIONS(description="Feed header timestamp of the publication in which this version was first seen. On an 'ended' row, the first publication in which the alert was absent"),
  version_status       STRING    NOT NULL OPTIONS(description="'active' where this row records content present in the feed, 'ended' where it records the alert's disappearance. On an 'ended' row every content column is NULL: the content of the last active version is the row before it"),
  content_hash         STRING    NOT NULL OPTIONS(description="First 12 hex characters of the SHA256 of the alert canonicalised -- translations sorted, informedEntity sorted -- which is what makes a new row mean a real change. Without canonicalising, 13 of 71 alerts alternate content every other publication, because Renfe reorders the translation array between publications and nothing else about them differs"),

  active_period_start  TIMESTAMP          OPTIONS(description="activePeriod.start as published, when Renfe says the disruption began. Present on 71 of 71 alerts sampled. No end is ever published, which is why version_status exists. NULL on an 'ended' row"),
  n_active_periods     INT64              OPTIONS(description="How many activePeriod entries the feed sent, kept as a drift check and not derivable from anything else here because only the first start is modelled. Every alert sampled sent exactly one entry carrying only a start. If this exceeds 1, active_period_start has stopped telling the whole story. NULL on an 'ended' row"),
  effect               STRING             OPTIONS(description="GTFS-RT effect enum, e.g. 'MODIFIED_SERVICE'. Renfe almost never sets it: 1 of 71 alerts sampled. NULL where the feed omits it, which is the normal case. The cause field is not stored at all, being absent on all 71"),
  source_translations  ARRAY<STRUCT<
                         language STRING,
                         text     STRING
                       >>                 OPTIONS(description="Every translation as published, sorted canonically. Kept as an array rather than flattened to a Spanish column because the language labels are wrong: a Catalan text was published tagged 'es'. Choosing a display language is a decision for the derived layer, where it can be made from the text rather than from Renfe's label"),
  route_ids            ARRAY<STRING>      OPTIONS(description="Every informedEntity.routeId as published, including non-Madrid routes on an alert that also names a Madrid one -- a disruption spanning several networks is one fact, and truncating it would misreport its extent. One alert sampled named 76 entities"),
  line_ids             ARRAY<STRING>      OPTIONS(description="Madrid lines this alert names, derived from the '10T%' route ids by the same rule the static pipeline uses. This is the column joins and filters actually use"),
  station_ids          ARRAY<STRING>      OPTIONS(description="Every informedEntity.stopId, joining to dimensions.cercanias_stations. Rare: 27 entries across 71 alerts. Empty where the alert is scoped to routes only"),
  trip_ids             ARRAY<STRING>      OPTIONS(description="Every informedEntity.trip.tripId, joining to facts.cercanias_scheduled_trips. Rarer still: 1 entry across 71 alerts. Empty for almost every alert"),
  source_payload       STRING             OPTIONS(description="The whole canonicalised alert as JSON text, so a field we chose not to model is still queryable without going back to the archive. STRING rather than JSON for the same reason as ops.rejected_rows.raw_row: DuckDB writes JSON into Parquet as BYTES and BigQuery will not load that into a JSON column. Use PARSE_JSON() to query inside it. NULL on an 'ended' row"),

  load_id              STRING    NOT NULL OPTIONS(description="Identifier of the recorder batch that wrote this row, joins to ops.load_runs"),
  load_time            TIMESTAMP NOT NULL OPTIONS(description="When this row was written. load_time minus observed_at is the ingestion lag, which is the feed-freshness SLO"),

  PRIMARY KEY (alert_id, observed_at) NOT ENFORCED
)
PARTITION BY service_date
CLUSTER BY alert_id;


-- ============================================================================
-- Changes to existing tables — run once, in this order, BEFORE the recorder
-- ============================================================================
-- require_partition_filter is a table option, not part of the schema, so these
-- flip in place: no reload, no WRITE_TRUNCATE, no exposure to the trap where an
-- inferred schema silently discards every column description.
--
-- Applied to the facts tables only, not to ops. The convention that earns its
-- keep is "require a partition filter on tables that grow without bound" --
-- ops.load_runs gains about one row per run and ops.rejected_rows is normally
-- empty, and both are what you query ad hoc while something is broken. Setting
-- it there would break load.finish_run and load.previous_hash, which look rows
-- up by load_id and by source, with no date to filter on.
--
-- pipelines/gtfs/src/pulso_gtfs/load.py and scripts/audit-gtfs.sh have already
-- been changed to satisfy this. Running these statements before deploying that
-- code will break the next daily GTFS load.

ALTER TABLE `pulso-madrid.facts.cercanias_scheduled_trips`
  SET OPTIONS (require_partition_filter = TRUE);

ALTER TABLE `pulso-madrid.facts.cercanias_scheduled_stops`
  SET OPTIONS (require_partition_filter = TRUE);

-- A live feed with zero entities carries a fresh header timestamp; a stalled one
-- carries an old one. Without this column an empty overnight feed and a broken
-- upstream look identical in ops.load_runs. No new ops table is needed: a
-- recorder batch maps onto a load_runs row with rows_read, rows_loaded,
-- rows_rejected and source_file_hash all keeping their existing meanings.

ALTER TABLE `pulso-madrid.ops.load_runs`
  ADD COLUMN IF NOT EXISTS source_timestamp TIMESTAMP
  OPTIONS(description="Feed header timestamp of the last publication in this batch. Distinguishes a feed that is live and empty, which is the network asleep, from a feed that has stopped updating, which is an outage");

-- Renfe serves incomplete national snapshots from some of its backends: a fresh header
-- timestamp, a valid payload, and Madrid absent entirely, in roughly one publication in
-- five. The recorder refuses those, and once the rows are gone a refused publication is
-- indistinguishable from the network being asleep -- so the count is recorded.

ALTER TABLE `pulso-madrid.ops.load_runs`
  ADD COLUMN IF NOT EXISTS partial_publications INT64
  OPTIONS(description="Publications in this batch that were refused as incomplete. Renfe serves partial national snapshots from some backends -- a fresh header timestamp and a valid payload with Madrid absent entirely, measured at 170-215 entities against 270-320 for a full one. NULL for sources that are files rather than feeds");

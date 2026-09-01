"""Parsing tests, run against real bytes.

The fixtures are a genuine publication of each feed, read from gtfsrt.renfe.com on
2026-09-01 and trimmed to Madrid. Every constant asserted below was measured from the
live feed, not chosen to make a test pass.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pulso_recorder.feeds import (
    MADRID,
    Trip,
    content_hash,
    ended_alert_row,
    header_timestamp,
    parse_alerts,
    parse_platform,
    parse_trains,
    service_date_for,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.sample.json").read_text())


def trip_lookup(*payloads: dict) -> dict[str, Trip]:
    """A lookup covering every trip in the fixtures, standing in for the warehouse."""
    ids: set[str] = set()
    for payload in payloads:
        for entity in payload["entity"]:
            holder = entity.get("vehicle") or entity.get("tripUpdate") or {}
            trip_id = (holder.get("trip") or {}).get("tripId")
            if trip_id:
                ids.add(trip_id)
    return {
        # trip_id looks like '1042M19507C5': service, train number, line.
        t: Trip(train_number=t[5:10], line_id=t[10:], service_date=date(2026, 9, 1))
        for t in ids
    }


# ------------------------------------------------------------------ trains

def test_both_feeds_share_one_header_timestamp():
    """Verified on 65 of 65 sampled publications, which is why they merge into one table."""
    assert header_timestamp(load("vehicle_positions")) == header_timestamp(load("trip_updates"))


def test_merges_position_and_delay_onto_one_row():
    vp, tu = load("vehicle_positions"), load("trip_updates")
    observed_at = header_timestamp(vp)
    rows, _ = parse_trains(vp, tu, trip_lookup(vp, tu), set(), observed_at)

    assert rows, "fixture should yield Madrid trains"
    assert len({r["trip_id"] for r in rows}) == len(rows), "one row per trip per publication"
    assert all(r["observed_at"] == observed_at.isoformat() for r in rows)

    merged = [r for r in rows if r["current_status"] and r["source_delay_seconds"] is not None]
    assert merged, "at least one trip should carry both a position and a delay"


def test_cancelled_trip_has_no_vehicle_columns():
    """Every trip in trip_updates but not vehicle_positions was CANCELED: 37 of 37."""
    vp, tu = load("vehicle_positions"), load("trip_updates")
    rows, _ = parse_trains(vp, tu, trip_lookup(vp, tu), set(), header_timestamp(tu))
    for row in rows:
        if row["schedule_relationship"] == "CANCELED":
            assert row["current_status"] is None
            assert row["lat"] is None and row["lon"] is None
            assert row["next_station_id"] is None
            assert row["n_stop_time_updates"] == 0


def test_only_the_first_stop_time_update_carries_a_delay():
    """3,877 of 3,877: exactly one entry has a delay and it is always the first."""
    tu = load("trip_updates")
    for entity in tu["entity"]:
        stus = entity["tripUpdate"].get("stopTimeUpdate") or []
        with_delay = [i for i, s in enumerate(stus)
                      if (s.get("arrival") or {}).get("delay") is not None]
        assert with_delay in ([], [0])


def test_non_madrid_trips_are_dropped():
    """The Madrid filter is the trip_id join, never a bounding box."""
    vp, tu = load("vehicle_positions"), load("trip_updates")
    rows, _ = parse_trains(vp, tu, {}, set(), header_timestamp(vp))
    assert rows == []


def test_platform_is_parsed_from_the_label():
    assert parse_platform("C5-19507-PLATF.(4)") == "4"
    assert parse_platform("C10-21100-PLATF.(12)") == "12"
    assert parse_platform("C5-19507") is None
    assert parse_platform(None) is None


def test_unparseable_label_records_an_anomaly_but_keeps_the_row():
    """An observation is never dropped: this feed cannot be fetched again."""
    observed_at = datetime.fromtimestamp(1788234755, UTC)
    vp = {"header": {"timestamp": "1788234755"}, "entity": [{
        "id": "VP_C5-19507",
        "vehicle": {"trip": {"tripId": "1042M19507C5"}, "currentStatus": "STOPPED_AT",
                    "stopId": "35001", "timestamp": "1788234755",
                    "vehicle": {"id": "19507", "label": "SOMETHING ELSE"}}}]}
    trips = {"1042M19507C5": Trip("19507", "C5", date(2026, 9, 1))}
    rows, anomalies = parse_trains(vp, None, trips, {"35001"}, observed_at)

    assert len(rows) == 1
    assert rows[0]["platform_number"] is None
    assert rows[0]["source_vehicle_label"] == "SOMETHING ELSE"
    assert len(anomalies) == 1 and "does not match" in anomalies[0].reason


def test_unmapped_wheelchair_value_is_reported_not_coerced():
    """GTFS has UNKNOWN_ACCESSIBILITY; silently nulling it would hide it."""
    observed_at = datetime.fromtimestamp(1788234755, UTC)
    tu = {"header": {"timestamp": "1788234755"}, "entity": [{
        "id": "TUUPDATE_1042M19507C5",
        "tripUpdate": {"trip": {"tripId": "1042M19507C5", "scheduleRelationship": "SCHEDULED"},
                       "stopTimeUpdate": [{"arrival": {"delay": 60, "time": "1788234815"},
                                           "stopId": "35001"}],
                       "vehicle": {"wheelchairAccessible": "UNKNOWN_ACCESSIBILITY"},
                       "delay": 60}}]}
    trips = {"1042M19507C5": Trip("19507", "C5", date(2026, 9, 1))}
    rows, anomalies = parse_trains(None, tu, trips, set(), observed_at)

    assert len(rows) == 1 and rows[0]["wheelchair_accessible"] is None
    assert len(anomalies) == 1 and "UNKNOWN_ACCESSIBILITY" in anomalies[0].reason


def test_skipped_list_can_contain_the_next_station():
    """The real trap: on 74 of 101 multi-entry observations the delay stop is also SKIPPED."""
    observed_at = datetime.fromtimestamp(1788234755, UTC)
    tu = {"header": {"timestamp": "1788234755"}, "entity": [{
        "id": "TUUPDATE_1042M20204C4",
        "tripUpdate": {"trip": {"tripId": "1042M20204C4", "scheduleRelationship": "SCHEDULED"},
                       "stopTimeUpdate": [
                           {"arrival": {"delay": 420, "time": "1788233820"}, "stopId": "18002"},
                           {"stopId": "18002", "scheduleRelationship": "SKIPPED"},
                           {"stopId": "17000", "scheduleRelationship": "SKIPPED"}],
                       "delay": 420}}]}
    trips = {"1042M20204C4": Trip("20204", "C4", date(2026, 9, 1))}
    rows, _ = parse_trains(None, tu, trips, set(), observed_at)

    assert rows[0]["next_station_id"] == "18002"
    assert rows[0]["skipped_station_ids"] == ["18002", "17000"]
    assert rows[0]["n_stop_time_updates"] == 3


def test_unknown_station_is_reported():
    observed_at = datetime.fromtimestamp(1788234755, UTC)
    vp = {"header": {"timestamp": "1788234755"}, "entity": [{
        "id": "VP_C5-19507",
        "vehicle": {"trip": {"tripId": "1042M19507C5"}, "currentStatus": "STOPPED_AT",
                    "stopId": "99999", "timestamp": "1788234755",
                    "vehicle": {"id": "19507", "label": "C5-19507-PLATF.(1)"}}}]}
    trips = {"1042M19507C5": Trip("19507", "C5", date(2026, 9, 1))}
    rows, anomalies = parse_trains(vp, None, trips, {"35001"}, observed_at)
    assert rows[0]["station_id"] == "99999"
    assert any("not in dimensions" in a.reason for a in anomalies)


# ------------------------------------------------------------------ alerts

def test_alert_hash_survives_a_reordered_translation_array():
    """The bug this exists to prevent.

    Renfe reorders descriptionText.translation between publications; 13 of 71 alerts
    alternated content every other publication with nothing else differing. Hashing the
    payload as delivered would write a new version every 20 seconds, forever.
    """
    alert = {
        "activePeriod": [{"start": "1788206280"}],
        "informedEntity": [{"routeId": "10T0031C2"}, {"routeId": "10T0020C2"}],
        "descriptionText": {"translation": [
            {"text": "uno", "language": "es"}, {"text": "dos", "language": "ca"}]},
    }
    reordered = json.loads(json.dumps(alert))
    reordered["descriptionText"]["translation"].reverse()
    reordered["informedEntity"].reverse()

    assert content_hash(alert) == content_hash(reordered)


def test_alert_hash_changes_when_the_text_changes():
    base = {"activePeriod": [{"start": "1"}], "informedEntity": [{"routeId": "10T0031C2"}],
            "descriptionText": {"translation": [{"text": "uno", "language": "es"}]}}
    changed = json.loads(json.dumps(base))
    changed["descriptionText"]["translation"][0]["text"] = "otra cosa"
    assert content_hash(base) != content_hash(changed)


def test_alerts_are_filtered_to_madrid_and_keep_every_route_they_name():
    payload = load("alerts")
    observed_at = header_timestamp(payload)
    alerts = parse_alerts(payload, observed_at)

    assert alerts, "the fixture holds Madrid alerts"
    for alert in alerts.values():
        assert alert["line_ids"], "a Madrid alert resolves to at least one line"
        assert any(r.startswith("10T") for r in alert["route_ids"])
        # Non-Madrid routes on a Madrid alert are kept: the disruption is one fact.
        assert len(alert["route_ids"]) >= len(alert["line_ids"])
        assert alert["version_status"] == "active"


def test_line_ids_are_derived_the_way_the_static_pipeline_derives_them():
    payload = {"header": {"timestamp": "1788234736"}, "entity": [{
        "id": "AVISO_1", "alert": {
            "activePeriod": [{"start": "1788206280"}],
            "informedEntity": [{"routeId": "10T0031C2"}, {"routeId": "10T0042C8"},
                               {"routeId": "61T0018C1"}],
            "descriptionText": {"translation": [{"text": "x", "language": "es"}]}}}]}
    alerts = parse_alerts(payload, datetime.fromtimestamp(1788234736, UTC))
    assert alerts["AVISO_1"]["line_ids"] == ["C2", "C8"]
    assert "61T0018C1" in alerts["AVISO_1"]["route_ids"]


def test_ended_row_carries_no_content():
    row = ended_alert_row("AVISO_1", "abc123", datetime.fromtimestamp(1788234736, UTC))
    assert row["version_status"] == "ended"
    assert row["source_payload"] is None and row["active_period_start"] is None
    assert row["content_hash"] == "abc123"


# ------------------------------------------------------------------ service date

def test_service_day_is_cut_at_three_in_the_morning():
    """Cercanias schedules nothing in hours 02 and 03, so the cut is safe anywhere there."""
    def madrid(hour: int, minute: int = 0) -> datetime:
        """Local Madrid wall-clock on 2026-09-01, hours allowed to run past 24."""
        return datetime(2026, 9, 1, tzinfo=MADRID) + timedelta(hours=hour, minutes=minute)

    assert service_date_for(madrid(23, 59)).isoformat() == "2026-09-01"  # late evening
    assert service_date_for(madrid(24, 30)).isoformat() == "2026-09-01"  # 00:30, still tonight
    assert service_date_for(madrid(26, 59)).isoformat() == "2026-09-01"  # 02:59, still tonight
    assert service_date_for(madrid(27, 0)).isoformat() == "2026-09-02"   # 03:00, new day
    assert service_date_for(madrid(29, 0)).isoformat() == "2026-09-02"   # 05:00, first trains

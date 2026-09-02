"""Tests against real bytes.

The arrivals fixture is a genuine response from gtfsrt CRTM's widget endpoint, captured
2026-09-02. Every constant asserted here was measured from the live sources, not chosen
to make a test pass.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pulso_metro.config import TIER1
from pulso_metro.names import format_station_name
from pulso_metro.poll import parse, service_date_for
from pulso_metro.stations import line_of, read_topology

FIXTURES = Path(__file__).parent / "fixtures"


def arrivals_payload() -> dict:
    return json.loads((FIXTURES / "arrivals.sample.json").read_text())


# ------------------------------------------------------------------ names

def test_accents_that_cannot_be_derived():
    assert format_station_name("NUÑEZ DE BALBOA") == "Núñez de Balboa"
    assert format_station_name("ALONSO MARTINEZ") == "Alonso Martínez"
    assert format_station_name("ESTACION DEL ARTE") == "Estación del Arte"


def test_particles_stay_lower_inside_a_name_but_not_at_the_start():
    assert format_station_name("GUZMAN EL BUENO") == "Guzmán el Bueno"
    assert format_station_name("ALAMEDA DE OSUNA") == "Alameda de Osuna"
    assert format_station_name("EL CARMEN") == "El Carmen"


def test_hyphens_and_apostrophes_start_a_new_word():
    assert format_station_name("O'DONNELL") == "O'Donnell"
    assert format_station_name("SAN FERMIN-ORCASUR") == "San Fermín-Orcasur"
    assert format_station_name("ARGANZUELA-PLANETARIO") == "Arganzuela-Planetario"


def test_roman_numerals_and_terminal_codes_stay_upper():
    assert format_station_name("PIO XII") == "Pío XII"
    assert format_station_name("ALFONSO XIII") == "Alfonso XIII"
    assert format_station_name("AEROPUERTO T1-T2-T3") == "Aeropuerto T1-T2-T3"


def test_existing_diacritics_survive():
    """CRTM keeps n-tilde and u-diaeresis but strips acute accents."""
    assert format_station_name("PEÑAGRANDE") == "Peñagrande"
    assert format_station_name("ARGÜELLES") == "Argüelles"


# ------------------------------------------------------------------ topology

def test_stations_are_physical_not_platforms(paradas):
    """CRTM codes platforms: 291 codes for 242 stations, Sol being three of them."""
    _, stations = read_topology(paradas)
    assert len(stations) == 242
    sol = next(s for s in stations.values() if s["station_name"] == "SOL")
    assert sol["source_station_ids"] == ["4_12", "4_35", "4_48"]
    assert sol["line_ids"] == ["1", "2", "3"]
    assert sol["station_id"] == "4_12", "the lowest code is the one polled"


def test_every_tier1_station_exists(paradas):
    _, stations = read_topology(paradas)
    names = {s["station_name"] for s in stations.values()}
    assert TIER1 <= names, sorted(TIER1 - names)


def test_line_3_is_present_although_the_gtfs_omits_it(paradas):
    stops, _ = read_topology(paradas)
    l3 = [s for s in stops if s["line_id"] == "3"]
    assert l3, "line 3 has no trips in the GTFS; this source is the only one that has it"


def test_branches_reduce_to_the_line_the_live_feed_names():
    assert line_of("10b") == "10"
    assert line_of("9A") == "9"
    assert line_of("12-1") == "12"
    assert line_of("7a") == "7"
    assert line_of("R") == "R"


# ------------------------------------------------------------------ arrivals

def test_parses_a_real_response():
    observed_at = datetime(2026, 9, 2, 5, 35, 30, tzinfo=UTC)
    row = parse(arrivals_payload(), "4_190", observed_at, 1)
    assert row["station_id"] == "4_190"
    assert row["observed_at"] == observed_at.isoformat()
    assert row["n_arrivals"] == len(row["arrivals"])
    for a in row["arrivals"]:
        assert a["line_id"] and a["direction"] in (1, 2)
        assert a["predicted_arrival"].endswith("+00:00"), "normalised to UTC"


def test_observed_at_is_our_clock_not_theirs():
    """CRTM's actualDate changes on every request, so it cannot be the idempotency key."""
    observed_at = datetime(2026, 9, 2, 5, 35, 30, tzinfo=UTC)
    row = parse(arrivals_payload(), "4_190", observed_at, 1)
    assert row["observed_at"] == observed_at.isoformat()
    assert row["source_timestamp"] != row["observed_at"]


def test_a_station_with_no_upcoming_trains_parses():
    """`times` is an empty object, not an empty list — a naive ['Time'] raises."""
    row = parse({"stopTimes": {"actualDate": "2026-09-02T02:10:00+02:00", "times": {}}},
                "4_16", datetime(2026, 9, 2, 0, 10, tzinfo=UTC), 2)
    assert row["arrivals"] == [] and row["n_arrivals"] == 0


def test_service_day_is_cut_at_three_not_midnight():
    """Metro runs to about 01:30, so a train at 00:40 belongs to the night before."""
    assert service_date_for(datetime(2026, 9, 1, 21, 0, tzinfo=UTC)) == "2026-09-01"
    assert service_date_for(datetime(2026, 9, 2, 0, 10, tzinfo=UTC)) == "2026-09-01"
    assert service_date_for(datetime(2026, 9, 2, 5, 0, tzinfo=UTC)) == "2026-09-02"


def test_single_element_collections_collapse_to_objects():
    """The payload is XML rendered as JSON, so a one-item list is sent as the item.

    Found by polling real stations: a station with one line raised AttributeError on
    linesStatus, and a station with one arrival would have done the same on times.
    """
    payload = {"stopTimes": {
        "actualDate": "2026-09-02T07:35:49+02:00",
        "times": {"Time": {"line": {"shortDescription": "6"}, "direction": 1,
                           "destination": "LAGUNA",
                           "destinationStop": {"codStop": "4_105"},
                           "time": "2026-09-02T07:37:11+02:00"}},
        "linesStatus": {"LineStatus": {"line": {"shortDescription": "6"},
                                       "SAEStatus": True}}}}
    row = parse(payload, "4_18", datetime(2026, 9, 2, 5, 35, 30, tzinfo=UTC), 1)
    assert row["n_arrivals"] == 1
    assert row["arrivals"][0]["line_id"] == "6"
    assert row["sae_status"] == [{"line_id": "6", "ok": True}]

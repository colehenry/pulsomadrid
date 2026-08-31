"""Tests for the Madrid filter and the enrichment, over a real slice of the feed.

The fixture in tests/fixtures/ was cut from a live response on 2026-08-31T05:46Z: three Madrid trains with positions, two Sevilla trains
(the feed is national), and one Madrid train the feed sent with no position at all.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.realtime import VehicleFeed, parse_feed
from app.warehouse import Snapshot, Trip

SAMPLE = Path(__file__).parent / "fixtures/vehicle_positions.sample.json"


@pytest.fixture
def feed_payload() -> dict:
    return json.loads(SAMPLE.read_text())


@pytest.fixture
def snapshot() -> Snapshot:
    """Only the Madrid trips, as the warehouse would give them."""
    return Snapshot(
        stations=[],
        lines=[],
        trips={
            "1041L19561C5": Trip("19561", "C5", "Humanes", None, 22),
            "1041L21806C7": Trip("21806", "C7", "Alcalá de Henares", None, 18),
            "1041L21008C8a": Trip("21008", "C8a", "Chamartín", None, 14),
            "1041L20727C5": Trip("20727", "C5", "Móstoles-El Soto", None, 22),
        },
        station_names={"18000": "Atocha", "70002": "Vicálvaro"},
    )


def test_keeps_only_trips_we_hold(feed_payload: dict, snapshot: Snapshot) -> None:
    """The Madrid filter is the trip_id join. The two Sevilla C1 trains must not survive."""
    result = parse_feed(feed_payload, snapshot)
    assert [v.train_number for v in result.vehicles] == ["19561", "21806", "21008"]
    assert "23515" not in {v.train_number for v in result.vehicles}


def test_drops_a_vehicle_with_no_position(feed_payload: dict, snapshot: Snapshot) -> None:
    """Trip 1041L20727C5 is ours, but the feed sent no position — it cannot be drawn."""
    assert any(e["vehicle"]["trip"]["tripId"] == "1041L20727C5" for e in feed_payload["entity"])
    result = parse_feed(feed_payload, snapshot)
    assert "20727" not in {v.train_number for v in result.vehicles}


def test_observed_at_comes_from_the_feed_header(feed_payload: dict, snapshot: Snapshot) -> None:
    result = parse_feed(feed_payload, snapshot)
    assert result.observed_at == datetime(2026, 8, 31, 5, 46, 51, tzinfo=UTC)


def test_line_and_destination_come_from_the_warehouse(feed_payload: dict,
                                                      snapshot: Snapshot) -> None:
    """The feed carries no destination and its label is free text, so both are ours."""
    first = parse_feed(feed_payload, snapshot).vehicles[0]
    assert (first.line_id, first.destination, first.calls_at) == ("C5", "Humanes", 22)
    assert first.status == "STOPPED_AT"
    assert (first.lat, first.lon) == (40.405067, -3.7026901)


def test_at_station_resolves_the_feed_stop_id(feed_payload: dict, snapshot: Snapshot) -> None:
    """Known stop_id becomes a display name; an unknown one stays null rather than raw."""
    vehicles = {v.train_number: v for v in parse_feed(feed_payload, snapshot).vehicles}
    assert vehicles["19561"].at_station == "Atocha"
    assert vehicles["21008"].at_station is None  # stop 10203 is not in station_names here


def test_empty_feed_is_not_an_error(snapshot: Snapshot) -> None:
    result = parse_feed({"header": {"timestamp": "1788155211"}, "entity": []}, snapshot)
    assert result.vehicles == []


async def test_upstream_failure_replays_the_last_good_snapshot(feed_payload: dict,
                                                               snapshot: Snapshot) -> None:
    """An upstream outage must degrade the map to stale data, not break the page."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=feed_payload)
        return httpx.Response(503)

    feed = VehicleFeed("https://example.invalid/vehicle_positions.json", cache_seconds=0)
    feed._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    good = await feed.current(snapshot)
    assert good is not None and len(good.vehicles) == 3
    assert feed.upstream_ok

    after_outage = await feed.current(snapshot)
    assert after_outage is not None
    assert [v.train_number for v in after_outage.vehicles] == ["19561", "21806", "21008"]
    assert not feed.upstream_ok
    assert after_outage.observed_at == good.observed_at
    await feed.close()


async def test_concurrent_cold_requests_make_one_upstream_fetch(feed_payload: dict,
                                                                snapshot: Snapshot) -> None:
    """The cache alone does not stop a stampede — only requests arriving after a fetch
    has finished can hit it. Ten arriving during one must still produce a single fetch:
    Renfe is a public feed we do not own, and Cloud Run puts up to 80 concurrent requests
    on one instance."""
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        await asyncio.sleep(0.05)  # hold it open so the other nine pile up behind it
        return httpx.Response(200, json=feed_payload)

    feed = VehicleFeed("https://example.invalid/vehicle_positions.json", cache_seconds=10)
    feed._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    results = await asyncio.gather(*(feed.current(snapshot) for _ in range(10)))

    assert calls["n"] == 1
    assert all(r is not None and len(r.vehicles) == 3 for r in results)
    await feed.close()


async def test_no_snapshot_at_all_returns_none(snapshot: Snapshot) -> None:
    feed = VehicleFeed("https://example.invalid/vehicle_positions.json")
    feed._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert await feed.current(snapshot) is None
    await feed.close()

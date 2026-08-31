"""Tests for the parts of the transform that encode a decision we could get wrong."""
from __future__ import annotations

import duckdb
import pytest

from pulso_gtfs.transform import _SECS, _ts


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("INSTALL icu; LOAD icu;")
    c.execute("SET TimeZone = 'UTC'")
    return c


def secs(con, value: str) -> int:
    return con.execute(f"SELECT {_SECS.format(c=repr(value))}").fetchone()[0]


def test_times_past_midnight_are_not_wrapped(con):
    """GTFS allows hours past 24. 24:05 is 05 past midnight the NEXT day, not 00:05 today."""
    assert secs(con, "00:05:00") == 300
    assert secs(con, "24:05:00") == 86_700
    assert secs(con, "25:30:00") == 91_800


def test_timestamp_lands_on_the_following_day(con):
    """A 24:05 call on 25 Aug is 00:05 on 26 Aug Madrid local, i.e. 22:05Z on 25 Aug."""
    # Cast in SQL: handing a TIMESTAMPTZ back to Python would need pytz, and the
    # pipeline never does that - it writes Parquet straight from DuckDB.
    got = con.execute(
        f"SELECT CAST({_ts('86700')} AS VARCHAR) FROM (SELECT DATE '2026-08-25' AS service_date)"
    ).fetchone()[0]
    assert got.startswith("2026-08-25 22:05:00+00")  # 00:05+02:00 in Madrid, next day


def test_timestamp_respects_summer_and_winter_offset(con):
    """Madrid is UTC+2 in August and UTC+1 in December. A fixed offset would fail this."""
    august = con.execute(
        f"SELECT CAST({_ts('28800')} AS VARCHAR) FROM (SELECT DATE '2026-08-25' AS service_date)"
    ).fetchone()[0]
    december = con.execute(
        f"SELECT CAST({_ts('28800')} AS VARCHAR) FROM (SELECT DATE '2026-12-25' AS service_date)"
    ).fetchone()[0]
    assert august.startswith("2026-08-25 06:00:00+00")     # 08:00 Madrid, UTC+2
    assert december.startswith("2026-12-25 07:00:00+00")   # 08:00 Madrid, UTC+1


def test_day_offset_is_zero_or_one(con):
    assert con.execute("SELECT CAST(86_700 / 86400 AS BIGINT)").fetchone()[0] == 1
    assert con.execute("SELECT CAST(300 / 86400 AS BIGINT)").fetchone()[0] == 0


def test_pattern_id_is_stable_and_line_scoped(con):
    """Same line and stops -> same id. Different line, same stops -> different id."""
    q = "SELECT substr(sha256(? || ':' || ?), 1, 12)"
    a = con.execute(q, ["C5", "A>B>C"]).fetchone()[0]
    b = con.execute(q, ["C5", "A>B>C"]).fetchone()[0]
    c = con.execute(q, ["C1", "A>B>C"]).fetchone()[0]
    d = con.execute(q, ["C5", "A>C"]).fetchone()[0]
    assert a == b
    assert a != c, "patterns must not be shared between lines"
    assert a != d


def test_madrid_filter_is_prefix_not_line_name(con):
    """'C1' exists in eleven Spanish networks; only the nucleo prefix isolates Madrid."""
    con.execute("""CREATE TABLE r AS SELECT * FROM (VALUES
        ('10T0001C1  ', 'C1  '), ('51T0001R1  ', 'R1  '), ('20T0001C1  ', 'C1  ')
    ) AS t(route_id, route_short_name)""")
    by_prefix = con.execute("SELECT COUNT(*) FROM r WHERE trim(route_id) LIKE '10T%'").fetchone()[0]
    by_line = con.execute("SELECT COUNT(*) FROM r WHERE trim(route_short_name) = 'C1'").fetchone()[0]
    assert by_prefix == 1
    assert by_line == 2, "filtering on line name alone would pull in Valencia"

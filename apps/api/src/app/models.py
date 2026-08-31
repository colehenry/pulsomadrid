"""Response shapes.

Every field carries a description, for the same reason every warehouse column does:
FastAPI publishes them at /docs, so the contract is readable without reading the code.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

Coordinate = tuple[float, float]  # (lon, lat) — GeoJSON order, which is what MapLibre wants


class Station(BaseModel):
    id: str = Field(description="Renfe stop_id, e.g. '18000'. The station key everywhere in Pulso")
    name: str = Field(description="Name to show a user, e.g. 'Atocha'")
    lat: float = Field(description="Latitude, WGS84 decimal degrees")
    lon: float = Field(description="Longitude, WGS84 decimal degrees")
    lines: list[str] = Field(description="Lines that call here, e.g. ['C1','C5']")


class Line(BaseModel):
    id: str = Field(description="Line name as used publicly, e.g. 'C5'")
    name: str | None = Field(description="Origin and destination as text. Describes the corridor, "
                                         "not any individual train")
    color: str | None = Field(description="Line colour, 6-digit hex with no '#'")
    shapes: list[list[Coordinate]] = Field(
        description="Route geometry: one [lon,lat] path per Renfe shape, normally two per line "
                    "(one per direction). Track, not stopping pattern")


class NetworkResponse(BaseModel):
    stations: list[Station]
    lines: list[Line]
    loaded_at: datetime = Field(description="When this snapshot was read from BigQuery")


class Vehicle(BaseModel):
    train_number: str = Field(description="Renfe's train number, e.g. '19810'. Shown on platform "
                                          "displays. Identifies a scheduled journey, not a "
                                          "physical train")
    line_id: str = Field(description="Line, e.g. 'C1'. From our schedule, not from the feed")
    lat: float = Field(description="Latitude reported by the feed, WGS84")
    lon: float = Field(description="Longitude reported by the feed, WGS84")
    status: str = Field(description="GTFS-RT currentStatus: 'STOPPED_AT', 'INCOMING_AT' or "
                                    "'IN_TRANSIT_TO'")
    at_station: str | None = Field(
        description="Display name of the feed's stop_id. That is the station the train is "
                    "standing at when status is STOPPED_AT, and the station it is heading for "
                    "otherwise. NULL where the feed names a stop_id outside the Madrid network")
    destination: str | None = Field(
        description="Last station of this trip's stopping pattern. Renfe leaves trip_headsign "
                    "empty on 98.7% of Madrid trips, so this is what to display")
    towards: str | None = Field(
        description="Terminus this train's direction heads towards — what a platform sign "
                    "means by 'towards Humanes'. Differs from destination for a short-turn "
                    "service: a C5 terminating at Fuenlabrada is still heading towards Humanes")
    calls_at: int = Field(description="Number of stations this train calls at over the whole trip")


class VehiclesResponse(BaseModel):
    observed_at: datetime | None = Field(
        description="Feed header timestamp of this snapshot, UTC. NULL only if the upstream feed "
                    "has not been reached successfully since this process started")
    vehicles: list[Vehicle]
    upstream_ok: bool = Field(
        description="False when the last fetch failed and these vehicles are the previous "
                    "snapshot replayed. Compare observed_at with now to judge how stale it is")

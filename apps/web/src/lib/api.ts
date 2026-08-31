/**
 * Client for apps/api. The shapes here mirror the Pydantic models it publishes at
 * http://localhost:8000/docs — keep the two in step.
 */
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** [lon, lat], GeoJSON order. */
export type Coordinate = [number, number];

export type Station = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  lines: string[];
};

export type Line = {
  id: string;
  name: string | null;
  /** 6-digit hex, no leading '#'. */
  color: string | null;
  /** One path per Renfe shape: the track, not the stopping pattern. */
  shapes: Coordinate[][];
};

export type Network = {
  stations: Station[];
  lines: Line[];
  loaded_at: string;
};

export type VehicleStatus = "STOPPED_AT" | "INCOMING_AT" | "IN_TRANSIT_TO" | "UNKNOWN";

export type Vehicle = {
  train_number: string;
  line_id: string;
  lat: number;
  lon: number;
  status: VehicleStatus;
  /** Where it is standing, or where it is heading, depending on status. */
  at_station: string | null;
  destination: string | null;
  towards: string | null;
  calls_at: number;
};

export type Vehicles = {
  /** Feed header timestamp. Null only if the API has never reached Renfe. */
  observed_at: string | null;
  vehicles: Vehicle[];
  /** False when these are the previous vehicles replayed after a failed fetch. */
  upstream_ok: boolean;
};

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { signal });
  if (!response.ok) {
    throw new Error(`${path} responded ${response.status}`);
  }
  return (await response.json()) as T;
}

export const fetchNetwork = (signal?: AbortSignal) => get<Network>("/api/network", signal);
export const fetchVehicles = (signal?: AbortSignal) => get<Vehicles>("/api/vehicles", signal);

/** '#F9BA13' from 'F9BA13'. Grey where the warehouse has no colour for the line. */
export function lineColor(hex: string | null | undefined): string {
  return hex ? `#${hex}` : "#8b8b8b";
}

import type { FeatureCollection, LineString, Point } from "geojson";
import { lineColor, type Network, type Vehicle } from "./api";

/** Route geometry, one feature per Renfe shape. Drawn first, under everything. */
export function linesToGeoJSON(network: Network): FeatureCollection<LineString> {
  return {
    type: "FeatureCollection",
    features: network.lines.flatMap((line) =>
      line.shapes.map((coordinates, index) => ({
        type: "Feature" as const,
        id: `${line.id}-${index}`,
        properties: { line: line.id, color: lineColor(line.color) },
        geometry: { type: "LineString" as const, coordinates },
      })),
    ),
  };
}

export function stationsToGeoJSON(network: Network): FeatureCollection<Point> {
  return {
    type: "FeatureCollection",
    features: network.stations.map((station) => ({
      type: "Feature" as const,
      id: station.id,
      properties: { name: station.name, lines: station.lines.join(" · ") },
      geometry: { type: "Point" as const, coordinates: [station.lon, station.lat] },
    })),
  };
}

export function vehiclesToGeoJSON(
  vehicles: Vehicle[],
  colors: Map<string, string>,
): FeatureCollection<Point> {
  return {
    type: "FeatureCollection",
    features: vehicles.map((vehicle) => ({
      type: "Feature" as const,
      id: vehicle.train_number,
      properties: {
        train_number: vehicle.train_number,
        line: vehicle.line_id,
        color: colors.get(vehicle.line_id) ?? "#8b8b8b",
      },
      geometry: { type: "Point" as const, coordinates: [vehicle.lon, vehicle.lat] },
    })),
  };
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Map as MapLibreMap,
  NavigationControl,
  setWorkerUrl,
  type GeoJSONSource,
  type MapGeoJSONFeature,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useTranslations } from "next-intl";

import Legend from "./Legend";
import LocaleSwitcher from "./LocaleSwitcher";
import StatusPill from "./StatusPill";
import VehicleCard from "./VehicleCard";
import { fetchNetwork, fetchVehicles, lineColor, type Network, type Vehicle } from "@/lib/api";
import { linesToGeoJSON, stationsToGeoJSON, vehiclesToGeoJSON } from "@/lib/geo";

// OpenFreeMap: free vector tiles, no account, no key, no usage cliff. Same reasoning
// that chose MapLibre over Mapbox.
const BASEMAP = "https://tiles.openfreemap.org/styles/liberty";

// MapLibre resolves its tile worker as `new URL("./maplibre-gl-worker.mjs", import.meta.url)`,
// which under Turbopack points at /_next/static/chunks/… where no such file is emitted.
// Next then serves its HTML 404 page, the module worker is rejected on MIME type, and the
// map renders as a blank canvas with no request to the basemap at all. So serve the worker
// ourselves: package.json's predev/prebuild copy it, with its sibling shared chunk, into
// public/maplibre.
setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");
const MADRID: [number, number] = [-3.703, 40.417];

const POLL_MS = 30_000;
const TICK_MS = 5_000; // how often the age of the data is recomputed for display

const EMPTY = { type: "FeatureCollection" as const, features: [] };

export default function MapView() {
  const t = useTranslations("status");
  const container = useRef<HTMLDivElement>(null);
  // The map lives in state, not a ref. React StrictMode mounts this effect twice in dev,
  // so the first instance is created and thrown away; holding it in a ref meant the
  // effects that push data into the map kept writing to the discarded one and never ran
  // again for the live map, which is why every layer stayed empty.
  const [mapInstance, setMapInstance] = useState<MapLibreMap | null>(null);

  const [network, setNetwork] = useState<Network | null>(null);
  const [networkError, setNetworkError] = useState(false);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [observedAt, setObservedAt] = useState<string | null>(null);
  const [upstreamOk, setUpstreamOk] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // ---- the network: fetched once, it changes at most daily -------------------------
  useEffect(() => {
    const abort = new AbortController();
    fetchNetwork(abort.signal)
      .then(setNetwork)
      .catch((error: unknown) => {
        if (!abort.signal.aborted) {
          console.error("network", error);
          setNetworkError(true);
        }
      });
    return () => abort.abort();
  }, []);

  // ---- live vehicles: polled ------------------------------------------------------
  const poll = useCallback(async () => {
    try {
      const data = await fetchVehicles();
      setVehicles(data.vehicles);
      setObservedAt(data.observed_at);
      setUpstreamOk(data.upstream_ok);
    } catch (error) {
      // The API already falls back to its last good snapshot, so a failure here means
      // the API itself is unreachable. Keep the last vehicles and let the age show.
      console.error("vehicles", error);
      setUpstreamOk(false);
    }
  }, []);

  useEffect(() => {
    // Subscribing to an external feed, not deriving state: the first read has to happen
    // here, and it resolves asynchronously rather than during this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void poll();
    const id = setInterval(() => {
      // A hidden tab should not poll a third-party feed every 30 seconds.
      if (document.visibilityState === "visible") void poll();
    }, POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [poll]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(id);
  }, []);

  // ---- the map --------------------------------------------------------------------
  useEffect(() => {
    const element = container.current;
    if (!element) return;

    const instance = new MapLibreMap({
      container: element,
      style: BASEMAP,
      center: MADRID,
      zoom: 9,
      attributionControl: { compact: true },
    });
    instance.addControl(new NavigationControl({ showCompass: false }), "bottom-right");

    // MapLibre reports a failed style, a missing worker or a dead tile endpoint through
    // this event rather than by throwing, so without it the map just goes blank.
    instance.on("error", (event) => {
      console.error("maplibre", event.error ?? event);
    });

    // "style.load", not "load": `load` waits for the first complete render, which never
    // happens if the tab is hidden while the map starts up — and then none of our sources
    // or layers are ever added. "style.load" fires as soon as the style is parsed, which
    // is all that is needed before addSource/addLayer.
    instance.on("style.load", () => {
      // Build order matters: geometry first, then stations, then vehicles on top.
      instance.addSource("lines", { type: "geojson", data: EMPTY });
      instance.addSource("stations", { type: "geojson", data: EMPTY });
      instance.addSource("vehicles", { type: "geojson", data: EMPTY });

      instance.addLayer({
        id: "lines",
        type: "line",
        source: "lines",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["get", "color"],
          "line-opacity": 0.85,
          "line-width": ["interpolate", ["linear"], ["zoom"], 7, 1.5, 11, 3, 15, 5],
        },
      });
      instance.addLayer({
        id: "stations",
        type: "circle",
        source: "stations",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 2.5, 12, 4.5, 15, 6],
          "circle-color": "#ffffff",
          "circle-stroke-color": "#22252b",
          "circle-stroke-width": 1.5,
        },
      });
      instance.addLayer({
        id: "station-labels",
        type: "symbol",
        source: "stations",
        minzoom: 11,
        layout: {
          "text-field": ["get", "name"],
          "text-size": 11,
          "text-offset": [0, 1.1],
          "text-anchor": "top",
        },
        paint: {
          "text-color": "#22252b",
          "text-halo-color": "#ffffff",
          "text-halo-width": 1.5,
        },
      });
      instance.addLayer({
        id: "vehicles",
        type: "circle",
        source: "vehicles",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 4, 12, 7, 15, 9],
          "circle-color": ["get", "color"],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
        },
      });

      instance.on("click", "vehicles", (event) => {
        const feature = event.features?.[0] as MapGeoJSONFeature | undefined;
        const number = feature?.properties?.train_number as string | undefined;
        if (number) setSelected(number);
      });
      instance.on("click", (event) => {
        const hits = instance.queryRenderedFeatures(event.point, { layers: ["vehicles"] });
        if (hits.length === 0) setSelected(null);
      });
      instance.on("mouseenter", "vehicles", () => {
        instance.getCanvas().style.cursor = "pointer";
      });
      instance.on("mouseleave", "vehicles", () => {
        instance.getCanvas().style.cursor = "";
      });

      // The container can still be unsized when the map is constructed — in dev the
      // stylesheet arrives after hydration — and MapLibre then falls back to its 400x300
      // default and keeps it. Measure again once, and keep watching.
      instance.resize();

      setMapInstance(instance);
    });

    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(element);

    return () => {
      observer.disconnect();
      instance.remove();
      setMapInstance(null);
    };
  }, []);

  // ---- feed the sources -----------------------------------------------------------
  useEffect(() => {
    if (!mapInstance || !network) return;
    (mapInstance.getSource("lines") as GeoJSONSource | undefined)?.setData(
      linesToGeoJSON(network),
    );
    (mapInstance.getSource("stations") as GeoJSONSource | undefined)?.setData(
      stationsToGeoJSON(network),
    );
  }, [mapInstance, network]);

  const colors = useMemo(
    () => new Map((network?.lines ?? []).map((line) => [line.id, lineColor(line.color)])),
    [network],
  );

  useEffect(() => {
    if (!mapInstance) return;
    (mapInstance.getSource("vehicles") as GeoJSONSource | undefined)?.setData(
      vehiclesToGeoJSON(vehicles, colors),
    );
  }, [mapInstance, vehicles, colors]);

  // ---- derived for the chrome -----------------------------------------------------
  const perLine = useMemo(() => {
    const counts = new Map<string, number>();
    for (const vehicle of vehicles) {
      counts.set(vehicle.line_id, (counts.get(vehicle.line_id) ?? 0) + 1);
    }
    return counts;
  }, [vehicles]);

  const selectedVehicle = vehicles.find((vehicle) => vehicle.train_number === selected);
  const ageSeconds = observedAt ? Math.round((now - Date.parse(observedAt)) / 1000) : null;

  return (
    <div className="map">
      <div ref={container} className="map__canvas" />

      <header className="panel panel--head">
        <div>
          <h1 className="panel__title">Pulso Madrid</h1>
          <StatusPill
            count={vehicles.length}
            observedAt={observedAt}
            upstreamOk={upstreamOk}
            ageSeconds={ageSeconds}
          />
          {networkError && <p className="status status--warn">{t("networkError")}</p>}
        </div>
        <LocaleSwitcher />
      </header>

      {network && <Legend lines={network.lines} counts={perLine} />}

      {selectedVehicle && (
        <VehicleCard
          vehicle={selectedVehicle}
          line={network?.lines.find((line) => line.id === selectedVehicle.line_id)}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

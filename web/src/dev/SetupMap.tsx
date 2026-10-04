import { useEffect, useRef } from "react";
import {
  GeoJSONSource,
  LngLat,
  LngLatBounds,
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  type MapMouseEvent,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { AnalysisArea } from "../api/client";
import MagicMapPreview, { type MagicPreview } from "./MagicMapPreview";

type Props = {
  centerLat: number;
  centerLon: number;
  area: AnalysisArea | null;
  disabled: boolean;
  onSelect: (lon: number, lat: number) => void;
  magicPreview?: MagicPreview | null;
  magicFocusRequest?: number;
  onMagicBearingChange?: (bearing: string) => void;
  onViewportChange?: (lon: number, lat: number) => void;
};

function areaFeature(area: AnalysisArea | null) {
  if (!area) {
    return {
      type: "FeatureCollection" as const,
      features: [],
    };
  }

  const bounds = area.bounds;
  return {
    type: "Feature" as const,
    properties: {},
    geometry: {
      type: "Polygon" as const,
      coordinates: [[
        [bounds.west_deg, bounds.south_deg],
        [bounds.east_deg, bounds.south_deg],
        [bounds.east_deg, bounds.north_deg],
        [bounds.west_deg, bounds.north_deg],
        [bounds.west_deg, bounds.south_deg],
      ]],
    },
  };
}

function installAnalysisAreaLayers(map: MapLibreMap, area: AnalysisArea | null) {
  const data = areaFeature(area);
  let source = map.getSource("analysis-area") as GeoJSONSource | undefined;
  if (!source) {
    map.addSource("analysis-area", { type: "geojson", data });
    source = map.getSource("analysis-area") as GeoJSONSource;
  }
  source.setData(data);

  if (!map.getLayer("analysis-area-fill")) {
    map.addLayer({
      id: "analysis-area-fill",
      type: "fill",
      source: "analysis-area",
      paint: { "fill-color": "#2563eb", "fill-opacity": 0.12 },
    });
  }
  if (!map.getLayer("analysis-area-outline-shadow")) {
    map.addLayer({
      id: "analysis-area-outline-shadow",
      type: "line",
      source: "analysis-area",
      layout: { "line-cap": "round", "line-join": "round", visibility: "visible" },
      paint: { "line-color": "#111827", "line-opacity": 1, "line-width": 9 },
    });
  }
  if (!map.getLayer("analysis-area-outline")) {
    map.addLayer({
      id: "analysis-area-outline",
      type: "line",
      source: "analysis-area",
      layout: { "line-cap": "round", "line-join": "round", visibility: "visible" },
      paint: { "line-color": "#ef1b1b", "line-opacity": 1, "line-width": 5 },
    });
  }
  map.moveLayer("analysis-area-outline-shadow");
  map.moveLayer("analysis-area-outline");
}

function renderAnalysisAreaOverlay(
  map: MapLibreMap,
  svg: SVGSVGElement,
  outline: HTMLDivElement,
  area: AnalysisArea | null,
) {
  svg.replaceChildren();
  outline.hidden = true;
  const width = Math.max(1, svg.clientWidth);
  const height = Math.max(1, svg.clientHeight);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  if (!area) return;
  const bounds = area.bounds;
  const corners = [
    [bounds.west_deg, bounds.south_deg],
    [bounds.east_deg, bounds.south_deg],
    [bounds.east_deg, bounds.north_deg],
    [bounds.west_deg, bounds.north_deg],
  ] as const;
  const projected = corners.map(([lon, lat]) => map.project([lon, lat]));
  const xs = projected.map((point) => point.x);
  const ys = projected.map((point) => point.y);
  const left = Math.min(...xs);
  const top = Math.min(...ys);
  outline.style.left = `${left}px`;
  outline.style.top = `${top}px`;
  outline.style.width = `${Math.max(1, Math.max(...xs) - left)}px`;
  outline.style.height = `${Math.max(1, Math.max(...ys) - top)}px`;
  outline.hidden = false;
  const pathData = projected
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(" ") + " Z";
  const namespace = "http://www.w3.org/2000/svg";
  for (const [stroke, strokeWidth] of [["#111827", "10"], ["#ef1b1b", "5"]] as const) {
    const path = document.createElementNS(namespace, "path");
    path.setAttribute("d", pathData);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", stroke);
    path.setAttribute("stroke-width", strokeWidth);
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("vector-effect", "non-scaling-stroke");
    svg.appendChild(path);
  }
}

export default function SetupMap({
  centerLat,
  centerLon,
  area,
  disabled,
  onSelect,
  magicPreview,
  magicFocusRequest,
  onMagicBearingChange,
  onViewportChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const markerRef = useRef<Marker | null>(null);
  const areaOverlayRef = useRef<SVGSVGElement | null>(null);
  const areaOutlineRef = useRef<HTMLDivElement | null>(null);
  const onSelectRef = useRef(onSelect);
  const disabledRef = useRef(disabled);
  const areaRef = useRef(area);
  const initialCenterRef = useRef<[number, number]>([centerLon, centerLat]);
  const onViewportChangeRef = useRef(onViewportChange);
  onViewportChangeRef.current = onViewportChange;

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    areaRef.current = area;
  }, [area]);

  useEffect(() => {
    disabledRef.current = disabled;
    const map = mapRef.current;
    if (map) {
      map.getCanvas().style.cursor = disabled ? "not-allowed" : "crosshair";
    }
  }, [disabled]);

  useEffect(() => {
    if (!containerRef.current) return;

    const map = new MapLibreMap({
      container: containerRef.current,
      center: initialCenterRef.current,
      zoom: 14,
      maxZoom: magicFocusRequest === undefined ? 22 : 24,
      style: {
        version: 8,
        sources: {
          gsi: {
            type: "raster",
            tiles: ["https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png"],
            tileSize: 256,
            maxzoom: 18,
            attribution: "国土地理院",
          },
          "analysis-area": {
            type: "geojson",
            data: {
              type: "FeatureCollection",
              features: [],
            },
          },
        },
        layers: [{ id: "gsi", type: "raster", source: "gsi" }],
      },
    });

    mapRef.current = map;
    map.addControl(new NavigationControl({ showCompass: false }), "top-right");
    map.getCanvas().style.cursor = disabledRef.current ? "not-allowed" : "crosshair";
    map.once("load", () => installAnalysisAreaLayers(map, areaRef.current));

    markerRef.current = new Marker({ color: "#111827" })
      .setLngLat(initialCenterRef.current)
      .addTo(map);

    const handleClick = (event: MapMouseEvent) => {
      if (disabledRef.current) return;
      onSelectRef.current(event.lngLat.lng, event.lngLat.lat);
    };
    map.on("click", handleClick);
    const reportCenter = () => {
      if (!onViewportChangeRef.current) return;
      const center = map.getCenter();
      onViewportChangeRef.current(center.lng, center.lat);
    };
    map.on("moveend", reportCenter);
    reportCenter();

    return () => {
      map.off("click", handleClick);
      map.off("moveend", reportCenter);
      markerRef.current?.remove();
      markerRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    markerRef.current?.setLngLat([centerLon, centerLat]);

    // Viewport movement does not depend on style/source readiness.
    // Do it immediately so geocoder selection can never get stuck waiting for
    // a load event that may already have fired.
    map.stop();
    map.resize();
    if (area) {
      map.fitBounds(
        [
          [area.bounds.west_deg, area.bounds.south_deg],
          [area.bounds.east_deg, area.bounds.north_deg],
        ],
        { padding: 40, maxZoom: 17, duration: 350 },
      );
    } else {
      map.easeTo({ center: [centerLon, centerLat], zoom: 14, duration: 350 });
    }

    const updateArea = () => installAnalysisAreaLayers(map, area);
    if (map.isStyleLoaded()) {
      updateArea();
    } else {
      map.once("load", updateArea);
      return () => {
        map.off("load", updateArea);
      };
    }
  }, [area, centerLat, centerLon]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !magicPreview || !magicFocusRequest) return;
    let bounds: [[number, number], [number, number]];
    if (magicPreview.footprintKind === "domain") {
      if (!area) return;
      bounds = [[area.bounds.west_deg, area.bounds.south_deg], [area.bounds.east_deg, area.bounds.north_deg]];
    } else {
      // An anchor-centered envelope covers every bearing without moving the
      // placement center when selection is repeated after the automatic zoom.
      const radius = magicPreview.footprintKind === "rectangle"
        ? Math.hypot(Number(magicPreview.length), Number(magicPreview.width) / 2)
        : Number(magicPreview.radius);
      if (!Number.isFinite(radius) || radius <= 0) return;
      bounds = LngLatBounds.fromLngLat(new LngLat(magicPreview.lon, magicPreview.lat), radius).toArray();
    }
    map.stop();
    map.resize();
    map.fitBounds(bounds, { padding: 72, maxZoom: 24, duration: 450 });
    // Only an explicit list click requests focus; edits and manual navigation
    // must not reset the user's viewport.
  }, [magicFocusRequest]);

  useEffect(() => {
    const map = mapRef.current;
    const svg = areaOverlayRef.current;
    const outline = areaOutlineRef.current;
    if (!map || !svg || !outline) return;
    const update = () => renderAnalysisAreaOverlay(map, svg, outline, area);
    const delayedUpdate = window.setTimeout(update, 450);
    map.on("move", update);
    map.on("moveend", update);
    map.on("resize", update);
    if (map.isStyleLoaded()) {
      update();
      window.requestAnimationFrame(update);
    } else map.once("load", update);
    return () => {
      window.clearTimeout(delayedUpdate);
      map.off("move", update);
      map.off("moveend", update);
      map.off("resize", update);
      map.off("load", update);
      svg.replaceChildren();
    };
  }, [area]);

  return (
    <div className="setup-map-wrap">
      <p className="setup-map-help">
        地図をクリックして解析中心を選択できます。四角い枠が解析範囲です。
      </p>
      <div className="setup-map-frame">
        <div
          ref={containerRef}
          className="setup-map-canvas"
          role="region"
          aria-label="解析場所と範囲を選択する地図"
          aria-disabled={disabled}
        />
        <svg
          ref={areaOverlayRef}
          className="setup-area-overlay"
          aria-hidden="true"
          data-analysis-area-overlay="true"
        />
        <div
          ref={areaOutlineRef}
          className="setup-area-dom-outline"
          aria-hidden="true"
          data-analysis-area-dom-outline="true"
        />
        <MagicMapPreview mapRef={mapRef} magic={magicPreview ?? null} analysisArea={area} disabled={disabled} onBearingChange={onMagicBearingChange} />
      </div>
      {disabled && <p className="setup-map-disabled">解析中は場所と範囲を変更できません。</p>}
    </div>
  );
}

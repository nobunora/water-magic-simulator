import { useEffect, useRef, useState } from "react";
import {
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  setWorkerUrl,
  type ImageSource,
  type MapMouseEvent,
  type StyleSpecification,
} from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import "maplibre-gl/dist/maplibre-gl.css";

import type {
  FlowVectorFeatureCollection,
  FlowViewport,
  ResultMetadataResponse,
} from "../api/client";
import { resultBounds, resultImageCoordinates } from "./resultGeometry";
import MagicResultFootprint, { type MagicGeometry } from "./MagicResultFootprint";
import { projectNativeFlowField } from "./nativeFlowField";
import { createParticleRenderer } from "./particleRenderer";

setWorkerUrl(maplibreWorkerUrl);

export type FlowRenderStats = {
  sourceFeatureCount: number;
  renderedFeatureCount: number;
  svgArrowCount: number;
  canvasArrowCount?: number;
  layerOrder: string[];
  featureBounds: [number, number, number, number] | null;
  mapBounds: [number, number, number, number];
};

type Props = {
  magicGeometry?: MagicGeometry | null;
  metadata: Pick<ResultMetadataResponse, "bounds">;
  imageUrl: string;
  flowVectorData: FlowVectorFeatureCollection | null;
  nextFlowVectorData?: FlowVectorFeatureCollection | null;
  flowInterpolation?: { current: number };
  flowSpeedRange?: readonly [number, number];
  flowSpeedBreaks?: readonly number[];
  flowDisplayMode?: "vectors" | "particles" | null;
  backgroundOpacity: number;
  mapLabel: string;
  focusPoint?: { lon: number; lat: number } | null;
  onInspect?: (lon: number, lat: number) => void;
  onFlowRenderStats?: (stats: FlowRenderStats | null) => void;
  onViewportChange?: (viewport: FlowViewport, zoom: number) => void;
  onCaptureReady?: (capture: ((expectedImageUrl?: string) => Promise<HTMLCanvasElement>) | null) => void;
};

const EMPTY_FLOW = {
  type: "FeatureCollection" as const,
  features: [],
};

const FLOW_SOURCE_ID = "flow-vector-source";
const FLOW_HALO_LAYER_ID = "flow-vector-halo";
const FLOW_LINE_LAYER_ID = "flow-vector-lines";
const FLOW_ARROW_LENGTH_PX = 18;
const PARTICLE_TRAIL_LENGTH_PX = 75;
const PARTICLE_MIN_VECTOR_CROSSINGS = 15;
const PARTICLE_TRAIL_SAMPLE_PX = 3;
const PARTICLE_MAX_NEIGHBORS = 8;
const PARTICLES_PER_VECTOR = 2;
const PARTICLE_PHASE_GROUPS = 4;
const PARTICLE_SPEED_SCALE_PX_PER_METER = 36;
const PARTICLE_MIN_SPEED_PX_PER_SECOND = 1.5;
const PARTICLE_MAX_SPEED_PX_PER_SECOND = 96;
const GSI_SOURCE_MAX_ZOOM = 18;
const RESULT_MAX_ZOOM = 21;

type ScreenPoint = { x: number; y: number };

type ProjectedFlowNode = ScreenPoint & {
  dx: number;
  dy: number;
  speedMps: number;
  speedPxPerSecond: number;
};

type ProjectedFlowField = {
  nodes: ProjectedFlowNode[];
  buckets: Map<string, number[]>;
  spacingPx: number;
  cellSizePx: number;
};

type FlowParticle = ScreenPoint & {
  sourceIndex: number;
  copyIndex: number;
  travelledPx: number;
  targetDistancePx: number;
  trail: ScreenPoint[];
  generation: number;
};

function flowColor(speedMps: number, range: readonly [number, number], breaks?: readonly number[]): string {
  const colors = ["#2DC4B2", "#3BB2D0", "#3F51B5", "#8E44AD", "#E74C3C", "#A52A2A", "#7F0000"];
  if (breaks) {
    let index = 0;
    while (index < breaks.length - 2 && speedMps > breaks[index + 1]) index++;
    return colors[Math.min(index, colors.length - 1)];
  }
  const [minimum, maximum] = range;
  if (maximum <= minimum) return colors[colors.length - 1];
  return colors[Math.min(colors.length - 1, Math.floor(Math.max(0, Math.min(1, (speedMps - minimum) / (maximum - minimum))) * colors.length))];
}

export function particleSpeedPxPerSecond(speedMps: number): number {
  return Math.min(
    PARTICLE_MAX_SPEED_PX_PER_SECOND,
    Math.max(PARTICLE_MIN_SPEED_PX_PER_SECOND, Math.max(0, speedMps) * PARTICLE_SPEED_SCALE_PX_PER_METER),
  );
}


function particlePhase(row: number, column: number): number {
  // Stable pseudo-random phase keeps one-second lifetimes continuous while
  // the surrounding flow geometry is interpolated during timeline playback.
  const mixed = Math.imul(row + 1, 73856093) ^ Math.imul(column + 1, 19349663);
  return (mixed >>> 0) / 0x1_0000_0000;
}

function median(values: number[]): number {
  if (values.length === 0) return 24;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle];
}

function estimateVectorSpacing(nodes: ProjectedFlowNode[]): number {
  if (nodes.length < 2) return 24;
  const nearestDistances = nodes.map((node, index) => {
    let nearest = Number.POSITIVE_INFINITY;
    // Adjacent rows/columns in the sampled grid occur near one another in
    // the ordered list. Bound preparation work independently of node count.
    nodes.slice(Math.max(0, index - 8), index + 9).forEach((candidate, offset) => {
      const candidateIndex = Math.max(0, index - 8) + offset;
      if (candidateIndex === index) return;
      nearest = Math.min(nearest, Math.hypot(candidate.x - node.x, candidate.y - node.y));
    });
    return nearest;
  }).filter(Number.isFinite);
  return Math.max(8, median(nearestDistances));
}

function bucketKey(x: number, y: number, cellSizePx: number): string {
  return `${Math.floor(x / cellSizePx)}:${Math.floor(y / cellSizePx)}`;
}

function createProjectedFlowField(nodes: ProjectedFlowNode[], nativeSpacingPx?: number): ProjectedFlowField {
  const spacingPx = nativeSpacingPx === undefined ? estimateVectorSpacing(nodes) : Math.max(8, nativeSpacingPx);
  const cellSizePx = Math.max(8, spacingPx * 1.5);
  const buckets = new Map<string, number[]>();
  if (nativeSpacingPx !== undefined) return { nodes, buckets, spacingPx, cellSizePx };
  nodes.forEach((node, index) => {
    const key = bucketKey(node.x, node.y, cellSizePx);
    const bucket = buckets.get(key) ?? [];
    bucket.push(index);
    buckets.set(key, bucket);
  });
  return { nodes, buckets, spacingPx, cellSizePx };
}

function sampleProjectedFlow(
  field: ProjectedFlowField,
  x: number,
  y: number,
): Pick<ProjectedFlowNode, "dx" | "dy" | "speedMps" | "speedPxPerSecond"> | null {
  if (field.nodes.length === 0) return null;
  const cellX = Math.floor(x / field.cellSizePx);
  const cellY = Math.floor(y / field.cellSizePx);
  const searchRadius = 2;
  const maximumDistance = field.spacingPx * 3;
  const candidates: Array<{ node: ProjectedFlowNode; distance: number }> = [];
  for (let offsetY = -searchRadius; offsetY <= searchRadius; offsetY += 1) {
    for (let offsetX = -searchRadius; offsetX <= searchRadius; offsetX += 1) {
      const indices = field.buckets.get(`${cellX + offsetX}:${cellY + offsetY}`) ?? [];
      indices.forEach((index) => {
        const node = field.nodes[index];
        const distance = Math.hypot(node.x - x, node.y - y);
        if (distance <= maximumDistance) {
          candidates.push({ node, distance });
          candidates.sort((left, right) => left.distance - right.distance);
          if (candidates.length > PARTICLE_MAX_NEIGHBORS) candidates.pop();
        }
      });
    }
  }
  if (candidates.length === 0) return null;

  let weightedDx = 0;
  let weightedDy = 0;
  let weightedSpeedMps = 0;
  let weightedSpeedPx = 0;
  let totalWeight = 0;
  candidates.slice(0, PARTICLE_MAX_NEIGHBORS).forEach(({ node, distance }) => {
    const normalizedDistance = distance / field.spacingPx;
    const weight = 1 / (0.15 + normalizedDistance * normalizedDistance);
    weightedDx += node.dx * weight;
    weightedDy += node.dy * weight;
    weightedSpeedMps += node.speedMps * weight;
    weightedSpeedPx += node.speedPxPerSecond * weight;
    totalWeight += weight;
  });
  const directionLength = Math.hypot(weightedDx, weightedDy);
  if (totalWeight <= 0 || directionLength <= 1e-6) return null;
  return {
    dx: weightedDx / directionLength,
    dy: weightedDy / directionLength,
    speedMps: weightedSpeedMps / totalWeight,
    speedPxPerSecond: weightedSpeedPx / totalWeight,
  };
}

function appendTrailPoint(particle: FlowParticle, point: ScreenPoint): void {
  const last = particle.trail[particle.trail.length - 1];
  if (last && Math.hypot(point.x - last.x, point.y - last.y) < PARTICLE_TRAIL_SAMPLE_PX) return;
  particle.trail.push(point);
  let trailLength = 0;
  for (let index = particle.trail.length - 1; index > 0; index -= 1) {
    const current = particle.trail[index];
    const previous = particle.trail[index - 1];
    trailLength += Math.hypot(current.x - previous.x, current.y - previous.y);
    if (trailLength > PARTICLE_TRAIL_LENGTH_PX) {
      particle.trail.splice(0, index - 1);
      break;
    }
  }
}


function overlayStyle(
  metadata: Pick<ResultMetadataResponse, "bounds">,
  imageUrl: string,
): StyleSpecification {
  const coordinates = resultImageCoordinates(metadata.bounds);
  const boundaryCoordinates = [
    [metadata.bounds.west_deg, metadata.bounds.south_deg],
    [metadata.bounds.east_deg, metadata.bounds.south_deg],
    [metadata.bounds.east_deg, metadata.bounds.north_deg],
    [metadata.bounds.west_deg, metadata.bounds.north_deg],
    [metadata.bounds.west_deg, metadata.bounds.south_deg],
  ];

  return {
    version: 8,
    sources: {
      "result-overlay": {
        type: "image",
        url: imageUrl,
        coordinates,
      },
      [FLOW_SOURCE_ID]: {
        type: "geojson",
        data: EMPTY_FLOW,
      },
      "analysis-boundary": {
        type: "geojson",
        data: {
          type: "Feature",
          properties: {},
          geometry: {
            type: "LineString",
            coordinates: boundaryCoordinates,
          },
        },
      },
    },
    layers: [
      {
        id: "result-overlay",
        type: "raster",
        source: "result-overlay",
        paint: {
          "raster-opacity": 1,
          "raster-fade-duration": 0,
        },
      },
      {
        id: FLOW_HALO_LAYER_ID,
        type: "line",
        source: FLOW_SOURCE_ID,
        layout: {
          visibility: "none",
          "line-cap": "round",
          "line-join": "round",
        },
        paint: {
          "line-color": "#FFFFFF",
          "line-width": [
            "interpolate",
            ["linear"],
            ["get", "speed_mps"],
            0.001, 5.0,
            0.50, 6.0,
            2.00, 7.5,
          ],
          "line-opacity": 0.9,
        },
      },
      {
        id: FLOW_LINE_LAYER_ID,
        type: "line",
        source: FLOW_SOURCE_ID,
        layout: {
          visibility: "none",
          "line-cap": "round",
          "line-join": "round",
        },
        paint: {
          "line-color": [
            "step",
            ["get", "speed_mps"],
            "#2DC4B2",
            0.10, "#3BB2D0",
            0.30, "#3F51B5",
            0.50, "#8E44AD",
            1.00, "#E74C3C",
            2.00, "#7F0000",
          ],
          "line-width": [
            "interpolate",
            ["linear"],
            ["get", "speed_mps"],
            0.001, 2.8,
            0.50, 3.6,
            2.00, 4.8,
          ],
          "line-opacity": 1,
        },
      },
      {
        id: "analysis-boundary-casing",
        type: "line",
        source: "analysis-boundary",
        layout: {
          visibility: "visible",
          "line-cap": "round",
          "line-join": "round",
        },
        paint: {
          "line-color": "#FFFFFF",
          "line-width": 8,
          "line-opacity": 0.95,
        },
      },
      {
        id: "analysis-boundary-outline",
        type: "line",
        source: "analysis-boundary",
        layout: {
          visibility: "visible",
          "line-cap": "round",
          "line-join": "round",
        },
        paint: {
          "line-color": "#DC2626",
          "line-width": 4,
          "line-opacity": 1,
        },
      },
    ],
  };
}

export default function ResultMap({
  magicGeometry = null,
  metadata,
  imageUrl,
  flowVectorData,
  nextFlowVectorData,
  flowInterpolation,
  flowSpeedRange = [0.001, 1] as const,
  flowSpeedBreaks,
  flowDisplayMode = flowVectorData ? "vectors" : null,
  backgroundOpacity,
  mapLabel,
  focusPoint,
  onInspect,
  onFlowRenderStats,
  onViewportChange,
  onCaptureReady,
}: Props) {
  const baseContainerRef = useRef<HTMLDivElement | null>(null);
  const overlayContainerRef = useRef<HTMLDivElement | null>(null);
  const baseMapRef = useRef<MapLibreMap | null>(null);
  const overlayMapRef = useRef<MapLibreMap | null>(null);
  const [footprintMap, setFootprintMap] = useState<MapLibreMap | null>(null);
  const markerRef = useRef<Marker | null>(null);
  const flowSvgRef = useRef<SVGSVGElement | null>(null);
  const flowCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const vectorCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const particleRendererRef = useRef<ReturnType<typeof createParticleRenderer>>(null);
  useEffect(() => () => { particleRendererRef.current?.dispose(); }, []);
  const inspectRef = useRef(onInspect);
  const viewportRef = useRef(onViewportChange);
  const initialImageUrlRef = useRef(imageUrl);
  const appliedImageUrlRef = useRef(imageUrl);

  useEffect(() => {
    const map = overlayMapRef.current;
    if (!map || !focusPoint) return;
    map.jumpTo({ center: [focusPoint.lon, focusPoint.lat] });
    markerRef.current?.remove();
    markerRef.current = new Marker({ color: "#1f2937" })
      .setLngLat([focusPoint.lon, focusPoint.lat])
      .addTo(map);
  }, [focusPoint]);

  useEffect(() => {
    inspectRef.current = onInspect;
  }, [onInspect]);

  useEffect(() => {
    viewportRef.current = onViewportChange;
  }, [onViewportChange]);


  useEffect(() => {
    const baseContainer = baseContainerRef.current;
    const overlayContainer = overlayContainerRef.current;
    if (!baseContainer || !overlayContainer) return;

    const bounds = resultBounds(metadata.bounds);
    const baseMap = new MapLibreMap({
      container: baseContainer,
      style: {
        version: 8,
        sources: {
          gsi: {
            type: "raster",
            tiles: ["https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png"],
            tileSize: 256,
            maxzoom: GSI_SOURCE_MAX_ZOOM,
            attribution: "国土地理院",
          },
        },
        layers: [{ id: "gsi", type: "raster", source: "gsi" }],
      },
      bounds,
      fitBoundsOptions: { padding: 32, maxZoom: 18 },
      maxZoom: RESULT_MAX_ZOOM,
      interactive: false,
    });
    baseMapRef.current = baseMap;

    const overlayMap = new MapLibreMap({
      container: overlayContainer,
      style: overlayStyle(metadata, initialImageUrlRef.current),
      bounds,
      fitBoundsOptions: { padding: 32, maxZoom: 18 },
      maxZoom: RESULT_MAX_ZOOM,
      attributionControl: false,
      canvasContextAttributes: { preserveDrawingBuffer: true },
    });
    overlayMapRef.current = overlayMap;
    setFootprintMap(overlayMap);
    onCaptureReady?.(async (expectedImageUrl) => {
      await new Promise<void>((resolve, reject) => {
        const timeout = window.setTimeout(() => {
          overlayMap.off("render", rendered);
          reject(new Error("結果画像の描画が完了しませんでした。"));
        }, 15000);
        const rendered = () => {
          if (expectedImageUrl && appliedImageUrlRef.current !== expectedImageUrl) return;
          if (!overlayMap.getSource("result-overlay") || !overlayMap.isSourceLoaded("result-overlay")) return;
          overlayMap.off("render", rendered);
          window.clearTimeout(timeout);
          resolve();
        };
        overlayMap.on("render", rendered);
        overlayMap.triggerRepaint();
      });
      const source = overlayMap.getCanvas();
      const output = document.createElement("canvas");
      output.width = source.width;
      output.height = source.height;
      const context = output.getContext("2d");
      if (!context) throw new Error("Canvas 2D context is unavailable");
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, output.width, output.height);
      context.drawImage(source, 0, 0);
      const particles = flowCanvasRef.current;
      const vectors = vectorCanvasRef.current;
      if (vectors) context.drawImage(vectors, 0, 0, output.width, output.height);
      if (particles) context.drawImage(particles, 0, 0, output.width, output.height);
      const svg = flowSvgRef.current;
      if (svg && svg.childElementCount > 0) {
        const markup = new XMLSerializer().serializeToString(svg);
        const blobUrl = URL.createObjectURL(new Blob([markup], { type: "image/svg+xml" }));
        try {
          const image = new Image();
          await new Promise<void>((resolve, reject) => {
            image.onload = () => resolve();
            image.onerror = () => reject(new Error("SVG capture failed"));
            image.src = blobUrl;
          });
          context.drawImage(image, 0, 0, output.width, output.height);
        } finally {
          URL.revokeObjectURL(blobUrl);
        }
      }
      return output;
    });
    overlayMap.addControl(new NavigationControl({ showCompass: false }), "top-right");

    const syncBase = () => {
      const center = overlayMap.getCenter();
      baseMap.jumpTo({
        center: [center.lng, center.lat],
        zoom: overlayMap.getZoom(),
        bearing: overlayMap.getBearing(),
        pitch: overlayMap.getPitch(),
      });
    };

    const emitViewport = () => {
      const b = overlayMap.getBounds();
      viewportRef.current?.({ west: b.getWest(), south: b.getSouth(), east: b.getEast(), north: b.getNorth() }, overlayMap.getZoom());
    };

    const handleClick = (event: MapMouseEvent) => {
      if (!inspectRef.current) return;
      markerRef.current?.remove();
      markerRef.current = new Marker({ color: "#1f2937" })
        .setLngLat(event.lngLat)
        .addTo(overlayMap);
      inspectRef.current(event.lngLat.lng, event.lngLat.lat);
    };

    overlayMap.on("move", syncBase);
    overlayMap.on("zoomend", emitViewport);
    overlayMap.on("moveend", emitViewport);
    overlayMap.on("click", handleClick);
    emitViewport();
    overlayMap.once("load", () => {
      syncBase();
      emitViewport();
      overlayMap.setLayoutProperty("analysis-boundary-casing", "visibility", "visible");
      overlayMap.setLayoutProperty("analysis-boundary-outline", "visibility", "visible");
      overlayMap.triggerRepaint();
    });

    return () => {
      markerRef.current?.remove();
      markerRef.current = null;
      onCaptureReady?.(null);
      overlayMap.off("move", syncBase);
      overlayMap.off("zoomend", emitViewport);
      overlayMap.off("moveend", emitViewport);
      overlayMap.off("click", handleClick);
      overlayMap.remove();
      baseMap.remove();
      overlayMapRef.current = null;
      baseMapRef.current = null;
    };
  }, [metadata]);

  useEffect(() => {
    const map = overlayMapRef.current;
    if (!map) return;

    const update = () => {
      const source = map.getSource("result-overlay") as ImageSource | undefined;
      appliedImageUrlRef.current = imageUrl;
      source?.updateImage({
        url: imageUrl,
        coordinates: resultImageCoordinates(metadata.bounds),
      });
      map.triggerRepaint();
    };

    if (map.getSource("result-overlay")) {
      update();
      return;
    }
    map.once("load", update);
    return () => {
      map.off("load", update);
    };
  }, [imageUrl, metadata.bounds]);

  useEffect(() => {
    const map = overlayMapRef.current;
    const svg = flowSvgRef.current;
    if (!map || !svg) return;

    let idleReporter: (() => void) | null = null;

    const featureBounds = (): [number, number, number, number] | null => {
      if (!flowVectorData || flowVectorData.features.length === 0) return null;
      const bounds: [number, number, number, number] = [Infinity, Infinity, -Infinity, -Infinity];
      for (const feature of flowVectorData.features) {
        for (const line of feature.geometry.coordinates) {
          for (const [lon, lat] of line) {
            bounds[0] = Math.min(bounds[0], lon); bounds[1] = Math.min(bounds[1], lat);
            bounds[2] = Math.max(bounds[2], lon); bounds[3] = Math.max(bounds[3], lat);
          }
        }
      }
      return Number.isFinite(bounds[0]) ? bounds : null;
    };

    const nextById = new Map(nextFlowVectorData?.features.map((feature) =>
      [`${feature.properties.row}:${feature.properties.column}`, feature]) ?? []);
    const canvas = vectorCanvasRef.current;
    const context = canvas?.getContext("2d");
    const renderVectors = (displayFlow: FlowVectorFeatureCollection | null) => {
      if (!canvas || !context) return;
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(1, canvas.clientWidth), height = Math.max(1, canvas.clientHeight);
      if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
        canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
      }
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      const paths = new Map<string, Path2D>();
      let count = 0;
      const fraction = flowInterpolation?.current ?? 0;
      for (const feature of displayFlow?.features ?? []) {
        const shaft = feature.geometry.coordinates[0];
        if (!shaft || shaft.length < 2) continue;
        const next = nextById.get(`${feature.properties.row}:${feature.properties.column}`);
        const nextShaft = next?.geometry.coordinates[0];
        const project = (index: number) => {
          const point = shaft[index], other = nextShaft?.[index] ?? point;
          return map.project([point[0] + (other[0] - point[0]) * fraction,
            point[1] + (other[1] - point[1]) * fraction]);
        };
        const tail = project(0), tip = project(shaft.length - 1);
        const length = Math.hypot(tip.x - tail.x, tip.y - tail.y);
        if (length <= 1e-6) continue;
        const dx = (tip.x - tail.x) / length, dy = (tip.y - tail.y) / length;
        const tipX = tail.x + dx * FLOW_ARROW_LENGTH_PX, tipY = tail.y + dy * FLOW_ARROW_LENGTH_PX;
        const head = FLOW_ARROW_LENGTH_PX * 0.28, spread = head * 0.58;
        const speed = feature.properties.speed_mps + ((next?.properties.speed_mps ?? feature.properties.speed_mps) - feature.properties.speed_mps) * fraction;
        const color = flowColor(speed, flowSpeedRange, flowSpeedBreaks);
        const path = paths.get(color) ?? new Path2D();
        path.moveTo(tail.x, tail.y); path.lineTo(tipX, tipY);
        path.moveTo(tipX, tipY); path.lineTo(tipX - dx * head - dy * spread, tipY - dy * head + dx * spread);
        path.moveTo(tipX, tipY); path.lineTo(tipX - dx * head + dy * spread, tipY - dy * head - dx * spread);
        paths.set(color, path);
        count++;
      }
      context.lineCap = "round"; context.lineJoin = "round";
      context.lineWidth = 7; context.strokeStyle = "rgba(255,255,255,0.96)";
      for (const path of paths.values()) context.stroke(path);
      context.lineWidth = 4;
      for (const [color, path] of paths) { context.strokeStyle = color; context.stroke(path); }
      canvas.dataset.flowCanvasArrows = String(count);
      svg.dataset.flowSvgArrows = "0";
    };

    const report = () => {
      const bounds = map.getBounds();
      onFlowRenderStats?.({
        sourceFeatureCount: map.querySourceFeatures(FLOW_SOURCE_ID).length,
        renderedFeatureCount: map.queryRenderedFeatures({
          layers: [FLOW_LINE_LAYER_ID],
        }).length,
        svgArrowCount: 0,
        canvasArrowCount: Number(canvas?.dataset.flowCanvasArrows ?? "0"),
        layerOrder: (map.getStyle().layers ?? []).map((layer) => layer.id),
        featureBounds: featureBounds(),
        mapBounds: [
          bounds.getWest(),
          bounds.getSouth(),
          bounds.getEast(),
          bounds.getNorth(),
        ],
      });
    };

    const update = () => {
      const displayFlow = flowDisplayMode === "vectors" ? flowVectorData : null;
      const visibility = displayFlow && displayFlow.features.length > 0 ? "visible" : "none";
      map.setLayoutProperty(FLOW_HALO_LAYER_ID, "visibility", "none");
      map.setLayoutProperty(FLOW_LINE_LAYER_ID, "visibility", "none");

      // Keep an explicit deterministic stack after any source/image update.
      // Result raster < MapLibre vector layers < analysis boundary.
      map.moveLayer(FLOW_HALO_LAYER_ID);
      map.moveLayer(FLOW_LINE_LAYER_ID);
      map.moveLayer("analysis-boundary-casing");
      map.moveLayer("analysis-boundary-outline");
      map.setLayoutProperty("analysis-boundary-casing", "visibility", "visible");
      map.setLayoutProperty("analysis-boundary-outline", "visibility", "visible");

      // One batched Canvas pass; no SVG nodes or duplicate MapLibre geometry.
      renderVectors(displayFlow);

      if (visibility === "none") {
        onFlowRenderStats?.(null);
      } else {
        idleReporter = report;
        map.once("idle", report);
      }
      map.triggerRepaint();
    };

    let vectorFrame = 0, lastVectorFrame = 0;
    const animateVectors = (now: number) => {
      if (now - lastVectorFrame >= 100 && flowDisplayMode === "vectors") {
        renderVectors(flowVectorData); lastVectorFrame = now;
      }
      vectorFrame = requestAnimationFrame(animateVectors);
    };
    if (nextFlowVectorData && flowInterpolation && flowDisplayMode === "vectors") vectorFrame = requestAnimationFrame(animateVectors);
    map.on("moveend", update);
    map.on("resize", update);

    if (map.getSource(FLOW_SOURCE_ID)) {
      update();
    } else {
      map.once("load", update);
    }
    return () => {
      map.off("load", update);
      map.off("moveend", update);
      map.off("resize", update);
      cancelAnimationFrame(vectorFrame);
      if (idleReporter) map.off("idle", idleReporter);
      svg.replaceChildren();
      svg.dataset.flowSvgArrows = "0";
    };
  }, [flowDisplayMode, flowVectorData, nextFlowVectorData, flowInterpolation, flowSpeedRange, flowSpeedBreaks, onFlowRenderStats]);

  useEffect(() => {
    const map = overlayMapRef.current;
    const canvas = flowCanvasRef.current;
    if (!map || !canvas) return;
    if (flowDisplayMode !== "particles" || !flowVectorData) {
      canvas.dataset.flowParticles = "0";
      return;
    }

    const gpu = particleRendererRef.current ?? createParticleRenderer(canvas);
    particleRendererRef.current = gpu;
    const context = gpu ? null : canvas.getContext("2d");
    if (!context && !gpu) return;
    canvas.dataset.flowRenderBackend = gpu ? "webgl" : "canvas";
    const buildField = () => createProjectedFlowField(flowVectorData.features.flatMap((feature) => {
      const shaft = feature.geometry.coordinates[0];
      if (!shaft || shaft.length < 2) return [];
      const tail = map.project([shaft[0][0], shaft[0][1]]);
      const tip = map.project([shaft[shaft.length - 1][0], shaft[shaft.length - 1][1]]);
      const length = Math.hypot(tip.x - tail.x, tip.y - tail.y);
      if (length <= 1e-6) return [];
      return [{
        x: tail.x + (tip.x - tail.x) * 0.42,
        y: tail.y + (tip.y - tail.y) * 0.42,
        dx: (tip.x - tail.x) / length,
        dy: (tip.y - tail.y) / length,
        speedMps: feature.properties.speed_mps,
        speedPxPerSecond: particleSpeedPxPerSecond(feature.properties.speed_mps),
      }];
    }), nativeField ? nativeField.spacingPx * flowVectorData.metadata.arrow_length_m / (0.8 * flowVectorData.flow_field!.cell_size_m) : undefined);

    const project = (coordinate: number[]) => map.project([coordinate[0], coordinate[1]]);
    let nativeField = flowVectorData.flow_field ? projectNativeFlowField(flowVectorData.flow_field, project) : null;
    let nextNativeField = nextFlowVectorData?.flow_field ? projectNativeFlowField(nextFlowVectorData.flow_field, project) : null;
    let field = buildField();
    const sample = (x: number, y: number) => {
      if (!nativeField) return sampleProjectedFlow(field, x, y);
      const current = nativeField.sample(x, y);
      if (!current) return null;
      const next = nextNativeField?.sample(x, y);
      const fraction = flowInterpolation?.current ?? 0;
      const flow = nativeField.vector(
        current.uMps + ((next?.uMps ?? current.uMps) - current.uMps) * fraction,
        current.vMps + ((next?.vMps ?? current.vMps) - current.vMps) * fraction,
      );
      return flow ? { ...flow, speedPxPerSecond: particleSpeedPxPerSecond(flow.speedMps) } : null;
    };
    const resetParticle = (
      particle: FlowParticle,
      index: number,
      staggerInitialLifetime = false,
    ) => {
      if (field.nodes.length === 0) return;
      particle.generation += 1;
      const seedIndex = particle.sourceIndex % field.nodes.length;
      const seed = field.nodes[seedIndex];
      const sourceFeature = flowVectorData.features[seedIndex % flowVectorData.features.length];
      const phase = particlePhase(
        (sourceFeature?.properties.row ?? seedIndex) + particle.copyIndex * 1009,
        (sourceFeature?.properties.column ?? seedIndex) + particle.generation,
      );
      const offset = (phase - 0.5) * field.spacingPx;
      particle.x = seed.x + seed.dx * offset;
      particle.y = seed.y + seed.dy * offset;
      if (nativeField && !nativeField.sample(particle.x, particle.y)) {
        particle.x = seed.x;
        particle.y = seed.y;
      }
      particle.targetDistancePx = field.spacingPx * PARTICLE_MIN_VECTOR_CROSSINGS;
      particle.travelledPx = staggerInitialLifetime
        ? particle.targetDistancePx * (index % PARTICLE_PHASE_GROUPS) / PARTICLE_PHASE_GROUPS
        : 0;
      particle.trail = [{ x: particle.x, y: particle.y }];
    };
    const createParticles = () => field.nodes.flatMap((node, sourceIndex) => (
      Array.from({ length: PARTICLES_PER_VECTOR }, (_, copyIndex) => {
        const particleIndex = sourceIndex * PARTICLES_PER_VECTOR + copyIndex;
        const particle: FlowParticle = {
          sourceIndex,
          copyIndex,
          x: node.x,
          y: node.y,
          travelledPx: 0,
          targetDistancePx: field.spacingPx * PARTICLE_MIN_VECTOR_CROSSINGS,
          trail: [{ x: node.x, y: node.y }],
          generation: -1,
        };
        resetParticle(particle, particleIndex, true);
        return particle;
      })
    ));
    let particles: FlowParticle[] = createParticles();
    const rebuildField = () => {
      nativeField = flowVectorData.flow_field ? projectNativeFlowField(flowVectorData.flow_field, project) : null;
      nextNativeField = nextFlowVectorData?.flow_field ? projectNativeFlowField(nextFlowVectorData.flow_field, project) : null;
      field = buildField();
      particles = createParticles();
      canvas.dataset.flowParticleSpacingPx = field.spacingPx.toFixed(1);
      canvas.dataset.flowParticleTargetDistancePx = (
        field.spacingPx * PARTICLE_MIN_VECTOR_CROSSINGS
      ).toFixed(1);
    };
    canvas.dataset.flowParticleMinCrossings = String(PARTICLE_MIN_VECTOR_CROSSINGS);
    canvas.dataset.flowParticlesPerVector = String(PARTICLES_PER_VECTOR);
    canvas.dataset.flowParticlePhaseGroups = String(PARTICLE_PHASE_GROUPS);
    canvas.dataset.flowParticleSeedPolicy = "fixed-source";
    canvas.dataset.flowParticlePhaseMode = "lifetime-offset";
    canvas.dataset.flowParticleTrailLengthPx = String(PARTICLE_TRAIL_LENGTH_PX);
    canvas.dataset.flowParticleSpacingPx = field.spacingPx.toFixed(1);
    canvas.dataset.flowParticleTargetDistancePx = (
      field.spacingPx * PARTICLE_MIN_VECTOR_CROSSINGS
    ).toFixed(1);
    map.on("moveend", rebuildField);
    map.on("resize", rebuildField);

    let frame = 0;
    let disposed = false;
    let previousFrameMs: number | null = null;

    const render = (now: number) => {
      if (disposed) return;
      if (previousFrameMs !== null && now - previousFrameMs < 1000 / 30) {
        frame = window.requestAnimationFrame(render);
        return;
      }
      const frameStarted = performance.now();
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(1, canvas.clientWidth);
      const height = Math.max(1, canvas.clientHeight);
      const pixelWidth = Math.round(width * ratio);
      const pixelHeight = Math.round(height * ratio);
      if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
        canvas.width = pixelWidth;
        canvas.height = pixelHeight;
      }
      context?.setTransform(ratio, 0, 0, ratio, 0, 0);
      context?.clearRect(0, 0, width, height);
      gpu?.begin(width, height, ratio);
      const elapsedSeconds = previousFrameMs === null
        ? 1 / 60
        : Math.min(0.05, Math.max(0, (now - previousFrameMs) / 1000));
      previousFrameMs = now;

      let rendered = 0;
      const paths = new Map<string, { trails: Path2D[]; points: Path2D }>();
      const halos = new Path2D();
      particles.forEach((particle, index) => {
        let flow = sample(particle.x, particle.y);
        if (!flow) {
          resetParticle(particle, index);
          flow = sample(particle.x, particle.y);
        }
        if (!flow) return;
        const distance = flow.speedPxPerSecond * elapsedSeconds;
        particle.x += flow.dx * distance;
        particle.y += flow.dy * distance;
        particle.travelledPx += distance;
        appendTrailPoint(particle, { x: particle.x, y: particle.y });

        const outsideViewport = particle.x < -6
          || particle.y < -6
          || particle.x > width + 6
          || particle.y > height + 6;
        if (outsideViewport || particle.travelledPx >= particle.targetDistancePx) {
          resetParticle(particle, index);
          flow = sample(particle.x, particle.y);
          if (!flow) return;
        }

        const particleColor = flowColor(flow.speedMps, flowSpeedRange, flowSpeedBreaks);
        if (gpu) {
          particle.trail.forEach((point, index) => gpu.point(point.x, point.y, particleColor,
            (index + 1) / particle.trail.length, 5.5, 0.55));
          gpu.point(particle.x, particle.y, particleColor, 1, 6.4, 0.56);
          rendered++;
          return;
        }
        const group = paths.get(particleColor) ?? { trails: [new Path2D(), new Path2D(), new Path2D()], points: new Path2D() };
        let previousSegmentGroup = -1;
        for (let segment = 1; segment < particle.trail.length; segment++) {
          const segmentGroup = Math.min(2, Math.floor(segment * 3 / particle.trail.length));
          const path = group.trails[segmentGroup];
          if (segmentGroup !== previousSegmentGroup) path.moveTo(particle.trail[segment - 1].x, particle.trail[segment - 1].y);
          path.lineTo(particle.trail[segment].x, particle.trail[segment].y);
          previousSegmentGroup = segmentGroup;
        }
        halos.moveTo(particle.x + 3.2, particle.y);
        halos.arc(particle.x, particle.y, 3.2, 0, Math.PI * 2);
        group.points.moveTo(particle.x + 1.8, particle.y);
        group.points.arc(particle.x, particle.y, 1.8, 0, Math.PI * 2);
        paths.set(particleColor, group);
        rendered += 1;
      });
      if (context) {
        context.lineCap = "round";
        for (let segment = 0; segment < 3; segment++) {
          context.globalAlpha = [0.25, 0.55, 1][segment];
          context.strokeStyle = "#FFFFFF"; context.lineWidth = 5.5;
          for (const group of paths.values()) context.stroke(group.trails[segment]);
          context.lineWidth = 2.5;
          for (const [color, group] of paths) { context.strokeStyle = color; context.stroke(group.trails[segment]); }
        }
        context.globalAlpha = 0.92; context.fillStyle = "#FFFFFF"; context.fill(halos);
        context.globalAlpha = 1;
        for (const [color, group] of paths) { context.fillStyle = color; context.fill(group.points); }
      }
      gpu?.finish();
      canvas.dataset.flowParticles = String(rendered);
      canvas.dataset.flowFieldCellSizeM = String(flowVectorData.flow_field?.cell_size_m ?? "legacy");
      canvas.dataset.flowFrameMs = (performance.now() - frameStarted).toFixed(2);
      frame = window.requestAnimationFrame(render);
    };

    frame = window.requestAnimationFrame(render);
    return () => {
      disposed = true;
      window.cancelAnimationFrame(frame);
      map.off("moveend", rebuildField);
      map.off("resize", rebuildField);
      context?.clearRect(0, 0, canvas.width, canvas.height);
      gpu?.clear();
      canvas.dataset.flowParticles = "0";
    };
  }, [flowDisplayMode, flowVectorData, nextFlowVectorData, flowInterpolation, flowSpeedRange, flowSpeedBreaks]);

  return (
    <div className="result-map-stack" role="region" aria-label={mapLabel}>
      <div
        ref={baseContainerRef}
        className="result-map result-map-base"
        style={{ opacity: Math.max(0, Math.min(1, backgroundOpacity)) }}
        aria-hidden="true"
      />
      <div
        ref={overlayContainerRef}
        className="result-map result-map-overlay"
      />
      <svg
        ref={flowSvgRef}
        className="result-flow-svg"
        aria-hidden="true"
        data-flow-svg-arrows="0"
      />
      <canvas
        ref={vectorCanvasRef}
        className="result-vector-canvas"
        aria-hidden="true"
      />
      <canvas
        ref={flowCanvasRef}
        className="result-flow-canvas"
        aria-hidden="true"
        data-flow-particles="0"
      />
      <MagicResultFootprint map={footprintMap} geometry={magicGeometry} />
    </div>
  );
}

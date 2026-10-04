import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  FlowVectorFeatureCollection,
  ResultMetadataResponse,
} from "../api/client";
import ResultMap, { particleSpeedPxPerSecond } from "./ResultMap";

const mocks = vi.hoisted(() => ({
  constructorOptions: [] as Array<Record<string, unknown>>,
  jumpTo: vi.fn(),
  updateImage: vi.fn(),
  setData: vi.fn(),
  setLayoutProperty: vi.fn(),
  moveLayer: vi.fn(),
  querySourceFeatures: vi.fn(),
  queryRenderedFeatures: vi.fn(),
  triggerRepaint: vi.fn(),
  setWorkerUrl: vi.fn(),
  createLinearGradient: vi.fn(),
  addColorStop: vi.fn(),
  arc: vi.fn(),
  fill: vi.fn(),
  pathMove: vi.fn(),
  pathLine: vi.fn(),
}));

vi.mock("maplibre-gl", () => {
  class Map {
    sources = new globalThis.Map<string, unknown>();
    style: { layers?: Array<{ id: string }> };

    constructor(options: Record<string, unknown>) {
      mocks.constructorOptions.push(options);
      const style = options.style as {
        sources?: Record<string, { type?: string }>;
        layers?: Array<{ id: string }>;
      };
      this.style = style;
      for (const [id, source] of Object.entries(style.sources ?? {})) {
        if (source.type === "image") {
          this.sources.set(id, { updateImage: mocks.updateImage });
        }
        if (source.type === "geojson") {
          this.sources.set(id, { setData: mocks.setData });
        }
      }
    }
    addControl() {}
    on() {}
    off() {}
    remove() {}
    once(_event: string, callback: () => void) { callback(); }
    jumpTo(value: unknown) { mocks.jumpTo(value); }
    getCenter() { return { lng: 139.75, lat: 35.65 }; }
    getZoom() { return 15; }
    getBearing() { return 0; }
    getPitch() { return 0; }
    getCanvas() { return { clientWidth: 1920, clientHeight: 1080 }; }
    getSource(id: string) { return this.sources.get(id); }
    setLayoutProperty(...args: unknown[]) { mocks.setLayoutProperty(...args); }
    moveLayer(...args: unknown[]) { mocks.moveLayer(...args); }
    querySourceFeatures(...args: unknown[]) {
      mocks.querySourceFeatures(...args);
      return flowData.features;
    }
    queryRenderedFeatures(...args: unknown[]) {
      mocks.queryRenderedFeatures(...args);
      return flowData.features;
    }
    getStyle() { return this.style; }
    project(lngLat: [number, number]) {
      return { x: (lngLat[0] - 139.7) * 5000, y: (35.7 - lngLat[1]) * 5000 };
    }
    getBounds() {
      return {
        getWest: () => 139.7,
        getSouth: () => 35.6,
        getEast: () => 139.8,
        getNorth: () => 35.7,
      };
    }
    triggerRepaint() { mocks.triggerRepaint(); }
  }

  class Marker {
    setLngLat() { return this; }
    addTo() { return this; }
    remove() {}
  }

  class NavigationControl {}

  return {
    Map,
    Marker,
    NavigationControl,
    setWorkerUrl: mocks.setWorkerUrl,
  };
});

const metadata: ResultMetadataResponse = {
  schema_version: "1",
  bounds: {
    west_deg: 139.7,
    south_deg: 35.6,
    east_deg: 139.8,
    north_deg: 35.7,
  },
  units: {
    water_depth: "m",
    terrain_elevation: "m",
    grid_resolution: "m",
  },
  available_time_indices: [0, 3],
  flow_vectors_available: true,
  time_values: [
    "2026-01-01T00:00:00",
    "2026-01-01T00:10:00",
    "2026-01-01T00:20:00",
    "2026-01-01T00:30:00",
  ],
  max_depth_summary: { global_max_depth_m: 1.0 },
  grid_level_summary: { "1m": 1200000 },
  depth_legend: [],
  provider_summary: { warnings: [] },
  engine_summary: {},
  run_summary: {
    application_version: "0.1.0",
    requested_accuracy_mode: "full_1m",
    rainfall_source: {},
    elevation_provider_counts: {},
    elevation_source_summary: {},
    manning_defaults: {},
    boundary_policy: "closed boundary",
    roof_rain_mass_diagnostic: {},
  },
  no_data_policy: "inactive cells are NaN",
  limitations: {},
};

const flowData: FlowVectorFeatureCollection = {
  type: "FeatureCollection",
  features: [{
    type: "Feature",
    geometry: {
      type: "MultiLineString",
      coordinates: [[[139.74, 35.64], [139.75, 35.65]]],
    },
    properties: {
      speed_mps: 0.4,
      u_mps: 0.4,
      v_mps: 0,
      time_index: 3,
      row: 10,
      column: 20,
    },
  }],
  metadata: {
    speed_unit: "m/s",
    min_speed_mps: 0.001,
    sample_stride_cells: 8,
    arrow_length_m: 6.4,
    arrow_count: 1,
    sampling_method: "max-speed-wet-cell-per-block",
    viewport: { west: 139.7, south: 35.6, east: 139.8, north: 35.7 },
  },
};

function options(index: number) {
  return mocks.constructorOptions[index] as {
    maxZoom?: number;
    style: {
      sources: Record<string, { type?: string; url?: string; data?: unknown; maxzoom?: number }>;
      layers: Array<{
        id: string;
        type: string;
        paint?: Record<string, unknown>;
        layout?: Record<string, unknown>;
      }>;
    };
  };
}

describe("ResultMap", () => {
  it("moves the map to the requested deepest-cell coordinates", () => {
    const props = { metadata, imageUrl: "/test.png", flowVectorData: null, backgroundOpacity: 1, mapLabel: "test" };
    const view = render(<ResultMap {...props} />);
    view.rerender(<ResultMap {...props} focusPoint={{ lon: 139.76, lat: 35.66 }} />);
    expect(mocks.jumpTo).toHaveBeenCalledWith({ center: [139.76, 35.66] });
  });

  it("maps particle travel speed proportionally to hydraulic velocity", () => {
    expect(particleSpeedPxPerSecond(0.1)).toBeCloseTo(3.6);
    expect(particleSpeedPxPerSecond(0.5)).toBeCloseTo(18);
    expect(particleSpeedPxPerSecond(1)).toBeCloseTo(36);
    expect(particleSpeedPxPerSecond(2)).toBeCloseTo(72);
    expect(particleSpeedPxPerSecond(2)).toBe(
      particleSpeedPxPerSecond(1) * 2,
    );
  });

  beforeEach(() => {
    vi.stubGlobal("Path2D", class {
      moveTo = mocks.pathMove;
      lineTo = mocks.pathLine;
      arc = mocks.arc;
    });
    mocks.pathMove.mockClear(); mocks.pathLine.mockClear();
    mocks.constructorOptions.length = 0;
    mocks.jumpTo.mockClear();
    mocks.updateImage.mockClear();
    mocks.setData.mockClear();
    mocks.setLayoutProperty.mockClear();
    mocks.moveLayer.mockClear();
    mocks.querySourceFeatures.mockClear();
    mocks.queryRenderedFeatures.mockClear();
    mocks.triggerRepaint.mockClear();
    mocks.createLinearGradient.mockReset();
    mocks.addColorStop.mockReset();
    mocks.arc.mockReset();
    mocks.fill.mockReset();
    mocks.createLinearGradient.mockReturnValue({ addColorStop: mocks.addColorStop });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      setTransform: vi.fn(),
      clearRect: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      arc: mocks.arc,
      fill: mocks.fill,
      createLinearGradient: mocks.createLinearGradient,
      drawImage: vi.fn(),
      lineCap: "butt",
      lineWidth: 1,
      strokeStyle: "#000",
      fillStyle: "#000",
    } as unknown as CanvasRenderingContext2D);
  });

  it("keeps the red analysis boundary visibly cased above the result raster", () => {
    render(
      <ResultMap
        metadata={metadata}
        imageUrl="/api/result/max.png"
        flowVectorData={null}
        backgroundOpacity={0.55}
        mapLabel="結果"
        onInspect={vi.fn()}
      />,
    );

    const overlay = options(1);
    expect(mocks.setWorkerUrl).toHaveBeenCalled();
    expect(options(0).maxZoom).toBe(21);
    expect(overlay.maxZoom).toBe(21);
    expect(options(0).style.sources.gsi.maxzoom).toBe(18);
    expect(overlay.style.sources["analysis-boundary"].data).toEqual(
      expect.objectContaining({
        geometry: expect.objectContaining({ type: "LineString" }),
      }),
    );
    const casing = overlay.style.layers.find(
      (layer) => layer.id === "analysis-boundary-casing",
    );
    const outline = overlay.style.layers.find(
      (layer) => layer.id === "analysis-boundary-outline",
    );
    expect(casing?.paint?.["line-width"]).toBe(8);
    expect(outline?.paint?.["line-color"]).toBe("#DC2626");
    expect(outline?.paint?.["line-opacity"]).toBe(1);
  });

  it("updates result images without recreating MapLibre and renders vector data with halo", () => {
    const onFlowRenderStats = vi.fn();
    const view = render(
      <ResultMap
        metadata={metadata}
        imageUrl="/api/result/max.png"
        flowVectorData={null}
        backgroundOpacity={0.55}
        mapLabel="結果"
        onInspect={vi.fn()}
        onFlowRenderStats={onFlowRenderStats}
      />,
    );
    const initialMapCount = mocks.constructorOptions.length;

    view.rerender(
      <ResultMap
        metadata={metadata}
        imageUrl="/api/result/depth.png?time_index=3"
        flowVectorData={flowData}
        backgroundOpacity={0.55}
        mapLabel="結果"
        onInspect={vi.fn()}
        onFlowRenderStats={onFlowRenderStats}
      />,
    );

    expect(mocks.constructorOptions).toHaveLength(initialMapCount);
    expect(mocks.updateImage).toHaveBeenLastCalledWith(
      expect.objectContaining({ url: "/api/result/depth.png?time_index=3" }),
    );
    expect(mocks.setData).not.toHaveBeenCalled();
    expect(mocks.setLayoutProperty).toHaveBeenCalledWith(
      "flow-vector-halo",
      "visibility",
      "none",
    );
    expect(mocks.setLayoutProperty).toHaveBeenCalledWith(
      "flow-vector-lines",
      "visibility",
      "none",
    );
    expect(mocks.moveLayer).toHaveBeenCalledWith("flow-vector-halo");
    expect(mocks.moveLayer).toHaveBeenCalledWith("flow-vector-lines");
    expect(mocks.querySourceFeatures).toHaveBeenCalledWith("flow-vector-source");
    expect(mocks.queryRenderedFeatures).toHaveBeenCalledWith({
      layers: ["flow-vector-lines"],
    });
    expect(onFlowRenderStats).toHaveBeenCalledWith(
      expect.objectContaining({
        sourceFeatureCount: 1,
        renderedFeatureCount: 1,
        svgArrowCount: 0,
        canvasArrowCount: 1,
      }),
    );
    const svg = view.container.querySelector(".result-flow-svg");
    expect(svg).toHaveAttribute("data-flow-svg-arrows", "0");
    expect(svg?.querySelectorAll("path")).toHaveLength(0);
    expect(view.container.querySelector(".result-vector-canvas")).toHaveAttribute("data-flow-canvas-arrows", "1");
    const [x1, y1] = mocks.pathMove.mock.calls[0];
    const [x2, y2] = mocks.pathLine.mock.calls[0];
    expect(Math.hypot(x2 - x1, y2 - y1)).toBeCloseTo(18, 1);

    const overlay = options(1);
    const vectorPaint = overlay.style.layers.find(
      (layer) => layer.id === "flow-vector-lines",
    )?.paint;
    expect(vectorPaint?.["line-color"]).toEqual(
      expect.arrayContaining(["step", expect.anything()]),
    );
    expect(
      overlay.style.layers.find((layer) => layer.id === "flow-vector-halo"),
    ).toBeDefined();
  });

  it("renders two continuously visible particles per vector with staggered lifetimes", () => {
    let animationFrame: FrameRequestCallback | null = null;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      animationFrame = callback;
      return 1;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);

    const staggeredFlowData: FlowVectorFeatureCollection = {
      ...flowData,
      features: Array.from({ length: 4 }, (_, index) => ({
        ...flowData.features[0],
        geometry: {
          type: "MultiLineString",
          coordinates: [[
            [139.74 + index * 0.002, 35.64],
            [139.741 + index * 0.002, 35.64],
          ]],
        },
        properties: {
          ...flowData.features[0].properties,
          row: 10 + index,
          column: 20 + index,
        },
      })),
      metadata: { ...flowData.metadata, arrow_count: 4 },
    };
    const view = render(
      <ResultMap
        metadata={metadata}
        imageUrl="/api/result/depth.png?time_index=3"
        flowVectorData={staggeredFlowData}
        flowDisplayMode="particles"
        backgroundOpacity={0.55}
        mapLabel="結果"
        onInspect={vi.fn()}
      />,
    );
    const canvas = view.container.querySelector(".result-flow-canvas") as HTMLCanvasElement;
    Object.defineProperty(canvas, "clientWidth", { configurable: true, value: 800 });
    Object.defineProperty(canvas, "clientHeight", { configurable: true, value: 600 });

    act(() => animationFrame?.(500));

    expect(canvas).toHaveAttribute("data-flow-particles", "8");
    expect(view.container.querySelector(".result-flow-svg")).toHaveAttribute(
      "data-flow-svg-arrows",
      "0",
    );
    expect(mocks.setLayoutProperty).toHaveBeenCalledWith(
      "flow-vector-lines",
      "visibility",
      "none",
    );
    expect(canvas).toHaveAttribute("data-flow-particle-min-crossings", "15");
    expect(canvas).toHaveAttribute("data-flow-particles-per-vector", "2");
    expect(canvas).toHaveAttribute("data-flow-particle-phase-groups", "4");
    expect(canvas).toHaveAttribute("data-flow-particle-seed-policy", "fixed-source");
    expect(canvas).toHaveAttribute("data-flow-particle-phase-mode", "lifetime-offset");
    expect(canvas).toHaveAttribute("data-flow-particle-trail-length-px", "75");
    const spacing = Number(canvas.dataset.flowParticleSpacingPx);
    const targetDistance = Number(canvas.dataset.flowParticleTargetDistancePx);
    expect(targetDistance / spacing).toBeCloseTo(15, 5);
    expect(mocks.arc).toHaveBeenCalledTimes(16);
    expect(mocks.fill).toHaveBeenCalledTimes(2);
    expect(mocks.createLinearGradient).not.toHaveBeenCalled();

    act(() => {
      for (let step = 1; step <= 30; step += 1) {
        animationFrame?.(500 + step * 50);
      }
    });
    expect(canvas).toHaveAttribute("data-flow-particles", "8");
  });

  it("keeps fast and slow flow particles visible across unequal lifetimes", () => {
    let animationFrame: FrameRequestCallback | null = null;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      animationFrame = callback;
      return 1;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);

    const speeds = [0.05, 0.2, 1, 2];
    const mixedSpeedFlowData: FlowVectorFeatureCollection = {
      ...flowData,
      features: speeds.map((speed, index) => ({
        ...flowData.features[0],
        geometry: {
          type: "MultiLineString",
          coordinates: [[
            [139.74 + index * 0.002, 35.64],
            [139.741 + index * 0.002, 35.64],
          ]],
        },
        properties: {
          ...flowData.features[0].properties,
          speed_mps: speed,
          u_mps: speed,
          row: 10 + index,
          column: 20 + index,
        },
      })),
      metadata: { ...flowData.metadata, arrow_count: speeds.length },
    };
    const view = render(
      <ResultMap
        metadata={metadata}
        imageUrl="/api/result/depth.png?time_index=3"
        flowVectorData={mixedSpeedFlowData}
        flowDisplayMode="particles"
        backgroundOpacity={0.55}
        mapLabel="結果"
        onInspect={vi.fn()}
      />,
    );
    const canvas = view.container.querySelector(".result-flow-canvas") as HTMLCanvasElement;
    Object.defineProperty(canvas, "clientWidth", { configurable: true, value: 800 });
    Object.defineProperty(canvas, "clientHeight", { configurable: true, value: 600 });

    act(() => {
      for (let step = 0; step <= 50; step += 1) {
        animationFrame?.(step * 50);
      }
    });
    expect(canvas).toHaveAttribute("data-flow-particles", "8");

    act(() => {
      for (let step = 51; step <= 250; step += 1) {
        animationFrame?.(step * 50);
        expect(canvas).toHaveAttribute("data-flow-particles", "8");
      }
    });
    expect(canvas).toHaveAttribute("data-flow-particle-seed-policy", "fixed-source");
    expect(canvas).toHaveAttribute("data-flow-particle-phase-mode", "lifetime-offset");
  });
});

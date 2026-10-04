import { useEffect } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getFlowVectors,
  startResultExtrema,
  getResultExtremaProgress,
  startResultEnergy,
  getResultEnergy,
  getResultEnergyPoint,
  inspectResult,
  type FlowVectorFeatureCollection,
  type ResultMetadataResponse,
} from "../api/client";
import ResultPanel, { strideForZoom } from "./ResultPanel";

const completeExtrema = { status: "complete" as const, progress_percent: 100, processed_frames: 4, total_frames: 4 };

const resultMapMockState = vi.hoisted(() => ({ reportViewport: true, captureError: false }));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getFlowVectors: vi.fn(), inspectResult: vi.fn(), startResultExtrema: vi.fn(), getResultExtremaProgress: vi.fn(), startResultEnergy: vi.fn(), getResultEnergy: vi.fn(), getResultEnergyPoint: vi.fn() };
});

vi.mock("./ResultMap", () => ({
  default: ({
    mapLabel,
    focusPoint,
    imageUrl,
    flowVectorData,
    flowDisplayMode,
    backgroundOpacity,
    onInspect,
    onViewportChange,
    onCaptureReady,
  }: {
    mapLabel: string;
    focusPoint?: { lon: number; lat: number } | null;
    imageUrl: string;
    flowVectorData: FlowVectorFeatureCollection | null;
    flowDisplayMode?: "vectors" | "particles" | null;
    backgroundOpacity: number;
    onInspect: (lon: number, lat: number) => void;
    onViewportChange?: (viewport: { west: number; south: number; east: number; north: number }, zoom: number) => void;
    onCaptureReady?: (capture: ((url?: string) => Promise<HTMLCanvasElement>) | null) => void;
  }) => {
    useEffect(() => {
      if (resultMapMockState.captureError) onCaptureReady?.(async () => { throw new Error("fixture capture failed"); });
    }, [onCaptureReady]);
    useEffect(() => {
      if (resultMapMockState.reportViewport) {
        onViewportChange?.({ west: 139.7, south: 35.6, east: 139.8, north: 35.7 }, 18);
      }
    }, [onViewportChange]);
    return (
    <div
      data-testid="result-map"
      data-focus-point={JSON.stringify(focusPoint)}
      data-image-url={imageUrl}
      data-flow-arrow-count={String(flowVectorData?.metadata.arrow_count ?? 0)}
      data-flow-time-index={String(flowVectorData?.features[0]?.properties.time_index ?? "")}
      data-flow-display-mode={flowDisplayMode ?? "off"}
      data-background-opacity={String(backgroundOpacity)}
    >
      {mapLabel}
      <button type="button" onClick={() => onInspect(139.75, 35.65)}>地点を確認</button>
    </div>
    );
  },
}));

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
  max_depth_summary: {
    global_max_depth_m: 1.25,
  },
  grid_level_summary: {
    "1m": 1200000,
  },
  depth_legend: [
    { label: "0.00–0.05 m", min_m: 0, max_m: 0.05, color: "#C6E8FF" },
    { label: "0.05–0.10 m", min_m: 0.05, max_m: 0.1, color: "#5BB1FF" },
    { label: "0.10–0.20 m", min_m: 0.1, max_m: 0.2, color: "#406EDE" },
    { label: "0.20–0.40 m", min_m: 0.2, max_m: 0.4, color: "#7E52C4" },
    { label: "0.40–0.80 m", min_m: 0.4, max_m: 0.8, color: "#C4418B" },
    { label: "0.80–1.60 m", min_m: 0.8, max_m: 1.6, color: "#6D1B4A" },
    { label: "1.60 m以上", min_m: 1.6, max_m: null, color: "#491234" },
  ],
  elevation_legend: [
    { label: "1.00–1.38 m", min_m: 1, max_m: 1.375, color: "#313695" },
    { label: "1.38–1.75 m", min_m: 1.375, max_m: 1.75, color: "#4575B4" },
    { label: "1.75–2.12 m", min_m: 1.75, max_m: 2.125, color: "#00B7D4" },
    { label: "2.12–2.50 m", min_m: 2.125, max_m: 2.5, color: "#1A9850" },
    { label: "2.50–2.88 m", min_m: 2.5, max_m: 2.875, color: "#FDE725" },
    { label: "2.88–3.25 m", min_m: 2.875, max_m: 3.25, color: "#FDAE61" },
    { label: "3.25–3.62 m", min_m: 3.25, max_m: 3.625, color: "#F46D43" },
    { label: "3.62–4.00 m", min_m: 3.625, max_m: 4, color: "#A50026" },
  ],
  provider_summary: {
    building_provider: "osm",
    road_provider: "osm",
    warnings: ["PLATEAUからOSMへフォールバックしました。"],
  },
  engine_summary: {
    sfincs_version: "2.4.0 Galibier",
    hydromt_sfincs_version: "2.0.0rc3",
  },
  run_summary: {
    application_version: "0.1.0",
    requested_accuracy_mode: "full_1m",
    rainfall_source: { mode: "constant", intensity_mm_per_h: 10 },
    elevation_provider_counts: { gsi_1m: 1200000 },
    elevation_source_summary: { primary: "GSI" },
    manning_defaults: { general: 0.03, road: 0.02 },
    boundary_policy: "closed boundary",
    roof_rain_mass_diagnostic: { relative_mass_error: 0 },
  },
  no_data_policy: "inactive cells are NaN",
  limitations: {
    infiltration_modelled: false,
    sewer_network_modelled: false,
    storm_drain_inlets_modelled: false,
    building_interior_modelled: false,
    spatial_meteorological_rainfall_modelled: false,
    river_stage_boundary_modelled: false,
    coastal_tide_surge_modelled: false,
    grade_separated_transport_modelled: false,
    official_forecast: false,
  },
};

describe("ResultPanel", () => {
  it("keeps vector screen density stable across integer zoom levels", () => {
    expect(strideForZoom(21)).toBe(1);
    expect(strideForZoom(20)).toBe(2);
    expect(strideForZoom(19)).toBe(3);
    expect(strideForZoom(18)).toBe(6);
    expect(strideForZoom(17)).toBe(12);
    expect(strideForZoom(16)).toBe(24);
    expect(strideForZoom(10)).toBe(1536);
    expect(strideForZoom(8)).toBe(4096);
  });

  it("does not prefetch unselected static layers or the complete depth timeline", async () => {
    const fetchSpy = vi.spyOn(window, "fetch").mockResolvedValue(new Response());
    try {
      render(<ResultPanel runId="run-1" metadata={metadata} rainfallSummary="10 mm/h" onNewAnalysis={vi.fn()} />);
      await waitFor(() => expect(screen.getByRole("button", { name: "最大浸水深" })).toHaveAttribute("aria-pressed", "true"));
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it("maps late sparse slider positions to retained output indices", () => {
    render(<ResultPanel runId="run-1" metadata={{ ...metadata, available_time_indices: [0, 3, 3, 99] }} rainfallSummary="10 mm/h" onNewAnalysis={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "時刻別の浸水深" }));
    expect(screen.getByRole("slider", { name: "結果時刻" })).toHaveAttribute("max", "1");
    fireEvent.change(screen.getByRole("slider", { name: "結果時刻" }), { target: { value: "1" } });
    expect(screen.getByTestId("result-map")).toHaveAttribute("data-image-url", "/api/v1/runs/run-1/layers/depth.png?time_index=3&display_revision=adaptive-area-v1");
  });

  beforeEach(() => {
    resultMapMockState.reportViewport = true;
    resultMapMockState.captureError = false;
    vi.mocked(inspectResult).mockReset();
    vi.mocked(getFlowVectors).mockReset();
    vi.mocked(startResultExtrema).mockReset();
    vi.mocked(getResultExtremaProgress).mockReset();
    vi.mocked(startResultExtrema).mockResolvedValue({ ...completeExtrema, depth: [], speed: [] });
    vi.mocked(startResultEnergy).mockReset();
    vi.mocked(getResultEnergy).mockReset();
    vi.mocked(getResultEnergyPoint).mockReset();
    vi.mocked(startResultEnergy).mockResolvedValue({status:"complete", progress_percent:100, processed_frames:4, total_frames:4, energy:[], aggregation_buffer_bytes:1920000, method:"境界流出積算"});
    vi.mocked(getResultEnergyPoint).mockResolvedValue({lon_deg:139.75, lat_deg:35.65, has_data:false, row:0, column:0, cell_area_m2:1, through_time_index:3, through_time_value:metadata.time_values[3]});
    Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      value: null,
    });
  });

  it("cycles ten deepest locations with their output times and reuses the ranking", async () => {
    const depth = Array.from({ length: 10 }, (_, index) => ({ rank: index + 1, cell_index: index,
      lon_deg: 139.76 + index * 0.00001, lat_deg: 35.66, time_index: index % 2 === 0 ? 0 : 3,
      time_value: metadata.time_values[index % 2 === 0 ? 0 : 3], depth_m: 10 - index,
      speed_mps: 2, cell_area_m2: 0.25 }));
    vi.mocked(startResultExtrema).mockResolvedValue({ ...completeExtrema, depth, speed: [] });
    vi.mocked(inspectResult).mockResolvedValue({ has_data: false, lon_deg: 139.76, lat_deg: 35.66, row: 0, column: 0 } as Awaited<ReturnType<typeof inspectResult>>);
    render(<ResultPanel runId="run-1" metadata={{ ...metadata, max_depth_summary: { ...metadata.max_depth_summary, global_max_lon_deg: 139.76, global_max_lat_deg: 35.66 } }} rainfallSummary="10 mm/h" onNewAnalysis={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "時刻別の浸水深" }));
    fireEvent.click(screen.getByRole("button", { name: "次の時刻" }));
    const button = screen.getByRole("button", { name: "最大深度箇所" });
    expect(button.previousElementSibling).toHaveTextContent("時刻別の浸水深");
    fireEvent.click(button);
    await waitFor(() => expect(inspectResult).toHaveBeenCalledWith("run-1", 139.76, 35.66, 0, expect.any(AbortSignal)));
    expect(screen.getByTestId("result-map")).toHaveAttribute("data-focus-point", JSON.stringify({ lon: 139.76, lat: 35.66 }));
    expect(screen.getByRole("button", { name: "時刻別の浸水深" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("point-rank")).toHaveTextContent("第1位 / 10地点");
    for (let index = 1; index <= 10; index++) {
      fireEvent.click(button);
      await waitFor(() => expect(screen.getByTestId("point-rank")).toHaveTextContent(`第${index % 10 + 1}位`));
      expect(screen.getByTestId("result-map")).toHaveAttribute("data-image-url", expect.stringContaining(`time_index=${index % 2 === 0 ? 0 : 3}`));
    }
    expect(screen.getByTestId("result-map")).toHaveAttribute("data-focus-point", JSON.stringify({ lon: 139.76, lat: 35.66 }));
    expect(startResultExtrema).toHaveBeenCalledTimes(1);
  });

  it("disables ranking navigation when output data are unavailable", () => {
    render(<ResultPanel runId="run-1" metadata={{ ...metadata, available_time_indices: [] }} rainfallSummary="10 mm/h" onNewAnalysis={vi.fn()} />);
    expect(screen.getByRole("button", { name: "最大深度箇所" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "最高速度箇所" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "最高総エネルギー箇所" })).toBeDisabled();
  });

  it.each(["最大深度", "最高速度"])("shows actual %s progress and shares the completed ranking", async (metric) => {
    const queued = { status: "queued" as const, progress_percent: 0, processed_frames: 0,
      total_frames: 8, depth: [], speed: [] };
    const entry = { rank: 1, cell_index: 1, lon_deg: 139.76, lat_deg: 35.66, time_index: 3,
      time_value: metadata.time_values[3], depth_m: 1, speed_mps: 2, cell_area_m2: .25 };
    vi.mocked(startResultExtrema).mockResolvedValue(queued);
    vi.mocked(getResultExtremaProgress).mockResolvedValueOnce({ ...queued, status: "running", progress_percent: 50, processed_frames: 4 })
      .mockResolvedValueOnce({ ...queued, status: "complete", progress_percent: 100, processed_frames: 8, depth: [entry], speed: [entry] });
    vi.mocked(inspectResult).mockResolvedValue({ has_data: false, lon_deg: 139.76, lat_deg: 35.66, row: 0, column: 0 });
    render(<ResultPanel runId="run-1" metadata={metadata} rainfallSummary="rain" onNewAnalysis={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: `${metric}箇所` }));
    await screen.findByText(`${metric}集計中 50%（4 / 8 区画×時刻）`);
    expect(screen.getByRole("progressbar", { name: "水深・流速集計の進捗" })).toHaveAttribute("value", "50");
    expect(screen.getByRole("button", { name: "最大深度箇所" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "最高速度箇所" })).toBeDisabled();
    await screen.findByText("最大深度・最高速度集計完了 100%");
    expect(screen.getByTestId("result-map")).toHaveAttribute("data-image-url", expect.stringContaining("time_index=3"));
    fireEvent.click(screen.getByRole("button", { name: `${metric === "最大深度" ? "最高速度" : "最大深度"}箇所` }));
    await waitFor(() => expect(screen.getByTestId("point-rank")).toHaveTextContent(metric === "最大深度" ? "最高流速" : "最高水深"));
    expect(startResultExtrema).toHaveBeenCalledTimes(1);
    expect(getResultExtremaProgress).toHaveBeenCalledTimes(2);
  });

  it("does not report failed extrema jobs as complete and allows retry", async () => {
    vi.mocked(startResultExtrema).mockResolvedValueOnce({ ...completeExtrema, status: "failed", progress_percent: 50,
      depth: [], speed: [], error: "水深集計失敗" });
    render(<ResultPanel runId="run-1" metadata={metadata} rainfallSummary="rain" onNewAnalysis={vi.fn()} />);
    const button = screen.getByRole("button", { name: "最大深度箇所" });
    fireEvent.click(button);
    await screen.findByText("Error: 水深集計失敗");
    expect(screen.queryByText("最大深度・最高速度集計完了 100%")).not.toBeInTheDocument();
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await screen.findByText("最大深度・最高速度集計完了 100%");
    expect(startResultExtrema).toHaveBeenCalledTimes(2);
  });

  it("shows actual energy progress and cycles ten completed locations without recalculating", async () => {
    const queued = { status: "queued" as const, progress_percent: 0, processed_frames: 0,
      total_frames: 4, energy: [], aggregation_buffer_bytes: 1920000, method: "境界流出積算" };
    const energy = Array.from({ length: 10 }, (_, index) => ({ rank: index + 1, cell_index: index,
      lon_deg: 139.76 + index * .00001, lat_deg: 35.66, time_index: 3, time_value: metadata.time_values[3],
      depth_m: 0, speed_mps: 0, cell_area_m2: 1, total_outflow_m3: 10, total_energy_j: 70000 - index }));
    vi.mocked(startResultEnergy).mockResolvedValue(queued);
    vi.mocked(getResultEnergy).mockResolvedValueOnce({ ...queued, status: "running", progress_percent: 50, processed_frames: 2 })
      .mockResolvedValueOnce({ ...queued, status: "complete", progress_percent: 100, processed_frames: 4, energy });
    vi.mocked(inspectResult).mockResolvedValue({ has_data: false, lon_deg: 139.76, lat_deg: 35.66, row: 0, column: 0 });
    render(<ResultPanel runId="run-1" metadata={metadata} rainfallSummary="rain" onNewAnalysis={vi.fn()} />);
    const button = screen.getByRole("button", { name: "最高総エネルギー箇所" });
    fireEvent.click(button);
    await screen.findByText("エネルギー集計中 50%（2 / 4時刻）");
    expect(screen.getByRole("progressbar")).toHaveAttribute("value", "50");
    expect(button).toBeDisabled();
    await screen.findByText("エネルギー集計完了 100%");
    expect(screen.getByTestId("point-rank")).toHaveTextContent("最高総エネルギー 第1位 / 10地点");
    expect(screen.getByRole("region", { name: "比較" })).toHaveTextContent("プリウス（1,400 kg）が時速36.0 km");
    expect(screen.getByRole("region", { name: "比較" })).toHaveTextContent("累積流出量 10.00 m³");
    expect(screen.getByTestId("result-map")).toHaveAttribute("data-image-url", expect.stringContaining("time_index=3"));
    for (let index = 1; index <= 10; index++) {
      fireEvent.click(button);
      await waitFor(() => expect(screen.getByTestId("point-rank")).toHaveTextContent(`第${index % 10 + 1}位`));
    }
    expect(startResultEnergy).toHaveBeenCalledTimes(1);
    expect(getResultEnergy).toHaveBeenCalledTimes(2);
  });

  it("shows energy failures without reporting completion", async () => {
    const failed = { status: "failed" as const, progress_percent: 33, processed_frames: 1,
      total_frames: 3, energy: [], aggregation_buffer_bytes: 0, method: "", error: "集計失敗" };
    vi.mocked(startResultEnergy).mockResolvedValueOnce(failed).mockResolvedValueOnce({ ...failed, status: "running", error: null });
    render(<ResultPanel runId="run-1" metadata={metadata} rainfallSummary="rain" onNewAnalysis={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "最高総エネルギー箇所" }));
    await screen.findByText("Error: 集計失敗");
    expect(screen.queryByText("エネルギー集計完了 100%")).not.toBeInTheDocument();
  });

  it("compares a clicked location outside the top ten using accumulated energy", async () => {
    vi.mocked(inspectResult).mockResolvedValue({has_data:true, lon_deg:139.75, lat_deg:35.65, row:1, column:1,
      depth_m:999, speed_mps:99, grid_resolution_m:.5});
    vi.mocked(getResultEnergyPoint).mockResolvedValue({has_data:true, lon_deg:139.75, lat_deg:35.65, row:1, column:1, cell_area_m2:1,
      total_energy_j:75, total_outflow_m3:5, rank:11, through_time_index:3, through_time_value:metadata.time_values[3]});
    render(<ResultPanel runId="run-1" metadata={metadata} rainfallSummary="rain" onNewAnalysis={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", {name:"地点を確認"}));
    const comparison = await screen.findByRole("region", {name:"比較"});
    expect(comparison).toHaveTextContent("力士（150 kg）が秒速1.00 m（100 m走換算: 100.0秒）");
    expect(comparison).toHaveTextContent("累積流出量 5.00 m³");
    expect(comparison).toHaveTextContent("総エネルギー 第11位");
    expect(screen.queryByTestId("point-rank")).not.toBeInTheDocument();
    expect(startResultExtrema).not.toHaveBeenCalled();
    expect(getResultEnergyPoint).toHaveBeenCalledWith("run-1", 139.75, 35.65, expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button",{name:"時刻別の浸水深"}));
    fireEvent.click(screen.getByRole("button",{name:"次の時刻"}));
    await waitFor(() => expect(inspectResult).toHaveBeenLastCalledWith("run-1",139.75,35.65,3,expect.any(AbortSignal)));
    expect(getResultEnergyPoint).toHaveBeenCalledTimes(1);
  });

  it("cycles speed independently, shows km/h and small-energy comparison, and clears it on manual inspection", async () => {
    const entry = { rank: 1, cell_index: 1, lon_deg: 139.76, lat_deg: 35.66, time_index: 3,
      time_value: metadata.time_values[3], depth_m: 0.6, speed_mps: 1, cell_area_m2: 0.25 };
    const entries = Array.from({ length: 10 }, (_, index) => ({ ...entry, rank: index + 1, cell_index: index + 1,
      lon_deg: entry.lon_deg + index * 0.00001, time_index: index % 2 === 0 ? 3 : 0 }));
    vi.mocked(startResultExtrema).mockResolvedValue({ ...completeExtrema, depth: [entry], speed: entries });
    vi.mocked(inspectResult).mockResolvedValue({ has_data: false, lon_deg: 139.76, lat_deg: 35.66, row: 0, column: 0 });
    render(<ResultPanel runId="run-1" metadata={metadata} rainfallSummary="rain" onNewAnalysis={vi.fn()} />);
    const button = screen.getByRole("button", { name: "最高速度箇所" });
    for (let index = 0; index <= 10; index++) {
      fireEvent.click(button);
      await waitFor(() => expect(screen.getByTestId("point-rank")).toHaveTextContent(`最高流速 第${index % 10 + 1}位`));
      expect(screen.getByTestId("result-map")).toHaveAttribute("data-image-url", expect.stringContaining(`time_index=${index % 2 === 0 ? 3 : 0}`));
    }
    expect(screen.getByRole("region", { name: "比較" })).toHaveTextContent("力士（150 kg）が秒速1.00 m（100 m走換算: 100.0秒）");
    fireEvent.click(screen.getByRole("button", { name: "最大深度箇所" }));
    await waitFor(() => expect(screen.getByTestId("point-rank")).toHaveTextContent("最高水深 第1位"));
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByTestId("point-rank")).toHaveTextContent("最高流速 第2位"));
    expect(startResultExtrema).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "地点を確認" }));
    expect(screen.queryByTestId("point-rank")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "比較" })).not.toBeInTheDocument();
  });

  it("allows ranking retries after a failed request", async () => {
    vi.mocked(startResultExtrema).mockRejectedValueOnce(new Error("ranking failed"));
    render(<ResultPanel runId="run-1" metadata={metadata} rainfallSummary="rain" onNewAnalysis={vi.fn()} />);
    const button = screen.getByRole("button", { name: "最高速度箇所" });
    fireEvent.click(button);
    await screen.findByText("Error: ranking failed");
    expect(button).toBeEnabled();
    fireEvent.click(button);
    await screen.findByText("流速の順位を表示できる地点がありません。");
    expect(startResultExtrema).toHaveBeenCalledTimes(2);
  });

  it("does not enable flow vectors before the current map zoom is known", () => {
    resultMapMockState.reportViewport = false;

    render(
      <ResultPanel
        runId="run-1"
        metadata={metadata}
        rainfallSummary="10 mm/h × 1分"
        onNewAnalysis={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "流れベクトル" })).toBeDisabled();
    expect(getFlowVectors).not.toHaveBeenCalled();
  });

  it("shows native point values including maximum time", async () => {
    vi.mocked(inspectResult).mockResolvedValue({
      lon_deg: 139.75,
      lat_deg: 35.65,
      has_data: true,
      row: 10,
      column: 20,
      time_index: null,
      time_value: null,
      depth_m: null,
      max_depth_m: 0.42,
      max_time_index: 0,
      max_time_value: "2026-01-01T00:30:00",
      terrain_elevation_m: 12.3,
      grid_resolution_m: 1,
    });

    render(
      <ResultPanel
        runId="run-1"
        metadata={metadata}
        rainfallSummary="10 mm/h × 1分"
        onNewAnalysis={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "地点を確認" }));

    expect(await screen.findByText("0.420 m")).toBeVisible();
    expect(screen.getByText("最大時刻")).toBeVisible();
    expect(screen.getByText("00:30")).toBeVisible();
    expect(screen.getByText("12.300 m")).toBeVisible();
    expect(screen.getByText("1.000 m")).toBeVisible();
  });

  it("formats numeric NetCDF time values as elapsed seconds", () => {
    render(
      <ResultPanel
        runId="run-1"
        metadata={{
          ...metadata,
          available_time_indices: [0, 1],
          time_values: ["0", "60"],
        }}
        rainfallSummary="10 mm/h × 1分"
        onNewAnalysis={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "時刻別の浸水深" }));
    fireEvent.click(screen.getByRole("button", { name: "次の時刻" }));

    expect(screen.getByText("現在: 00:01")).toBeVisible();
  });

  it.each([[6, 301, "301秒"], [299, 600, "600秒"], [300, 600, "10分"]] as const)("uses casting duration %s for result units", (casting, total, label) => {
    render(<ResultPanel runId="run-magic" metadata={{ ...metadata,
      available_time_indices: [0, 1, 2], time_values: ["0", "6", String(total)],
      run_summary: { ...metadata.run_summary, rainfall_source: {
        kind: "water_magic", configuration_json: JSON.stringify({ spell_id:"gw2-healingrain", casting_seconds: casting, relaxation_seconds: total - casting, volume_m3: .039 }),
      } },
    }} rainfallSummary="fallback" onNewAnalysis={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "時刻別の浸水深" }));
    fireEvent.click(screen.getByRole("button", { name: "次の時刻" }));
    fireEvent.click(screen.getByRole("button", { name: "次の時刻" }));
    expect(screen.getByText(`現在: ${label}`)).toBeVisible();
    expect(screen.getByText("緩和中")).toBeVisible();
  });

  it("passes the selected actual output index to native inspection on the time layer", async () => {
    vi.mocked(inspectResult)
      .mockResolvedValueOnce({
      lon_deg: 139.75,
      lat_deg: 35.65,
      has_data: true,
      row: 10,
      column: 20,
      time_index: 0,
      time_value: "2026-01-01T00:00:00",
      depth_m: 0.02,
      speed_mps: 1,
      max_depth_m: 0.42,
      max_time_index: 3,
      max_time_value: "2026-01-01T00:30:00",
      terrain_elevation_m: 12.3,
      grid_resolution_m: 1,
      })
      .mockResolvedValueOnce({
        lon_deg: 139.75,
        lat_deg: 35.65,
        has_data: true,
        row: 10,
        column: 20,
        time_index: 3,
        time_value: "2026-01-01T00:30:00",
        depth_m: 0.12,
        speed_mps: 2,
        max_depth_m: 0.42,
        max_time_index: 3,
        max_time_value: "2026-01-01T00:30:00",
        terrain_elevation_m: 12.3,
        grid_resolution_m: 1,
      });

    render(
      <ResultPanel
        runId="run-1"
        metadata={metadata}
        rainfallSummary="10 mm/h × 1分"
        onNewAnalysis={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "時刻別の浸水深" }));
    fireEvent.click(screen.getByRole("button", { name: "地点を確認" }));

    expect(await screen.findByText("0.02 m")).toBeVisible();
    expect(screen.getByText("1.00 m/s")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "次の時刻" }));
    expect(await screen.findByText("0.12 m")).toBeVisible();
    expect(screen.getByText("2.00 m/s")).toBeVisible();
    expect(screen.getByText("現在水深")).toBeVisible();
    expect(vi.mocked(inspectResult).mock.calls[1]?.slice(0, 4)).toEqual([
      "run-1",
      139.75,
      35.65,
      3,
    ]);
  });

  it("shows no-data distinctly from zero depth", async () => {
    vi.mocked(inspectResult).mockResolvedValue({
      lon_deg: 139.75,
      lat_deg: 35.65,
      has_data: false,
      row: 10,
      column: 20,
      time_index: null,
      time_value: null,
      depth_m: null,
      max_depth_m: null,
      max_time_index: null,
      max_time_value: null,
      terrain_elevation_m: null,
      grid_resolution_m: null,
    });

    render(
      <ResultPanel
        runId="run-1"
        metadata={metadata}
        rainfallSummary="10 mm/h × 1分"
        onNewAnalysis={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "地点を確認" }));

    expect(
      await screen.findByText("この地点には解析データがありません。"),
    ).toBeVisible();
    expect(screen.queryByText("0.000 m")).not.toBeInTheDocument();
  });

  it("shows maximum depth first, the exact six-band legend, and complete provenance/limitations", () => {
    render(
      <ResultPanel
        runId="run-1"
        metadata={metadata}
        rainfallSummary="10 mm/h × 1分"
        onNewAnalysis={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "解析結果" })).toBeVisible();
    expect(screen.getByTestId("result-map")).toHaveTextContent("最大浸水深の地図");

    const summary = screen.getByText("結果概要").closest("details");
    expect(summary).not.toHaveAttribute("open");
    expect(screen.getByText("1.250 m")).not.toBeVisible();

    const legend = screen.getByLabelText("浸水深の凡例");
    expect(legend.closest(".result-sidebar")).not.toBeNull();

    for (const label of [
      "0.00–0.05 m",
      "0.05–0.10 m",
      "0.10–0.20 m",
      "0.20–0.40 m",
      "0.40–0.80 m",
      "0.80–1.60 m",
      "1.60 m以上",
    ]) {
      expect(screen.getByText(label)).toBeVisible();
    }

    fireEvent.click(screen.getByText("結果概要"));
    expect(screen.getByText("1.250 m")).toBeVisible();
    expect(screen.getAllByText("osm").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("浸透は考慮していません。")).toBeVisible();
    expect(
      screen.getByText("陸橋・トンネル・高速道路下などの立体交差を通る水の流れは考慮していません。"),
    ).toBeVisible();

    fireEvent.click(screen.getByText("解析条件と出典"));
    expect(screen.getByText("Application: 0.1.0")).toBeVisible();
    expect(screen.getByText(/Rainfall:/)).toBeVisible();
    expect(screen.getByText(/Elevation:/)).toBeVisible();
    expect(screen.getByText(/Elevation provider counts:/)).toBeVisible();
    expect(screen.getByText(/Manning:/)).toBeVisible();
    expect(screen.getByText("Boundary: closed boundary")).toBeVisible();
    expect(screen.getByText(/Roof-rain mass diagnostic:/)).toBeVisible();
    expect(screen.getByText("HydroMT-SFINCS: 2.0.0rc3")).toBeVisible();
  });

  it("uses saved rainfall metadata instead of the current setup inputs", () => {
    render(
      <ResultPanel
        runId="run-1"
        metadata={{
          ...metadata,
          run_summary: {
            ...metadata.run_summary,
            rainfall_source: {
              kind: "constant",
              intensity_mm_per_h: "123.5",
              duration_minutes: "60",
            },
          },
        }}
        rainfallSummary="150 mm/h × 20分"
        onNewAnalysis={vi.fn()}
      />,
    );

    expect(screen.getByText("123.5 mm/h × 60分 / 高精度 — 全域1 m")).toBeVisible();
    expect(screen.queryByText(/150 mm\/h × 20分/)).not.toBeInTheDocument();
  });

  it("switches only among actual time indices and exposes grid-resolution controls", () => {
    render(
      <ResultPanel
        runId="run-1"
        metadata={metadata}
        rainfallSummary="10 mm/h × 1分"
        onNewAnalysis={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "時刻別の浸水深" }));
    expect(screen.getByTestId("result-map")).toHaveTextContent("時刻別浸水深の地図");
    expect(screen.getByRole("slider", { name: "結果時刻" })).toBeVisible();
    expect(screen.getByText("現在: 00:00")).toBeVisible();
    expect(screen.getByTestId("result-map")).toHaveAttribute(
      "data-image-url",
      "/api/v1/runs/run-1/layers/depth.png?time_index=0&display_revision=adaptive-area-v1",
    );

    fireEvent.click(screen.getByRole("button", { name: "次の時刻" }));
    expect(screen.getByText("現在: 00:30")).toBeVisible();
    expect(screen.getByTestId("result-map")).toHaveAttribute(
      "data-image-url",
      "/api/v1/runs/run-1/layers/depth.png?time_index=3&display_revision=adaptive-area-v1",
    );

    fireEvent.click(screen.getByRole("button", { name: "計算格子" }));
    expect(screen.getByTestId("result-map")).toHaveTextContent("計算格子解像度の地図");
    expect(screen.getByTestId("result-map")).toHaveAttribute(
      "data-image-url",
      "/api/v1/runs/run-1/layers/grid-resolution.png?display_revision=half-metre-grid-v2",
    );
    expect(screen.getByText("実計算格子: 1 m")).toBeVisible();
    expect(screen.getByText("32 m")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "標高" }));
    expect(screen.getByTestId("result-map")).toHaveTextContent("標高の地図");
    expect(screen.getByTestId("result-map")).toHaveAttribute(
      "data-image-url",
      "/api/v1/runs/run-1/layers/elevation.png?display_revision=adaptive-area-v1",
    );
    expect(screen.getByLabelText("標高の凡例")).toBeVisible();
    expect(screen.getByText("1.00–1.38 m")).toBeVisible();
    expect(screen.getByText("3.62–4.00 m")).toBeVisible();
  });

  it.each([false, true])("selects the center maximum and uses the correct flow spacing (magic=%s)", async (magic) => {
    vi.mocked(inspectResult).mockResolvedValue({
      lon_deg: 139.75, lat_deg: 35.65, has_data: true,
      max_time_index: 3, max_time_value: "2026-01-01T00:30:00",
    } as Awaited<ReturnType<typeof inspectResult>>);
    const emptyFlow: FlowVectorFeatureCollection = {
      type: "FeatureCollection",
      features: [],
      metadata: {
        speed_unit: "m/s",
        min_speed_mps: 0.001,
        sample_stride_cells: 8,
        arrow_length_m: 6.4,
        arrow_count: 0,
        sampling_method: "max-speed-wet-cell-per-block",
        viewport: { west: 139.7, south: 35.6, east: 139.8, north: 35.7 },
      },
    };
    const visibleFlow: FlowVectorFeatureCollection = {
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
        speed_scale: { breaks: [0, 0.001, 0.25, 0.5, 0.75, 1, 3, 100], class_count: 7, mode: "hybrid" },
        min_speed_mps: 0.001,
        sample_stride_cells: 8,
        arrow_length_m: 6.4,
        arrow_count: 1,
        sampling_method: "max-speed-wet-cell-per-block",
        viewport: { west: 139.7, south: 35.6, east: 139.8, north: 35.7 },
      },
    };
    vi.mocked(getFlowVectors).mockImplementation(async (_runId, timeIndex) => (
      timeIndex === 0 ? emptyFlow : visibleFlow
    ));

    render(
      <ResultPanel
        runId="run-1"
        metadata={magic ? { ...metadata, run_summary: { ...metadata.run_summary, rainfall_source: {
          kind: "water_magic", configuration_json: JSON.stringify({ spell_id: "gw2-healingrain", casting_seconds: 6, relaxation_seconds: 60, volume_m3: .117 }),
        } } } : metadata}
        rainfallSummary="10 mm/h × 1分"
        onNewAnalysis={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "流れベクトル" }));

    expect(await screen.findByText(magic ? "現在: 1800秒" : "現在: 00:30")).toBeVisible();
    expect(screen.getByRole("button", { name: "時刻別の浸水深" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("result-map")).toHaveAttribute(
      "data-image-url",
      "/api/v1/runs/run-1/layers/depth.png?time_index=3&display_revision=adaptive-area-v1",
    );
    await waitFor(() => {
      expect(screen.getByTestId("result-map")).toHaveAttribute("data-flow-arrow-count", "1");
    });
    expect(screen.getByTestId("result-map")).toHaveAttribute("data-flow-time-index", "3");
    expect(getFlowVectors).toHaveBeenCalledTimes(1); // Paused: no next-frame request.
    expect(screen.getByLabelText("流速の凡例")).toBeVisible();
    expect(screen.getByLabelText("流速の凡例")).toHaveTextContent("0.001");
    expect(screen.getByLabelText("流速の凡例")).toHaveTextContent("m/s");
    expect(screen.getByLabelText("流速の凡例")).toHaveTextContent("3–100 m/s");
    expect(screen.getByLabelText("流速の凡例").querySelectorAll("i")).toHaveLength(7);
    expect(screen.getByText("矢印の向き: 流向 / 色: 流速")).toBeVisible();
    expect(screen.getByText("矢印: 1本")).toBeVisible();
    expect(screen.getByTestId("result-map")).toHaveAttribute("data-flow-display-mode", "vectors");
    expect(vi.mocked(getFlowVectors)).toHaveBeenCalledWith(
      "run-1",
      3,
      expect.any(Object),
      6,
      expect.any(AbortSignal),
      false,
    );

    fireEvent.change(screen.getByRole("slider", { name: "結果時刻" }), { target: { value: "0" } });
    expect(await screen.findByText(magic ? "現在: 0秒" : "現在: 00:00")).toBeVisible();
    if (magic) {
      expect(screen.queryByLabelText("ベクトル間隔")).not.toBeInTheDocument();
      await waitFor(() => expect(getFlowVectors).toHaveBeenCalledWith("run-1", 0, expect.any(Object), 6, expect.any(AbortSignal), false));
    }

    fireEvent.click(screen.getByRole("button", { name: "粒子フロー" }));
    expect(screen.getByRole("button", { name: "粒子フロー" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(magic ? "現在: 0秒" : "現在: 00:00")).toBeVisible();
    expect(screen.getByTestId("result-map")).toHaveAttribute("data-flow-display-mode", "particles");
    expect(screen.getByText(magic ? "現在: 0秒" : "現在: 00:00")).toBeVisible();
    expect(screen.getByText(/粒子の進行方向: 補間したベクトル場/)).toBeVisible();
    expect(screen.getByText(/寿命: 最低15ベクトル間隔/)).toBeVisible();
    expect(screen.getByText(/開始位相: 4群/)).toBeVisible();
    await waitFor(() => expect(getFlowVectors).toHaveBeenCalledWith("run-1", 0, expect.any(Object), 6, expect.any(AbortSignal), true));
    expect(await screen.findByText("粒子の発生点: 0個")).toBeVisible();
    expect(screen.getByText("この時刻には表示可能な流れがありません。")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "流れベクトル" }));
    expect(screen.getByText(magic ? "現在: 0秒" : "現在: 00:00")).toBeVisible();

    fireEvent.change(screen.getByRole("slider", { name: "結果時刻" }), { target: { value: "1" } });
    await waitFor(() => {
      expect(screen.getByTestId("result-map")).toHaveAttribute("data-flow-time-index", "3");
    });
    expect(inspectResult).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "最大浸水深" }));
    expect(screen.getByRole("button", { name: "流れベクトル" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("result-map")).toHaveTextContent("最大浸水深の地図");
  });

  it("keeps the selected time when the timeline was already opened", async () => {
    render(<ResultPanel runId="run-1" metadata={metadata} rainfallSummary="rain" onNewAnalysis={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "時刻別の浸水深" }));
    fireEvent.click(screen.getByRole("button", { name: "粒子フロー" }));
    await waitFor(() => expect(getFlowVectors).toHaveBeenCalled());
    expect(screen.getByText("現在: 00:00")).toBeVisible();
    expect(inspectResult).not.toHaveBeenCalled();
  });

  it("reports a failed GIF frame capture and restores the export control", async () => {
    resultMapMockState.captureError = true;
    render(<ResultPanel runId="run-1" metadata={metadata} rainfallSummary="rain" onNewAnalysis={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "時刻別の浸水深" }));
    fireEvent.click(screen.getByRole("button", { name: "GIF" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("fixture capture failed");
    expect(screen.getByRole("button", { name: "GIF" })).toBeEnabled();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("keeps layer controls and the timeline inside the fullscreen region", async () => {
    render(
      <ResultPanel
        runId="run-1"
        metadata={metadata}
        rainfallSummary="10 mm/h × 1分"
        onNewAnalysis={vi.fn()}
      />,
    );

    const region = screen.getByTestId("result-focus-region");
    const requestFullscreen = region.requestFullscreen as ReturnType<typeof vi.fn>;

    expect(region.querySelector('[aria-label="結果レイヤー"]')).not.toBeNull();
    for (const label of ["最大浸水深", "時刻別の浸水深", "計算格子", "標高", "流れベクトル"]) {
      expect(region.querySelector(`button[aria-pressed]`)).not.toBeNull();
      expect(screen.getByRole("button", { name: label })).toBeVisible();
    }
    const layerButtons = Array.from(
      region.querySelectorAll('[aria-label="結果レイヤー"] button'),
      (button) => button.textContent?.trim(),
    );
    expect(layerButtons).toEqual(["最大浸水深", "時刻別の浸水深", "最大深度箇所", "最高速度箇所", "最高総エネルギー箇所", "流れベクトル", "粒子フロー", "計算格子", "標高"]);

    fireEvent.click(screen.getByRole("button", { name: "地図を全画面表示" }));
    expect(requestFullscreen).toHaveBeenCalledTimes(1);

    Object.defineProperty(document, "fullscreenElement", {
      configurable: true,
      value: region,
    });
    fireEvent(document, new Event("fullscreenchange"));

    expect(await screen.findByRole("slider", { name: "結果時刻" })).toBeVisible();
    expect(region.querySelector('[aria-label="結果時刻"]')).not.toBeNull();
    expect(region.querySelector('[aria-label="浸水深の凡例"]')).not.toBeNull();
    expect(region.querySelector(".result-point-panel")).not.toBeNull();
    expect(region.querySelector(".result-map-panel")).not.toBeNull();
    expect(region.querySelector(".result-sidebar-extra")).not.toBeNull();
    expect(screen.getByRole("button", { name: "全画面表示を終了" })).toBeVisible();
  });

  it("shows vector unavailability for historical runs without stored velocities", () => {
    render(
      <ResultPanel
        runId="run-1"
        metadata={{ ...metadata, flow_vectors_available: false }}
        rainfallSummary="10 mm/h × 1分"
        onNewAnalysis={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "流れベクトル" })).toBeDisabled();
    expect(screen.getByText("この解析には流れベクトルデータがありません。")).toBeVisible();
  });
});

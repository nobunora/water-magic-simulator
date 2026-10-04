import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import SmokeApp from "./dev/SmokeApp";
import {
  createElevationPreview,
  createRun,
  getAppConfig,
  getHealth,
  getRecentRainfallRanking,
  getResultMetadata,
  getRun,
  importResult,
  openDemoResult,
  searchLocation,
  type ResultMetadataResponse,
} from "./api/client";

vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return {
    ...actual,
    getHealth: vi.fn(),
    getAppConfig: vi.fn(),
    getRecentRainfallRanking: vi.fn(),
    createElevationPreview: vi.fn(),
    createRun: vi.fn(),
    getRun: vi.fn(),
    cancelRun: vi.fn(),
    getResultMetadata: vi.fn(),
    importResult: vi.fn(),
    openDemoResult: vi.fn(),
    inspectResult: vi.fn(),
    searchLocation: vi.fn(),
  };
});

vi.mock("./dev/SetupMap", () => ({
  default: ({
    area,
    disabled,
    onSelect,
  }: {
    area: { width_m: number } | null;
    disabled: boolean;
    onSelect: (lon: number, lat: number) => void;
  }) => (
    <div
      data-testid="setup-map"
      data-area-width={area?.width_m ?? ""}
      data-disabled={String(disabled)}
    >
      <button type="button" disabled={disabled} onClick={() => onSelect(139.8, 35.7)}>
        地図で地点選択
      </button>
    </div>
  ),
}));

vi.mock("./result/ResultMap", () => ({
  default: ({ mapLabel }: { mapLabel: string }) => (
    <div data-testid="result-map">{mapLabel}</div>
  ),
}));

const metadata: ResultMetadataResponse = {
  schema_version: "1",
  bounds: {
    west_deg: 139.764,
    south_deg: 35.679,
    east_deg: 139.770,
    north_deg: 35.684,
  },
  units: {
    water_depth: "m",
    terrain_elevation: "m",
    grid_resolution: "m",
  },
  available_time_indices: [0, 1],
  flow_vectors_available: false,
  time_values: ["2026-01-01T00:00:00", "2026-01-01T00:01:00"],
  max_depth_summary: { global_max_depth_m: 1.25 },
  grid_level_summary: { "1m": 250000 },
  depth_legend: [
    { label: "0.00–0.05 m", min_m: 0, max_m: 0.05, color: "#C6E8FF" },
  ],
  provider_summary: {
    building_provider: "osm",
    road_provider: "osm",
    warnings: [],
  },
  engine_summary: {
    sfincs_version: "2.4.0 Galibier",
    sfincs_build_sha256: "ABC",
    sfincs_engine_source: "SFINCS_BIN",
    hydromt_sfincs_version: "2.0.0rc3",
  },
  run_summary: {
    application_version: "0.1.0",
    requested_accuracy_mode: "full_1m",
    rainfall_source: { mode: "constant" },
    elevation_provider_counts: { gsi_1m: 250000 },
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

describe("local review UI", () => {
  it("expands the analysis extent with the largest effect dimension and exposes click help", () => {
    render(<SmokeApp magicMock />);
    fireEvent.click(screen.getByRole("button", {name:/小学校のプール/}));
    fireEvent.change(screen.getByLabelText("長さ (m)"), {target:{value:"80"}});
    expect(screen.getByLabelText("範囲")).toHaveValue("250");
    fireEvent.change(screen.getByLabelText("範囲"), {target:{value:"100"}});
    expect(screen.getByLabelText("範囲")).toHaveValue("250");
    fireEvent.change(screen.getByLabelText("長さ (m)"), {target:{value:"200"}});
    expect(screen.getByLabelText("長さ (m)")).toHaveValue(166.6);
    expect(screen.getByLabelText("範囲")).toHaveValue("250");
    expect(screen.getByRole("button", {name:"解析開始"})).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const explanation = screen.getByLabelText("長さ (m)の説明").closest("details")!;
    expect(explanation).not.toHaveAttribute("open");
    fireEvent.click(screen.getByLabelText("長さ (m)の説明"));
    expect(explanation).toBeInTheDocument();
  });
  it("calculates total water from rate and editable duration, preserving an observation override", async () => {
    render(<SmokeApp magicMock />);
    fireEvent.change(screen.getByLabelText("1秒あたりの水量 (m³/s)"), {target:{value:"12"}});
    fireEvent.change(screen.getByLabelText("魔法継続時間 (秒)"), {target:{value:"5"}});
    expect(screen.getByLabelText("観測時間 (秒)")).toHaveValue(50);
    fireEvent.change(screen.getByLabelText("観測時間 (秒)"), {target:{value:"20"}});
    expect(screen.getByText("60.0 m³（12.00 m³/s × 5秒）")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name:"解析開始"}));
    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createRun).mock.calls[0][0].water_magic).toMatchObject({volume_m3:60,generation_rate_m3ps:12,casting_seconds:5,relaxation_seconds:20,release_mode:"initial"});
  });
  it("replaces city samples with magic without submitting uniform-rain runs", async () => {
    vi.mocked(createRun).mockClear();
    render(<SmokeApp magicMock />);
    expect(screen.getByLabelText("範囲")).toHaveValue("100");
    expect(screen.getByRole("button", { name: /Dragon Quest VII Reimagined.*メイルストロム/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("1秒あたりの水量 (m³/s)")).toHaveValue(625);
    expect(screen.queryByText("四日市")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ファイルから読込" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: /Water II/ }));
    expect(screen.getByLabelText("緯度")).toHaveValue("35.681236");
    expect(screen.getByLabelText("経度")).toHaveValue("139.767125");
    expect(screen.getByLabelText("緯度")).not.toHaveAttribute("readonly");
    expect(screen.getByLabelText("経度")).not.toHaveAttribute("readonly");
    expect(screen.queryByLabelText("配置の緯度")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /GIFを/ })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "1. 条件" }).compareDocumentPosition(screen.getByRole("heading", { name: "2. 魔法の設定" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.change(screen.getByLabelText("方向 (°)"), { target: { value: "90" } });
    expect(screen.getByLabelText("方向 (°)")).toHaveValue(90);
    expect(screen.queryByLabelText("水の配置方法")).not.toBeInTheDocument();
    expect(screen.getByText("水の配置方法: 開始時に全量を配置")).toBeVisible();
    expect(screen.getByLabelText("1秒あたりの水量 (m³/s)")).toHaveValue(20);
    expect(screen.getByText("2.0 mm")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("観測時間 (秒)"), { target: { value: "90" } });
    expect(screen.getByText("94秒（魔法継続 4秒 + 観測 90秒）")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("魔法継続時間 (秒)"), { target: { value: "0" } });
    expect(screen.getByLabelText("魔法継続時間 (秒)")).toHaveValue(1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const start = screen.getByRole("button", { name: "解析開始" });
    expect(start).toBeEnabled();
    expect(createRun).not.toHaveBeenCalled();
  });
  it("submits second-based magic independently of rainfall and accepts coordinate edits", async () => {
    vi.mocked(createRun).mockClear();
    render(<SmokeApp magicMock />);
    fireEvent.click(screen.getByRole("button", { name: /Water II/ }));
    fireEvent.change(screen.getByLabelText("緯度"), { target: { value: "35.700000" } });
    fireEvent.change(screen.getByLabelText("経度"), { target: { value: "139.800000" } });
    fireEvent.click(screen.getByRole("button", { name: "解析開始" }));
    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createRun).mock.calls[0][0]).toMatchObject({
      requested_accuracy_mode: "full_1m", grid_cell_size_m: 0.5,
      analysis_area: { width_m: 200, height_m: 200, area_m2: 40000 },
      water_magic: { position: { lat_deg: 35.7, lon_deg: 139.8 }, casting_seconds: 4, relaxation_seconds: 40, volume_m3: 80, release_mode:"initial", initial_motion:"radial", initial_speed_mps:2 },
    });
    expect(vi.mocked(createRun).mock.calls[0][0].rainfall).toBeUndefined();
  });
  it("submits directional catalog geometry instead of the previous rain preset", async () => {
    vi.mocked(createRun).mockClear();
    render(<SmokeApp magicMock />);
    fireEvent.click(screen.getByRole("button", {name:/Water II/}));
    fireEvent.change(screen.getByLabelText("1秒あたりの水量 (m³/s)"), {target:{value:"100"}});
    fireEvent.click(screen.getByRole("button", {name:/Neuvillette/}));
    fireEvent.change(screen.getByLabelText("方向 (°)"), {target:{value:"90"}});
    fireEvent.click(screen.getByRole("button", {name:"解析開始"}));
    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createRun).mock.calls[0][0].water_magic).toMatchObject({
      spell_id:"genshin-neuvillette", footprint_kind:"rectangle", length_m:8, width_m:1,
      volume_m3:60, casting_seconds:3, relaxation_seconds:30, bearing_deg:90, release_mode:"initial", initial_motion:"directional", initial_speed_mps:2,
    });
  });
  it("submits a counterclockwise initial vortex", async () => {
    render(<SmokeApp magicMock />);
    expect(screen.getByLabelText("初期運動")).toHaveValue("vortex");
    expect(screen.getByLabelText("初速 (m/s)")).toHaveValue(2);
    fireEvent.change(screen.getByLabelText("渦の向き"), {target:{value:"counterclockwise"}});
    fireEvent.change(screen.getByLabelText("最大流速になる半径 (m)"), {target:{value:"3.5"}});
    fireEvent.click(screen.getByRole("button", {name:"解析開始"}));
    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createRun).mock.calls[0][0].water_magic).toMatchObject({
      release_mode:"initial", initial_motion:"vortex", initial_speed_mps:2,
      vortex_direction:"counterclockwise", vortex_core_radius_m:3.5,
    });
  });
  it("submits the complete school pool once with no initial velocity", async () => {
    render(<SmokeApp magicMock />);
    fireEvent.click(screen.getByRole("button", {name:/小学校のプール/}));
    expect(screen.getByLabelText("初期運動")).toHaveValue("none");
    fireEvent.click(screen.getByRole("button", {name:"解析開始"}));
    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createRun).mock.calls[0][0].water_magic).toMatchObject({
      spell_id:"school-pool", volume_m3:300, length_m:25, width_m:12,
      release_mode:"initial", initial_motion:"none", initial_speed_mps:0,
    });
  });
  it("fixes new analyses to initial water without a placement selector", async () => {
    render(<SmokeApp magicMock />);
    expect(screen.queryByLabelText("水の配置方法")).not.toBeInTheDocument();
    expect(screen.getByLabelText("初期運動")).toHaveValue("vortex");
    expect(screen.getByLabelText("魔法継続時間 (秒)")).toHaveValue(4);
    expect(screen.getByRole("button", {name:"解析開始"})).toBeEnabled();
    fireEvent.click(screen.getByRole("button", {name:"解析開始"}));
    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createRun).mock.calls[0][0].water_magic?.release_mode).toBe("initial");
  });
  it("preserves old continuous results and defaults their next analysis to stationary initial placement", async () => {
    window.localStorage.setItem("urban-pluvial-flood-simulator.active-run-id.water-magic-mock", "00000000-0000-0000-0000-000000000001");
    vi.mocked(getResultMetadata).mockResolvedValue({...metadata, run_summary:{...metadata.run_summary,
      rainfall_source:{kind:"water_magic", configuration_json:JSON.stringify({
        spell_id:"dq7-maelstrom", footprint_kind:"circle", position:{lon_deg:139.767125,lat_deg:35.681236},
        volume_m3:2500, radius_m:20, bearing_deg:0, casting_seconds:4, relaxation_seconds:60,
      })},
    }});
    render(<SmokeApp magicMock />);
    await screen.findByRole("heading", {name:"解析結果"});
    expect(screen.getByText(/メイルストロム.*発動 4秒/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", {name:"新しい解析"}));
    expect(screen.queryByLabelText("水の配置方法")).not.toBeInTheDocument();
    expect(screen.getByLabelText("初期運動")).toHaveValue("none");
    expect(screen.queryByLabelText("初速 (m/s)")).not.toBeInTheDocument();
    expect(screen.getByRole("button", {name:"解析開始"})).toBeEnabled();
  });
  beforeEach(() => {
    vi.mocked(createRun).mockClear();
    window.localStorage.clear();
    vi.mocked(getAppConfig).mockResolvedValue({
      mode: "local",
      allow_run: true,
      allow_result_import: true,
      download_url: "https://github.com/example/releases/latest",
      demo_result_event_ids: [],
    });
    vi.mocked(getHealth).mockResolvedValue({
      status: "ok",
      api_version: "v1",
      application_version: "0.1.0",
      engine: { required: "SFINCS 2.4.0 Galibier" },
    });
    vi.mocked(createRun).mockResolvedValue({
      run_id: "00000000-0000-0000-0000-000000000001",
      status: "QUEUED",
    });
    vi.mocked(createElevationPreview).mockResolvedValue({
      preview_id: "00000000-0000-0000-0000-000000000009",
      bounds: metadata.bounds,
      grid_cell_size_m: 1,
      width_samples: 501,
      height_samples: 501,
      elevation_legend: [
        { label: "1.00–2.00 m", min_m: 1, max_m: 2, color: "#313695" },
      ],
      provider_counts: { gsi_1m: 251001 },
      nearest_filled_cells: 0,
      image_url: "/api/v1/elevation-previews/preview.png",
    });
    vi.mocked(getRun).mockResolvedValue({
      run_id: "00000000-0000-0000-0000-000000000001",
      state: "COMPLETE",
      stage_code: "COMPLETE",
      stage_label: "完了",
      failure_code: null,
      failure_message: null,
    });
    vi.mocked(getResultMetadata).mockResolvedValue(metadata);
    vi.mocked(importResult).mockResolvedValue({
      run_id: "00000000-0000-0000-0000-000000000002",
      status: "COMPLETE",
    });
    vi.mocked(getRecentRainfallRanking).mockResolvedValue({
      period_start: "2016-09-23",
      period_end: "2026-09-23",
      coverage_note: "代表被災地点であり、市内の絶対最大浸水地点を示すものではありません。",
      events: [
        {
          event_id: "tokyo-60m-1",
          station_id: "44132",
          station_name: "四日市市中心部",
          duration_minutes: 60,
          total_precipitation_mm: 123.5,
          intensity_mm_per_h: 123.5,
          event_date_or_datetime_metadata: "2025",
          source_url: "https://example.test/jma",
          catalog_generated_at_utc: "2026-09-03T00:00:00+00:00",
          data_quality_flags: [],
          station_lon_deg: 139.75,
          station_lat_deg: 35.69,
          profile_available: false,
          damage_location_name: "くすの木パーキング",
          damage_location_source_url: "https://example.test/damage",
        },
      ],
    });
    vi.mocked(searchLocation).mockResolvedValue({
      candidates: [],
      attribution: {
        text: "CSISシンプルジオコーディング実験を利用",
        url: "https://geocode.csis.u-tokyo.ac.jp/",
      },
    });
  });

  it("renders the Full 1 m controls and lets the setup map change location/range", () => {
    render(<App />);

    expect(screen.getByText("ローカルレビュー版")).toBeVisible();
    expect(screen.queryByText(/Full 1 m/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("雨量強度 (mm/h)")).toHaveValue("150");
    expect(screen.getByLabelText("継続時間 (min)")).toHaveValue("20");
    expect(screen.queryByRole("button", { name: "Adaptive OFF" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Adaptive ON" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "負荷を見積る" })).not.toBeInTheDocument();
    expect(screen.queryByText(/精度:/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "解析開始" })).toBeVisible();
    expect(screen.getByTestId("setup-map")).toHaveAttribute("data-area-width", "500");
    expect(screen.getByText("約 0.6〜1.2 GB")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "地図で地点選択" }));
    expect(screen.getByLabelText("緯度")).toHaveValue("35.700000");
    expect(screen.getByLabelText("経度")).toHaveValue("139.800000");

    fireEvent.change(screen.getByLabelText("範囲"), { target: { value: "500" } });
    expect(screen.getByTestId("setup-map")).toHaveAttribute("data-area-width", "1000");
    expect(screen.getByText("約 1.0〜2.0 GB")).toBeVisible();

    fireEvent.change(screen.getByLabelText("最小ブロック"), { target: { value: "2" } });
    expect(screen.getByText("約 0.6〜1.2 GB")).toBeVisible();
  });

  it("fills rainfall inputs from the static rainfall ranking", async () => {
    render(<App />);

    await waitFor(() => expect(getAppConfig).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /2025.*四日市.*123\.5 mm\/h/ }));
    expect(screen.getByRole("dialog")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "解析条件に反映" }));

    expect(screen.getByText(/123.5 mm\/h/)).toBeVisible();

    expect(screen.getByLabelText("雨量強度 (mm/h)")).toHaveValue("123.5");
    expect(screen.getByLabelText("継続時間 (min)")).toHaveValue("60");
    expect(screen.getByLabelText("緯度")).toHaveValue("34.966500");
    expect(screen.getByLabelText("経度")).toHaveValue("136.620800");
    expect(screen.getByLabelText("範囲")).toHaveValue("2000");
    expect(screen.getByLabelText("最小ブロック")).toHaveValue("auto");
  });

  it("reconnects to the saved run after a browser reload", async () => {
    const runId = "00000000-0000-0000-0000-000000000001";
    window.localStorage.setItem("urban-pluvial-flood-simulator.active-run-id", runId);

    render(<App />);

    await waitFor(() => expect(getRun).toHaveBeenCalledWith(runId));
  });

  it("offers a prepared result in local mode and opens it on request", async () => {
    vi.mocked(getAppConfig).mockResolvedValue({
      mode: "local",
      allow_run: true,
      allow_result_import: true,
      download_url: "https://github.com/example/releases/latest",
      demo_result_event_ids: ["2025-yokkaichi"],
    });
    vi.mocked(openDemoResult).mockResolvedValue({
      run_id: "00000000-0000-0000-0000-000000000002",
      status: "COMPLETE",
    });
    render(<App />);

    await waitFor(() => expect(getAppConfig).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: /2025.*四日市.*123\.5 mm\/h/ }));
    fireEvent.click(screen.getByRole("button", { name: "解析済みデータを読み込む" }));

    await waitFor(() => expect(openDemoResult).toHaveBeenCalledWith("2025-yokkaichi"));
    expect(await screen.findByRole("heading", { name: "解析結果" })).toBeVisible();
  });

  it("opens prepared results from the sample choice dialog and replaces analysis with download in demo mode", async () => {
    vi.mocked(getAppConfig).mockResolvedValue({
      mode: "demo",
      allow_run: false,
      allow_result_import: false,
      download_url: "https://github.com/example/releases/latest",
      demo_result_event_ids: ["2025-yokkaichi"],
    });
    vi.mocked(openDemoResult).mockResolvedValue({
      run_id: "00000000-0000-0000-0000-000000000002",
      status: "COMPLETE",
    });
    render(<App />);

    expect(await screen.findByRole("link", { name: "Windows版をダウンロード" })).toHaveAttribute(
      "href",
      "https://github.com/example/releases/latest",
    );
    expect(screen.queryByRole("button", { name: "解析開始" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("解析済みデータをファイルから読み込む")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /2025.*四日市.*123\.5 mm\/h/ }));
    expect(screen.getByRole("dialog")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "解析済みデータを読み込む" }));
    await waitFor(() => expect(openDemoResult).toHaveBeenCalledWith("2025-yokkaichi"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("disables analyzed-data loading when a sample archive is unavailable", async () => {
    render(<App />);

    await waitFor(() => expect(getAppConfig).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /2026.*千葉.*115 mm\/h/ }));

    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByRole("button", { name: "解析済みデータを読み込む" })).toBeDisabled();
    expect(screen.getByText("このサンプルの解析済みデータは現在ありません。")).toBeVisible();
  });

  it("imports a saved result for review and offers compressed export", async () => {
    render(<App />);
    const file = new File(["archive"], "saved-result.zip", { type: "application/zip" });
    fireEvent.change(screen.getByLabelText("解析済みデータをファイルから読み込む"), {
      target: { files: [file] },
    });

    await waitFor(() => expect(importResult).toHaveBeenCalledWith(file));
    expect(await screen.findByRole("heading", { name: "解析結果" })).toBeVisible();
    expect(screen.getByRole("link", { name: "結果をエクスポート" })).toHaveAttribute(
      "href",
      "/api/v1/runs/00000000-0000-0000-0000-000000000002/export",
    );
  });

  it("previews elevation without starting a run and can continue to analysis", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: "標高のみ取得" }));
    await waitFor(() => expect(createElevationPreview).toHaveBeenCalledWith(
      expect.objectContaining({ width_m: 500, height_m: 500 }),
      1,
    ));
    expect(createRun).not.toHaveBeenCalled();
    expect(await screen.findByRole("heading", { name: "取得した標高" })).toBeVisible();
    expect(screen.getByLabelText("取得した標高の凡例")).toHaveTextContent("1.00–2.00 m");

    fireEvent.click(screen.getByRole("button", { name: "この条件で解析開始" }));
    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(1));
  });


  it("uses the selected uniform block size for a new calculation", async () => {
    render(<App />);

    expect(screen.queryByRole("button", { name: "Adaptive OFF" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Adaptive ON" })).not.toBeInTheDocument();
    expect(screen.queryByText(/精度:/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "解析開始" }));
    await waitFor(() => {
      expect(createRun).toHaveBeenCalledWith(
        expect.objectContaining({ requested_accuracy_mode: "uniform", grid_cell_size_m: 1 }),
      );
    });
  });

  it("sends a manually selected 4 m block size to the run API", async () => {
    render(<App />);

    fireEvent.change(screen.getByLabelText("最小ブロック"), {
      target: { value: "4" },
    });
    expect(screen.getByText("15,625")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "解析開始" }));

    await waitFor(() => {
      expect(createRun).toHaveBeenCalledWith(
        expect.objectContaining({
          requested_accuracy_mode: "uniform",
          grid_cell_size_m: 4,
        }),
      );
    });
    expect(createRun).not.toHaveBeenCalledWith(
      expect.objectContaining({ requested_accuracy_mode: "adaptive" }),
    );
  });


  it("uses a geocoder candidate as the canonical setup center", async () => {
    vi.mocked(searchLocation).mockResolvedValue({
      candidates: [
        {
          title: "東京都府中市宮町1丁目",
          lon: 139.4805,
          lat: 35.6722,
          provider: "csis_simple_geocoding",
          confidence: 5,
          level: 8,
          converted: "東京都府中市宮町1丁目",
        },
      ],
      attribution: {
        text: "CSISシンプルジオコーディング実験を利用",
        url: "https://geocode.csis.u-tokyo.ac.jp/",
      },
    });

    render(<App />);

    fireEvent.change(screen.getByLabelText("住所・地名を検索"), {
      target: { value: "府中駅" },
    });
    fireEvent.click(screen.getByRole("button", { name: "検索" }));

    const candidate = await screen.findByRole("button", {
      name: /東京都府中市宮町1丁目/,
    });
    fireEvent.click(candidate);

    expect(screen.getByLabelText("緯度")).toHaveValue("35.672200");
    expect(screen.getByLabelText("経度")).toHaveValue("139.480500");
    expect(screen.getByTestId("setup-map")).toHaveAttribute("data-area-width", "500");
    expect(screen.getByText(/CSISシンプルジオコーディング実験を利用/)).toBeVisible();
  });

  it("disables setup-map mutation while a run is active", async () => {
    vi.mocked(getRun).mockResolvedValue({
      run_id: "00000000-0000-0000-0000-000000000001",
      state: "RUNNING_ENGINE",
      stage_code: "RUNNING_ENGINE",
      stage_label: "SFINCSを実行中",
      failure_code: null,
      failure_message: null,
    });

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "解析開始" }));

    await waitFor(() => {
      expect(screen.getByTestId("setup-map")).toHaveAttribute("data-disabled", "true");
    });
    expect(screen.getByRole("button", { name: "地図で地点選択" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "解析開始" })).toBeDisabled();

    await waitFor(() => {
      expect(screen.getByText(/SFINCS計算中/)).toBeVisible();
    });
  });

  it("enters dedicated RESULT mode after a completed run and retains setup inputs for a new analysis", async () => {
    render(<App />);

    fireEvent.change(screen.getByLabelText("継続時間 (min)"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByLabelText("雨量強度 (mm/h)"), {
      target: { value: "10" },
    });
    fireEvent.click(screen.getByRole("button", { name: "解析開始" }));

    await waitFor(() => {
      expect(createRun).toHaveBeenCalledWith(
        expect.objectContaining({ requested_accuracy_mode: "uniform", grid_cell_size_m: 1 }),
      );
    });
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "解析結果" })).toBeVisible();
    });

    expect(screen.getByTestId("result-map")).toHaveTextContent("最大浸水深の地図");
    expect(screen.queryByRole("heading", { name: "1. 条件" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "新しい解析" }));

    expect(screen.getByRole("heading", { name: "1. 条件" })).toBeVisible();
    expect(screen.getByLabelText("緯度")).toHaveValue("35.681236");
    expect(screen.getByLabelText("経度")).toHaveValue("139.767125");
    expect(screen.getByLabelText("範囲")).toHaveValue("250");
    expect(screen.getByLabelText("雨量強度 (mm/h)")).toHaveValue("10");
    expect(screen.getByLabelText("継続時間 (min)")).toHaveValue("1");
    expect(screen.queryByText(/精度:/)).not.toBeInTheDocument();
  });
});

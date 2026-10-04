import { availableResultTimes } from "./resultTime";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getFlowVectors,
  startResultExtrema,
  getResultExtremaProgress,
  startResultEnergy,
  getResultEnergy,
  getResultEnergyPoint,
  inspectResult,
  resultExportUrl,
  resultLayerUrl,
  type FlowVectorFeatureCollection,
  type FlowViewport,
  type PointInspectionResponse,
  type ResultMetadataResponse,
  type ResultExtremaResponse,
  type ResultExtremaJobResponse,
  type ResultExtremeLocation,
  type ResultEnergyJobResponse,
  type PointEnergyResponse,
} from "../api/client";
import ResultMap, { type FlowRenderStats } from "./ResultMap";
import { encodeGif, type GifFrame } from "./gifEncoder";
import { savedMagic, savedMagicGeometry, magicTimeLabel, magicTimeUnit, elapsedSeconds } from "./magicTime";
import { spellById } from "../dev/magicCatalog";
import "./result.css";
import { comparisonSpeedLabel, cumulativeEnergyComparison, energyComparison, kmhLabel } from "./energyComparison";

function waitForRankingPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { window.clearTimeout(timer); signal.removeEventListener("abort", abort); reject(new DOMException("Aborted", "AbortError")); };
    const timer = window.setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 300);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

type Props = {
  runId: string;
  metadata: ResultMetadataResponse;
  rainfallSummary: string;
  onNewAnalysis: () => void;
};

type ResultLayer = "max_depth" | "time_depth" | "grid_resolution" | "elevation";

const LIMITATION_LABELS: Record<string, string> = {
  infiltration_modelled: "浸透は考慮していません。",
  sewer_network_modelled: "下水道・雨水管は考慮していません。",
  storm_drain_inlets_modelled: "排水口・雨水桝は考慮していません。",
  building_interior_modelled: "建物内部の浸水は計算していません。",
  spatial_meteorological_rainfall_modelled: "気象降雨は解析範囲内で一様として扱います。",
  river_stage_boundary_modelled: "河川水位との連成は行いません。",
  coastal_tide_surge_modelled: "潮位・高潮との連成は行いません。",
  grade_separated_transport_modelled: "陸橋・トンネル・高速道路下などの立体交差を通る水の流れは考慮していません。",
  official_forecast: "この結果は数値シナリオであり、公的な洪水予報・避難情報ではありません。",
};

const GRID_LEGEND = [
  ["0.5 m", "#4C78A8"],
  ["1 m", "#264653"],
  ["2 m", "#2A6F97"],
  ["4 m", "#3D9180"],
  ["8 m", "#7AA874"],
  ["16 m", "#BABC7D"],
  ["32 m", "#D0C8AD"],
] as const;

export function strideForZoom(zoom: number): number {
  const stride = Math.round(6 * 2 ** (18 - zoom));
  return Math.max(1, Math.min(4096, stride));
}


const FLOW_COLORS = ["#2DC4B2", "#3BB2D0", "#3F51B5", "#8E44AD", "#E74C3C", "#A52A2A", "#7F0000"] as const;

function metres(value: number | null | undefined): string {
  return value == null ? "—" : `${value.toFixed(3)} m`;
}

function elapsedLabel(values: string[], index: number, unit?: "seconds" | "minutes"): string {
  const currentValue = values[index] ?? "";
  const startValue = values[0] ?? "";
  const numericTime = /^-?\d+(?:\.\d+)?$/;
  if (numericTime.test(currentValue) && numericTime.test(startValue)) {
    if (unit) return magicTimeLabel(Math.max(0, Number(currentValue) - Number(startValue)), unit);
    return durationLabel(Math.max(0, Number(currentValue) - Number(startValue)) / 60);
  }
  const current = Date.parse(currentValue);
  const start = Date.parse(startValue);
  if (Number.isFinite(current) && Number.isFinite(start)) {
    if (unit) return magicTimeLabel(Math.max(0, (current - start) / 1000), unit);
    return durationLabel(Math.max(0, (current - start) / 60_000));
  }
  return values[index] ?? `index ${index}`;
}

function durationLabel(elapsedMinutes: number): string {
  const roundedMinutes = Math.round(elapsedMinutes);
  const days = Math.floor(roundedMinutes / 1440);
  const hours = Math.floor((roundedMinutes % 1440) / 60);
  const minutes = roundedMinutes % 60;
  if (days > 0) return `${days}日 ${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function elapsedValueLabel(values: string[], value: string | null | undefined, fallbackIndex: number, unit?: "seconds" | "minutes"): string {
  const valueIndex = value == null ? -1 : values.indexOf(value);
  return elapsedLabel(values, valueIndex >= 0 ? valueIndex : fallbackIndex, unit);
}

function savedRainfallSummary(
  metadata: ResultMetadataResponse,
  fallback: string,
): string {
  const rainfall = metadata.run_summary.rainfall_source ?? {};
  const magic = savedMagic(metadata);
  if (magic) return `${spellById(magic.spell_id).name} · ${magic.volume_m3} m³ · ${magic.release_mode === "initial" ? "初期配置・追跡" : "発動"} ${magicTimeLabel(magic.casting_seconds, magicTimeUnit(magic.casting_seconds))} + 緩和 ${magicTimeLabel(magic.relaxation_seconds, magicTimeUnit(magic.casting_seconds))}`;
  const intensity = Number(rainfall.intensity_mm_per_h);
  const duration = Number(rainfall.duration_minutes);
  if (!Number.isFinite(intensity) || !Number.isFinite(duration)) return fallback;
  return `${intensity} mm/h × ${duration}分`;
}

export default function ResultPanel({
  runId,
  metadata,
  rainfallSummary,
  onNewAnalysis,
}: Props) {
  const [layer, setLayer] = useState<ResultLayer>("max_depth");
  const [showMagicFootprint, setShowMagicFootprint] = useState(true);
  const [timePosition, setTimePosition] = useState(0);
  const [backgroundTransparency, setBackgroundTransparency] = useState(45);
  const [flowMode, setFlowMode] = useState<"off" | "vectors" | "particles">("off");
  const flowVisible = flowMode !== "off";
  const [flowVectorData, setFlowVectorData] = useState<FlowVectorFeatureCollection | null>(null);
  const [flowLoading, setFlowLoading] = useState(false);
  const [flowError, setFlowError] = useState<string | null>(null);
  const [flowRenderStats, setFlowRenderStats] = useState<FlowRenderStats | null>(null);
  const [flowViewport, setFlowViewport] = useState<FlowViewport>({ west: metadata.bounds.west_deg, south: metadata.bounds.south_deg, east: metadata.bounds.east_deg, north: metadata.bounds.north_deg });
  const [flowStride, setFlowStride] = useState(() => strideForZoom(18));
  const [flowViewportReady, setFlowViewportReady] = useState(false);
  const [nextFlowVectorData, setNextFlowVectorData] = useState<FlowVectorFeatureCollection | null>(null);
  const flowInterpolationRef = useRef(0);
  const [playing, setPlaying] = useState(false);
  const playingRef = useRef(false);
  const [loop, setLoop] = useState(true);
  const [gifProgress, setGifProgress] = useState<number | null>(null);
  const [gifError, setGifError] = useState<string | null>(null);
  const [gifFrameUrl, setGifFrameUrl] = useState<string | null>(null);
  const gifCancelRef = useRef(false);
  const captureRef = useRef<((expectedImageUrl?: string) => Promise<HTMLCanvasElement>) | null>(null);
  const flowAutoLocateRef = useRef(false);
  const initialFlowTimeRunIdRef = useRef<string | null>(null);
  const [inspection, setInspection] = useState<PointInspectionResponse | null>(null);
  const [inspectionLoading, setInspectionLoading] = useState(false);
  const [inspectionError, setInspectionError] = useState<string | null>(null);
  const [inspectionPoint, setInspectionPoint] = useState<{ lon: number; lat: number } | null>(null);
  const [mapFocusPoint, setMapFocusPoint] = useState<{ lon: number; lat: number } | null>(null);
  const [extrema, setExtrema] = useState<ResultExtremaResponse | null>(null);
  const [extremaLoading, setExtremaLoading] = useState(false);
  const [extremaJob, setExtremaJob] = useState<ResultExtremaJobResponse | null>(null);
  const [extremaMetric, setExtremaMetric] = useState<"depth" | "speed" | null>(null);
  const [extremaError, setExtremaError] = useState<string | null>(null);
  const [energyJob, setEnergyJob] = useState<ResultEnergyJobResponse | null>(null);
  const [energyCalculating, setEnergyCalculating] = useState(false);
  const [pointEnergy, setPointEnergy] = useState<PointEnergyResponse | null>(null);
  const [pointEnergyLoading, setPointEnergyLoading] = useState(false);
  const [pointEnergyError, setPointEnergyError] = useState<string | null>(null);
  const [rankedPoint, setRankedPoint] = useState<{ metric: "depth" | "speed" | "energy"; entry: ResultExtremeLocation } | null>(null);
  const rankCursorRef = useRef({ depth: -1, speed: -1, energy: -1 });
  const extremaRequestRef = useRef<AbortController | null>(null);
  const focusRegionRef = useRef<HTMLDivElement | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === focusRegionRef.current);
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);




  return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, []);

  const toggleFullscreen = useCallback(() => {
    const region = focusRegionRef.current;
    if (!region) return;
    if (document.fullscreenElement === region) {
      void document.exitFullscreen();
      return;
    }
    void region.requestFullscreen();
  }, []);

  const availableTimeIndices = useMemo(() => availableResultTimes(metadata), [metadata]);
  const selectedTimeIndex = availableTimeIndices[timePosition] ?? null;
  const manualComparison = rankedPoint === null && !inspectionLoading && inspection?.has_data && pointEnergy?.has_data
    && pointEnergy.lon_deg === inspectionPoint?.lon && pointEnergy.lat_deg === inspectionPoint?.lat ? pointEnergy : null;
  const integratedComparison = manualComparison !== null || rankedPoint?.metric === "energy";
  const comparison = manualComparison
    ? cumulativeEnergyComparison(manualComparison.total_energy_j ?? 0, manualComparison.total_outflow_m3 ?? 0)
    : rankedPoint?.metric === "energy"
    ? cumulativeEnergyComparison(rankedPoint.entry.total_energy_j ?? 0, rankedPoint.entry.total_outflow_m3 ?? 0)
    : rankedPoint && rankedPoint.entry.time_index === selectedTimeIndex
      ? energyComparison(rankedPoint.entry.depth_m, rankedPoint.entry.cell_area_m2, rankedPoint.entry.speed_mps ?? null) : null;
  const energyProgressVisible = energyCalculating || pointEnergyLoading && energyJob?.status !== "complete";
  const activeTimeIndex = layer === "time_depth" ? selectedTimeIndex : null;
  const imageUrl = useMemo(() => {
    if (layer === "time_depth") {
      if (selectedTimeIndex === null) return resultLayerUrl(runId, "max-depth");
      return resultLayerUrl(runId, "depth", selectedTimeIndex);
    }
    if (layer === "grid_resolution") return resultLayerUrl(runId, "grid-resolution");
    if (layer === "elevation") return resultLayerUrl(runId, "elevation");
    return resultLayerUrl(runId, "max-depth");
  }, [layer, runId, selectedTimeIndex]);

  const handleFlowToggle = useCallback((requestedMode: "vectors" | "particles") => {
    if (flowMode === requestedMode) {
      flowAutoLocateRef.current = false;
      setFlowMode("off");
      setFlowVectorData(null);
      setFlowError(null);
      setFlowRenderStats(null);
      return;
    }
    const selectInitialFlowTime = initialFlowTimeRunIdRef.current !== runId && layer !== "time_depth" && !isFullscreen;
    initialFlowTimeRunIdRef.current = runId;
    flowAutoLocateRef.current = selectInitialFlowTime;
    setLayer("time_depth");
    setFlowMode(requestedMode);
  }, [flowMode, layer, isFullscreen, runId]);

  const showMaximumDepth = useCallback(() => {
    setLayer("max_depth");
    flowAutoLocateRef.current = false;
    setFlowMode("off");
    setFlowVectorData(null);
    setFlowError(null);
    setFlowRenderStats(null);
  }, []);

  useEffect(() => {
    if (
      !flowVisible ||
      !flowViewportReady ||
      !metadata.flow_vectors_available ||
      selectedTimeIndex === null
    ) {
      setFlowVectorData(null);
      setFlowLoading(false);
      setFlowError(null);
      return;
    }

    const controller = new AbortController();
    let disposed = false;

    const load = async () => {
      setFlowLoading(true);
      setFlowError(null);
      setFlowVectorData(null);
      try {
        if (flowAutoLocateRef.current) {
          const center = await inspectResult(
            runId,
            (metadata.bounds.west_deg + metadata.bounds.east_deg) / 2,
            (metadata.bounds.south_deg + metadata.bounds.north_deg) / 2,
            null,
            controller.signal,
          );
          if (disposed) return;
          flowAutoLocateRef.current = false;
          if (center?.has_data && center.max_time_index !== null) {
            const maximumPosition = availableTimeIndices.reduce((best, index, position) => (
              Math.abs(index - center.max_time_index!) < Math.abs(availableTimeIndices[best] - center.max_time_index!)
                ? position : best
            ), 0);
            if (maximumPosition !== timePosition) {
              setTimePosition(maximumPosition);
              return;
            }
          }
        }
        const current = await getFlowVectors(
          runId,
          selectedTimeIndex,
          flowViewport,
          flowStride,
          controller.signal,
          flowMode === "particles",
        );
        if (disposed) return;

        flowAutoLocateRef.current = false;
        setFlowVectorData(current);
      } catch (cause: unknown) {
        if (!controller.signal.aborted && !disposed) {
          flowAutoLocateRef.current = false;
          setFlowVectorData(null);
          setFlowError(String(cause));
        }
      } finally {
        if (!controller.signal.aborted && !disposed) setFlowLoading(false);
      }
    };

    void load();
    return () => {
      disposed = true;
      controller.abort();
    };
  }, [
    flowVisible,
    flowMode,
    availableTimeIndices,
    metadata.flow_vectors_available,
    metadata.bounds,
    runId,
    selectedTimeIndex,
    timePosition,
    flowViewport,
    flowStride,
    flowViewportReady,
  ]);

  const showTimeline =
    availableTimeIndices.length > 0 &&
    (layer === "time_depth" || flowVisible || isFullscreen);

  const mapLabel =
    layer === "time_depth"
      ? "時刻別浸水深の地図"
      : layer === "grid_resolution"
        ? "計算格子解像度の地図"
        : layer === "elevation"
          ? "標高の地図"
        : "最大浸水深の地図";

  const handleInspect = useCallback((lon: number, lat: number) => {
    setRankedPoint(null);
    setInspectionPoint({ lon, lat });
  }, []);

  useEffect(() => {
    setExtrema(null);
    setExtremaJob(null);
    setExtremaMetric(null);
    setRankedPoint(null);
    setExtremaError(null);
    setExtremaLoading(false);
    setEnergyJob(null);
    setEnergyCalculating(false);
    setPointEnergy(null);
    setPointEnergyLoading(false);
    setPointEnergyError(null);
    rankCursorRef.current = { depth: -1, speed: -1, energy: -1 };
    return () => {
      extremaRequestRef.current?.abort();
      extremaRequestRef.current = null;
    };
  }, [runId]);

  const visitRankedPoint = async (metric: "depth" | "speed" | "energy") => {
    if (extremaRequestRef.current) return;
    const controller = new AbortController();
    extremaRequestRef.current = controller;
    setExtremaLoading(true);
    setExtremaError(null);
    try {
      let candidates: ResultExtremeLocation[];
      if (metric === "energy") {
        setEnergyCalculating(true);
        let job = energyJob?.status === "complete" ? energyJob : await startResultEnergy(runId, controller.signal);
        if (controller.signal.aborted) return;
        setEnergyJob(job);
        while (job.status === "queued" || job.status === "running") {
          await waitForRankingPoll(controller.signal);
          job = await getResultEnergy(runId, controller.signal);
          if (controller.signal.aborted) return;
          setEnergyJob(job);
        }
        if (job.status === "failed") throw new Error(job.error ?? "エネルギー集計に失敗しました。");
        candidates = job.energy ?? [];
      } else {
        setExtremaMetric(metric);
        let ranking = extrema;
        if (ranking === null) {
          setExtremaJob(null);
          let job = await startResultExtrema(runId, controller.signal);
          if (controller.signal.aborted) return;
          setExtremaJob(job);
          while (job.status === "queued" || job.status === "running") {
            await waitForRankingPoll(controller.signal);
            job = await getResultExtremaProgress(runId, controller.signal);
            if (controller.signal.aborted) return;
            setExtremaJob(job);
          }
          if (job.status === "failed") throw new Error(job.error ?? "水深・流速の集計に失敗しました。");
          ranking = { depth: job.depth, speed: job.speed };
        }
        if (controller.signal.aborted) return;
        setExtrema(ranking);
        candidates = ranking[metric];
      }
      if (controller.signal.aborted) return;
      const entries = candidates.filter(entry => availableTimeIndices.includes(entry.time_index));
      if (!entries.length) {
        setExtremaError(`${metric === "depth" ? "水深" : metric === "speed" ? "流速" : "エネルギー"}の順位を表示できる地点がありません。`);
        return;
      }
      const cursor = (rankCursorRef.current[metric] + 1) % entries.length;
      rankCursorRef.current[metric] = cursor;
      const entry = entries[cursor];
      playingRef.current = false;
      setPlaying(false);
      flowAutoLocateRef.current = false;
      setTimePosition(availableTimeIndices.indexOf(entry.time_index));
      setLayer("time_depth");
      setMapFocusPoint({ lon: entry.lon_deg, lat: entry.lat_deg });
      setInspectionPoint({ lon: entry.lon_deg, lat: entry.lat_deg });
      setRankedPoint({ metric, entry });
    } catch (cause: unknown) {
      if (!controller.signal.aborted) setExtremaError(String(cause));
    } finally {
      if (!controller.signal.aborted) {
        extremaRequestRef.current = null;
        setExtremaLoading(false);
        setEnergyCalculating(false);
      }
    }
  };

  useEffect(() => {
    if (inspectionPoint === null) return;
    const controller = new AbortController();
    setInspectionLoading(true);
    setInspectionError(null);

    void inspectResult(
      runId,
      inspectionPoint.lon,
      inspectionPoint.lat,
      activeTimeIndex,
      controller.signal,
    )
      .then((next) => {
        if (!controller.signal.aborted) setInspection(next);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setInspectionError(String(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setInspectionLoading(false);
      });

    return () => controller.abort();
  }, [activeTimeIndex, inspectionPoint, runId]);

  useEffect(() => {
    setPointEnergy(null);
    setPointEnergyError(null);
    setPointEnergyLoading(false);
    if (!inspectionPoint || rankedPoint || !metadata.flow_vectors_available) return;
    const controller = new AbortController();
    setPointEnergyLoading(true);
    void (async () => {
      let job = await startResultEnergy(runId, controller.signal);
      if (controller.signal.aborted) return;
      setEnergyJob(job);
      while (job.status === "queued" || job.status === "running") {
        await waitForRankingPoll(controller.signal);
        job = await getResultEnergy(runId, controller.signal);
        if (controller.signal.aborted) return;
        setEnergyJob(job);
      }
      if (job.status === "failed") throw new Error(job.error ?? "エネルギー集計に失敗しました。");
      const next = await getResultEnergyPoint(runId, inspectionPoint.lon, inspectionPoint.lat, controller.signal);
      if (!controller.signal.aborted) setPointEnergy(next);
    })().catch((cause: unknown) => {
      if (!controller.signal.aborted) setPointEnergyError(String(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setPointEnergyLoading(false);
    });
    return () => controller.abort();
  }, [inspectionPoint, rankedPoint, runId, metadata.flow_vectors_available]);

  const magic = savedMagic(metadata);
  const omittedLimitations = Object.entries({
    grade_separated_transport_modelled: false,
    ...metadata.limitations,
  })
    .filter(([key, implemented]) => !implemented && !(magic && key === "spatial_meteorological_rainfall_modelled"))
    .map(([key]) => LIMITATION_LABELS[key] ?? `${key}: 未実装`);

  const provider = metadata.provider_summary;
  const engine = metadata.engine_summary;
  const runSummary = metadata.run_summary;
  const timeUnit = magic ? magicTimeUnit(magic.casting_seconds) : undefined;
  const magicGeometry = useMemo(() => savedMagicGeometry(metadata), [metadata]);
  const formatElapsed = (index: number) => elapsedLabel(metadata.time_values, index, timeUnit);
  const globalMax = metadata.max_depth_summary.global_max_depth_m;
  const flowSpeedRange = useMemo<readonly [number, number]>(() => {
    const minimum = flowVectorData?.metadata.display_min_speed_mps ?? 0.001;
    const candidateMaximum = flowVectorData?.metadata.display_max_speed_mps ?? 2;
    return [minimum, candidateMaximum > minimum ? candidateMaximum : Math.max(minimum, 2)];
  }, [flowVectorData]);
  const flowSpeedBreaks = flowVectorData?.metadata.speed_scale?.breaks;
  const flowLegend = useMemo(() => {
    if (flowSpeedBreaks) return Array.from({ length: flowVectorData?.metadata.speed_scale?.class_count ?? 0 }, (_, index) => {
      const edge0 = flowSpeedBreaks[index];
      const edge1 = flowSpeedBreaks[Math.min(index + 1, flowSpeedBreaks.length - 1)];
      const labels = flowVectorData?.metadata.speed_scale?.boundary_labels;
      return [`${labels?.[index] ?? edge0}–${labels?.[Math.min(index + 1, flowSpeedBreaks.length - 1)] ?? edge1} m/s`, FLOW_COLORS[index]] as const;
    });
    return FLOW_COLORS.map((color, index) => {
      const [minimum, maximum] = flowSpeedRange;
      return [`${(minimum + (maximum - minimum) * index / FLOW_COLORS.length).toFixed(3)}–${(minimum + (maximum - minimum) * (index + 1) / FLOW_COLORS.length).toFixed(3)} m/s`, color] as const;
    });
  }, [flowSpeedBreaks, flowVectorData, flowSpeedRange]);
  const maxTimePosition = Math.max(0, availableTimeIndices.length - 1);

  useEffect(() => {
    flowInterpolationRef.current = 0;
    setNextFlowVectorData(null);
    if (!playing || !flowVisible || !flowVectorData || selectedTimeIndex === null) return;
    const nextPosition = timePosition + 1;
    const nextIndex = availableTimeIndices[nextPosition];
    if (nextIndex == null) return;
    const controller = new AbortController();
    void getFlowVectors(runId, nextIndex, flowViewport, flowStride, controller.signal, flowMode === "particles")
      .then((data) => {
        if (!controller.signal.aborted) setNextFlowVectorData(data);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [playing, flowMode, flowVectorData, flowVisible, flowStride, flowViewport, availableTimeIndices, runId, selectedTimeIndex, timePosition]);

  useEffect(() => { playingRef.current = playing; }, [playing]);

  useEffect(() => {
    if (!playing || availableTimeIndices.length < 2) return;
    let frame = 0;
    let started = performance.now();
    const durationMs = 1000;
    const tick = (now: number) => {
      const fraction = Math.min(1, (now - started) / durationMs);
      flowInterpolationRef.current = fraction;
      if (fraction >= 1) {
        setTimePosition((position) => {
          if (position < maxTimePosition) return position + 1;
          if (loop) return 0;
          setPlaying(false);
          return position;
        });
        started = now;
      }
      if (playingRef.current) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, flowVectorData, nextFlowVectorData, loop, maxTimePosition, availableTimeIndices.length]);

  const handleViewportChange = useCallback((viewport: FlowViewport, zoom: number) => {
    setFlowViewport(viewport);
    setFlowStride(strideForZoom(zoom));
    setFlowViewportReady(true);
  }, []);

  const exportGif = useCallback(async () => {
    const capture = captureRef.current;
    if (!capture || availableTimeIndices.length === 0) return;
    gifCancelRef.current = false;
    setGifError(null);
    setGifProgress(0);
    setPlaying(false);
    setLayer("time_depth");
    const count = Math.min(80, availableTimeIndices.length);
    const positions = Array.from({ length: count }, (_, i) => Math.round(i * (availableTimeIndices.length - 1) / Math.max(1, count - 1)));
    const frames: GifFrame[] = [];
    let width = 0;
    let height = 0;
    try {
      for (let i = 0; i < positions.length; i += 1) {
        if (gifCancelRef.current) break;
        const position = positions[i];
        setTimePosition(position);
        const frameUrl = resultLayerUrl(runId, "depth", availableTimeIndices[position] ?? 0);
        setGifFrameUrl(frameUrl);
        const source = await capture(frameUrl);
        const scale = Math.min(1, 640 / source.width, 480 / source.height);
        width = Math.max(1, Math.round(source.width * scale));
        height = Math.max(1, Math.round(source.height * scale));
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height + 28;
        const ctx = canvas.getContext("2d");
        if (!ctx) throw new Error("Canvas 2D context is unavailable");
        ctx.drawImage(source, 0, 0, width, height);
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, height, width, 28);
        ctx.fillStyle = "#111827";
        ctx.font = "14px sans-serif";
        ctx.fillText(formatElapsed(availableTimeIndices[position] ?? 0), 10, height + 19);
        frames.push({ rgba: ctx.getImageData(0, 0, width, height + 28).data, delayCs: 10 });
        setGifProgress((i + 1) / positions.length);
      }
      if (!gifCancelRef.current && frames.length > 0) {
        const blob = encodeGif(width, height + 28, frames, loop);
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `flood-result-${runId}.gif`;
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (error) {
      setGifError(error instanceof Error ? error.message : String(error));
    } finally {
      setGifFrameUrl(null);
      setGifProgress(null);
    }
  }, [loop, availableTimeIndices, metadata.time_values, runId]);

  return (
    <section className="result-shell" aria-labelledby="result-title">
      <div className="result-heading">
        <div>
          <p className="result-kicker">RESULT</p>
          <h2 id="result-title">解析結果</h2>
          <p>{savedRainfallSummary(metadata, rainfallSummary)} / 高精度 — 全域{Object.keys(metadata.grid_level_summary).map((size) => size.replace("m", " m")).join(" / ")}</p>
          {magic && <p>表示ステップ: {magicTimeLabel(elapsedSeconds(metadata.time_values, availableTimeIndices[1] ?? 0) - elapsedSeconds(metadata.time_values, availableTimeIndices[0] ?? 0), timeUnit!)}（{magic.release_mode === "initial" ? "追跡時間" : "魔法継続"}5分未満は秒、5分以上は分）</p>}
          {magicGeometry && <button type="button" aria-pressed={showMagicFootprint} onClick={() => setShowMagicFootprint(value => !value)}>魔法の効果範囲を{showMagicFootprint ? "非表示" : "表示"}</button>}
        </div>
        <div className="result-heading-actions">
          <a className="result-export-link" href={resultExportUrl(runId)} download>結果をエクスポート</a>
          <button type="button" onClick={onNewAnalysis}>新しい解析</button>
        </div>
      </div>

      <div className="result-limitation-bar">
        数値シナリオです。公的な洪水予報・避難判断の代替ではありません。
      </div>

      <div className="result-focus-region" ref={focusRegionRef} data-testid="result-focus-region">
        <div className="result-focus-controls" data-testid="result-focus-controls">
          <div className="result-focus-control-row">
            <div className="result-layer-controls" aria-label="結果レイヤー">
              <button
                type="button"
                className={layer === "max_depth" ? "is-active" : ""}
                aria-pressed={layer === "max_depth"}
                onClick={showMaximumDepth}
              >
                最大浸水深
              </button>
              <button
                type="button"
                className={layer === "time_depth" ? "is-active" : ""}
                aria-pressed={layer === "time_depth"}
                disabled={availableTimeIndices.length === 0}
                onClick={() => setLayer("time_depth")}
              >
                時刻別の浸水深
              </button>
              <button
                type="button"
                disabled={extremaLoading || availableTimeIndices.length === 0 || extrema?.depth.length === 0}
                title="押すたびに水深上位10地点の場所とピーク時刻へ移動します"
                onClick={() => void visitRankedPoint("depth")}
              >
                最大深度箇所
              </button>
              <button
                type="button"
                disabled={extremaLoading || !metadata.flow_vectors_available || availableTimeIndices.length === 0 || extrema?.speed.length === 0}
                title="押すたびに流速上位10地点の場所とピーク時刻へ移動します"
                onClick={() => void visitRankedPoint("speed")}
              >
                最高速度箇所
              </button>
              <button
                type="button"
                disabled={extremaLoading || !metadata.flow_vectors_available || availableTimeIndices.length === 0 || energyJob?.status === "complete" && (energyJob.energy?.length ?? 0) === 0}
                title="1 m区画から流出した総エネルギー上位10地点を巡回し、集計終了時刻へ移動します"
                onClick={() => void visitRankedPoint("energy")}
              >最高総エネルギー箇所</button>
              <button
                type="button"
                className={flowMode === "vectors" ? "is-active" : ""}
                aria-pressed={flowMode === "vectors"}
                disabled={!metadata.flow_vectors_available || !flowViewportReady}
                onClick={() => handleFlowToggle("vectors")}
              >
                流れベクトル
              </button>
              <button
                type="button"
                className={flowMode === "particles" ? "is-active" : ""}
                aria-pressed={flowMode === "particles"}
                disabled={!metadata.flow_vectors_available || !flowViewportReady}
                onClick={() => handleFlowToggle("particles")}
              >
                粒子フロー
              </button>
              <button
                type="button"
                className={layer === "grid_resolution" ? "is-active" : ""}
                aria-pressed={layer === "grid_resolution"}
                onClick={() => setLayer("grid_resolution")}
              >
                計算格子
              </button>
              <button
                type="button"
                className={layer === "elevation" ? "is-active" : ""}
                aria-pressed={layer === "elevation"}
                onClick={() => setLayer("elevation")}
              >
                標高
              </button>
            </div>

            <button
              type="button"
              className="result-fullscreen-button"
              onClick={toggleFullscreen}
              aria-label={isFullscreen ? "全画面表示を終了" : "地図を全画面表示"}
            >
              {isFullscreen ? "全画面を終了" : "全画面"}
            </button>
          </div>

          <div className="result-display-controls">
            <label>
              背景地図の透明度
              <input
                aria-label="背景地図の透明度"
                type="range"
                min={0}
                max={100}
                step={1}
                value={backgroundTransparency}
                onChange={(event) => setBackgroundTransparency(Number(event.target.value))}
              />
              <span>{backgroundTransparency}%</span>
            </label>
            {!metadata.flow_vectors_available && (
              <span className="result-muted">この解析には流れベクトルデータがありません。</span>
            )}
          </div>

          {showTimeline && (
            <div className="result-timeline">
              <button type="button" onClick={() => setPlaying((value) => !value)} aria-label={playing ? "一時停止" : "再生"}>
                {playing ? "Pause" : "Play"}
              </button>
              <label>
                <input type="checkbox" checked={loop} onChange={(event) => setLoop(event.target.checked)} />
                Loop
              </label>
              <button type="button" onClick={() => void exportGif()} disabled={gifProgress !== null}>GIF</button>
              {gifError && <p role="alert">GIFを出力できませんでした: {gifError}</p>}
              {gifProgress !== null && (
                <>
                  <progress max={1} value={gifProgress} />
                  <button type="button" onClick={() => { gifCancelRef.current = true; }}>Cancel</button>
                </>
              )}
              <span className="result-vector-note">GIFではCORS制約を避けるため背景地図を省略します。</span>
              <button
                type="button"
                disabled={timePosition <= 0}
                onClick={() => setTimePosition((value) => Math.max(0, value - 1))}
                aria-label="前の時刻"
              >
                ◀
              </button>
              <input
                aria-label="結果時刻"
                type="range"
                min={0}
                max={maxTimePosition}
                step={1}
                value={timePosition}
                onChange={(event) => setTimePosition(Number(event.target.value))}
              />
              <button
                type="button"
                disabled={timePosition >= maxTimePosition}
                onClick={() => setTimePosition((value) => Math.min(maxTimePosition, value + 1))}
                aria-label="次の時刻"
              >
                ▶
              </button>
              <strong>現在: {formatElapsed(selectedTimeIndex ?? 0)}</strong>
              {magic && <>
                <span>{elapsedSeconds(metadata.time_values, selectedTimeIndex ?? 0) < magic.casting_seconds ? (magic.release_mode === "initial" ? "初期水塊の追跡中" : "魔法発動中") : "緩和中"}</span>
              </>}
            </div>
          )}
        </div>

        <div className="result-layout">
          <div className="result-map-panel">
            <ResultMap
              magicGeometry={showMagicFootprint ? magicGeometry : null}
              metadata={metadata}
              imageUrl={gifFrameUrl ?? imageUrl}
              flowVectorData={flowVectorData}
              nextFlowVectorData={playing ? nextFlowVectorData : null}
              flowInterpolation={flowInterpolationRef}
              flowSpeedRange={flowSpeedRange}
              flowSpeedBreaks={flowSpeedBreaks}
              flowDisplayMode={flowMode === "off" ? null : flowMode}
              backgroundOpacity={(100 - backgroundTransparency) / 100}
              mapLabel={mapLabel}
              focusPoint={mapFocusPoint}
              onInspect={handleInspect}
              onFlowRenderStats={setFlowRenderStats}
              onViewportChange={handleViewportChange}
              onCaptureReady={(capture) => { captureRef.current = capture; }}
            />
          </div>

          <aside className="result-sidebar">
            <section className="result-point-panel">
              <h3>地点</h3>
              {energyProgressVisible && <p role="status">{`エネルギー集計中 ${energyJob?.progress_percent ?? 0}%（${energyJob?.processed_frames ?? 0} / ${energyJob?.total_frames ?? availableTimeIndices.length}時刻）`}</p>}
              {pointEnergyLoading && !energyProgressVisible && <p role="status">比較データを読み込んでいます…</p>}
              {extremaLoading && !energyCalculating && extremaMetric !== null && <>
                <p role="status">{`${extremaMetric === "depth" ? "最大深度" : "最高速度"}集計中 ${extremaJob?.progress_percent ?? 0}%${extremaJob && extremaJob.total_frames > 0 ? `（${extremaJob.processed_frames} / ${extremaJob.total_frames} 区画×時刻）` : "（準備中）"}`}</p>
                <progress aria-label="水深・流速集計の進捗" max={100} value={extremaJob?.progress_percent ?? 0} />
              </>}
              {!extremaLoading && extremaJob?.status === "complete" && <p role="status">最大深度・最高速度集計完了 100%</p>}
              {energyProgressVisible && <progress aria-label="エネルギー集計の進捗" max={100} value={energyJob?.progress_percent ?? 0} />}
              {!energyProgressVisible && energyJob?.status === "complete" && <p role="status">エネルギー集計完了 100%</p>}
              {pointEnergyError && <p className="smoke-error">{pointEnergyError}</p>}
              {extremaError && <p className="smoke-error">{extremaError}</p>}
              {rankedPoint && (
                <p data-testid="point-rank">
                  {rankedPoint.metric === "depth" ? "最高水深" : rankedPoint.metric === "speed" ? "最高流速" : "最高総エネルギー"} 第{rankedPoint.entry.rank}位 / {rankedPoint.metric === "energy" ? energyJob?.energy?.length : extrema?.[rankedPoint.metric].length}地点
                  · {rankedPoint.metric === "energy" ? `${(rankedPoint.entry.total_energy_j ?? 0).toFixed(2)} J` : rankedPoint.metric === "depth" ? metres(rankedPoint.entry.depth_m) : kmhLabel((rankedPoint.entry.speed_mps ?? 0) * 3.6)}
                  · {elapsedLabel(metadata.time_values, rankedPoint.entry.time_index, timeUnit)}
                </p>
              )}
              {inspectionLoading && <p>地点データを読み込んでいます…</p>}
              {inspectionError && <p className="smoke-error">{inspectionError}</p>}
              {!inspectionLoading && !inspection && (
                <p>地図をクリックすると、その地点の計算値を確認できます。</p>
              )}
              {inspection && !inspection.has_data && (
                <p>この地点には解析データがありません。</p>
              )}
              {inspection?.has_data && (
                <dl>
                  <dt>緯度</dt><dd>{inspection.lat_deg.toFixed(6)}</dd>
                  <dt>経度</dt><dd>{inspection.lon_deg.toFixed(6)}</dd>
                  <dt>地盤高</dt><dd>{metres(inspection.terrain_elevation_m)}</dd>
                  <dt>最大浸水深</dt><dd>{metres(inspection.max_depth_m)}</dd>
                  <dt>最大時刻</dt>
                  <dd>
                    {inspection.max_time_index == null
                      ? "—"
                      : elapsedValueLabel(
                          metadata.time_values,
                          inspection.max_time_value,
                          inspection.max_time_index,
                          timeUnit,
                        )}
                  </dd>
                  {layer === "time_depth" && (
                    <>
                      <div className="result-point-water-motion">
                        <div><dt>現在水深</dt><dd>{inspection.depth_m == null ? "—" : `${inspection.depth_m.toFixed(2)} m`}</dd></div>
                        <div><dt>流速</dt><dd>{inspection.speed_mps == null ? "—" : `${inspection.speed_mps.toFixed(2)} m/s`}</dd></div>
                      </div>
                      <dt>測定時刻</dt><dd>{elapsedValueLabel(metadata.time_values, inspection.time_value, selectedTimeIndex ?? 0, timeUnit)}</dd>
                    </>
                  )}
                  <dt>格子</dt><dd>{metres(inspection.grid_resolution_m)}</dd>
                </dl>
              )}
              {comparison && (rankedPoint || manualComparison) && (
                <section aria-label="比較" className="result-energy-comparison">
                  <h4>比較</h4>
                  <p><strong>{comparison.name}（{comparison.referenceMassKg.toLocaleString()} kg）が{comparisonSpeedLabel(comparison)}で動くときと同じ運動エネルギー</strong></p>
                  <p>{integratedComparison ? `1 m区画の累積流出量 ${(manualComparison?.total_outflow_m3 ?? rankedPoint?.entry.total_outflow_m3 ?? 0).toFixed(2)} m³ · 総エネルギー ${comparison.energyJ.toFixed(2)} J` : `この地点の水 ${comparison.waterMassKg.toFixed(1)} kg · 流速 ${kmhLabel((rankedPoint?.entry.speed_mps ?? 0) * 3.6)}`}</p>
                  {manualComparison && <p>積算終了時刻: {elapsedLabel(metadata.time_values, manualComparison.through_time_index, timeUnit)}{manualComparison.rank != null && ` · 総エネルギー 第${manualComparison.rank}位`}</p>}
                  <details>
                    <summary>換算の条件</summary>
                    <p>{integratedComparison ? energyJob?.method : `1セル ${(rankedPoint?.entry.cell_area_m2 ?? 1).toFixed(2)} m² × 水深 ${(rankedPoint?.entry.depth_m ?? 0).toFixed(3)} m、水の密度1,000 kg/m³。運動エネルギー ½mv² を比較します。`}プリウス換算が時速30 km未満なら力士、30〜120 kmならプリウス、120 kmを超える場合は新幹線1車両で換算します。</p>
                    <p>力士は秒速と100 m走の所要時間で表示します。所要時間は100 m÷換算速度で、一定速度で走ると仮定した比較です。</p>
                    <p>プリウスG・2WDは<a href="https://toyota.jp/pages/contents/prius/005_p_001/pdf/prius_spec_202610.pdf" target="_blank" rel="noreferrer">主要諸元</a>の1,400 kg、力士は<a href="https://sumo.or.jp/Entertainment/quiz/1322/" target="_blank" rel="noreferrer">体重の目安</a>を丸めた150 kg。セル内の水の比較で、衝突力の推定ではありません。</p>
                    <p>新幹線1車両は比較用に44,000 kgとします。<a href="https://jr-central.co.jp/news/release/_pdf/000030998.pdf" target="_blank" rel="noreferrer">JR東海のN700S確認試験車資料</a>の編成重量上限700 tを16両で割った43.75 tを約44 tに丸めた代表値です。号車ごとの実測質量ではありません。</p>
                  </details>
                </section>
              )}
            </section>

            <div className={`result-legends${flowVisible && metadata.flow_vectors_available ? " result-legends-parallel" : ""}`}>
            <section className="result-legend result-legend-sidebar" aria-label={layer === "grid_resolution" ? "計算格子の凡例" : layer === "elevation" ? "標高の凡例" : "浸水深の凡例"}>
              {layer !== "grid_resolution" && layer !== "elevation" ? (
                <>
                  <strong>{layer === "max_depth" ? "最大浸水深 (m)" : "浸水深 (m)"}</strong>
                  {metadata.depth_legend?.map((item) => (
                    <span key={item.label}>
                      <i style={{ backgroundColor: item.color }} aria-hidden="true" />
                      {item.label}
                    </span>
                  ))}

                </>
              ) : layer === "grid_resolution" ? (
                <>
                  <strong>格子解像度</strong>
                  {GRID_LEGEND.map(([label, color]) => (
                    <span key={label}>
                      <i style={{ backgroundColor: color }} aria-hidden="true" />
                      {label}
                    </span>
                  ))}
                  <span className="result-vector-note">実計算格子: 1 m</span>
                </>
              ) : (
                <>
                  <strong>標高 (m)</strong>
                  {metadata.elevation_legend?.map((item) => (
                    <span key={item.label}>
                      <i style={{ backgroundColor: item.color }} aria-hidden="true" />
                      {item.label}
                    </span>
                  ))}
                </>
              )}
            </section>

            {flowVisible && metadata.flow_vectors_available && (
              <section className="result-legend result-legend-sidebar result-flow-legend" aria-label="流速の凡例">
                <strong>流速 (m/s)</strong>
                {flowLegend.map(([label, color]) => (
                  <span key={`${label}-${color}`}>
                    <i style={{ backgroundColor: color }} aria-hidden="true" />
                    {label}
                  </span>
                ))}
                <span className="result-vector-note">
                  {flowMode === "particles"
                    ? `粒子の進行方向: ${flowVectorData?.flow_field ? "解析セルの流速" : "補間したベクトル場"} / 移動速度と色: 流速 / 寿命: 最低15ベクトル間隔 / 開始位相: 4群`
                    : "矢印の向き: 流向 / 色: 流速"}
                </span>
                {flowLoading && <span className="result-vector-note">流れベクトルを読み込み中…</span>}
                {flowError && <span className="result-warning">流れベクトルを読み込めません: {flowError}</span>}
                {!flowLoading && !flowError && flowVectorData && (
                  <>
                    <span className="result-vector-note">
                      {flowMode === "particles" ? "粒子の発生点" : "矢印"}: {flowVectorData.metadata.arrow_count.toLocaleString()}
                      {flowMode === "particles" ? "個" : "本"}
                    </span>
                    {flowMode === "vectors" && flowRenderStats && (
                      <span className="result-vector-note">
                        描画した矢印: {(flowRenderStats.canvasArrowCount ?? flowRenderStats.svgArrowCount).toLocaleString()}本
                      </span>
                    )}
                    {flowMode === "vectors" && flowRenderStats &&
                      flowVectorData.metadata.arrow_count > 0 &&
                      flowRenderStats.renderedFeatureCount === 0 &&
                      flowRenderStats.svgArrowCount === 0 && (flowRenderStats.canvasArrowCount ?? 0) === 0 && (
                        <span className="result-warning">
                          GeoJSONは存在しますが、現在のMapLibre表示範囲では描画featureが0件です。
                          <br />
                          layer順: {flowRenderStats.layerOrder.join(" > ")}
                          <br />
                          feature範囲: {flowRenderStats.featureBounds
                            ? flowRenderStats.featureBounds.map((value) => value.toFixed(6)).join(", ")
                            : "—"}
                          <br />
                          map範囲: {flowRenderStats.mapBounds
                            .map((value) => value.toFixed(6))
                            .join(", ")}
                        </span>
                      )}
                  </>
                )}
                {!flowLoading &&
                  !flowError &&
                  flowVectorData &&
                  flowVectorData.metadata.arrow_count === 0 && (
                    <span className="result-vector-note">この時刻には表示可能な流れがありません。</span>
                  )}
              </section>
            )}

            </div>
            <div className="result-sidebar-extra">
              <details>
                <summary>結果概要</summary>
                <dl>
                  <dt>最大浸水深</dt>
                  <dd>{typeof globalMax === "number" ? `${globalMax.toFixed(3)} m` : "—"}</dd>
                  <dt>建物データ</dt><dd>{provider?.building_provider ?? "—"}</dd>
                  <dt>道路データ</dt><dd>{provider?.road_provider ?? "—"}</dd>
                  <dt>SFINCS</dt><dd>{engine?.sfincs_version ?? "—"}</dd>
                </dl>
                {provider?.warnings?.map((warning) => (
                  <p className="result-warning" key={warning}>{warning}</p>
                ))}
              </details>

              <details>
                <summary>解析条件と出典</summary>
                <p>
                  範囲: {metadata.bounds.south_deg.toFixed(6)}, {metadata.bounds.west_deg.toFixed(6)}
                  {" — "}
                  {metadata.bounds.north_deg.toFixed(6)}, {metadata.bounds.east_deg.toFixed(6)}
                </p>
                <p>
                  Grid: {Object.entries(metadata.grid_level_summary)
                    .map(([level, count]) => `${level}: ${count.toLocaleString()}`)
                    .join(" / ")}
                </p>
                <p>Application: {runSummary.application_version}</p>
                <p>Accuracy: {runSummary.requested_accuracy_mode}</p>
                <p>Flow vectors: {metadata.flow_vectors_available ? "available" : "not stored"}</p>
                <p>Rainfall: <code>{JSON.stringify(runSummary.rainfall_source)}</code></p>
                <p>Elevation: <code>{JSON.stringify(runSummary.elevation_source_summary)}</code></p>
                <p>Elevation provider counts: <code>{JSON.stringify(runSummary.elevation_provider_counts)}</code></p>
                <p>Manning: <code>{JSON.stringify(runSummary.manning_defaults)}</code></p>
                <p>Boundary: {runSummary.boundary_policy}</p>
                <p>Roof-rain mass diagnostic: <code>{JSON.stringify(runSummary.roof_rain_mass_diagnostic)}</code></p>
                <p>HydroMT-SFINCS: {engine?.hydromt_sfincs_version ?? "—"}</p>
                <p className="result-policy">{metadata.no_data_policy}</p>
              </details>

              <details open>
                <summary>モデルの主な制約</summary>
                <ul>
                  {omittedLimitations.map((label) => <li key={label}>{label}</li>)}
                  <li>{magic ? (magic.release_mode === "initial" ? "開始時に建物を除く地表へ全量と初速を配置し、その後は追加給水・強制駆動しません。" : "建物を除く効果範囲内の地表に総水量を配分します。給水以外の初期水量はゼロです。") : "屋根雨水は周囲の地表へ質量保存で再配分する近似です。"}</li>
                </ul>
              </details>
            </div>
          </aside>
        </div>
      </div>
    </section>
  );
}

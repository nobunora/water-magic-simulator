import type { ResultMetadataResponse, RunConfig } from "../api/client";
import type { MagicGeometry } from "./MagicResultFootprint";

export type SavedMagic = NonNullable<RunConfig["water_magic"]>;

export function savedMagic(metadata: ResultMetadataResponse): SavedMagic | null {
  const source = metadata.run_summary.rainfall_source;
  if (source?.kind !== "water_magic" || typeof source.configuration_json !== "string") return null;
  try {
    const value = JSON.parse(source.configuration_json) as SavedMagic;
    if (!Number.isFinite(value.casting_seconds) || !Number.isFinite(value.relaxation_seconds)) return null;
    return value;
  } catch {
    return null;
  }
}

export function magicTimeUnit(casting: number): "seconds" | "minutes" {
  return casting < 300 ? "seconds" : "minutes";
}

export function magicTimeLabel(seconds: number, unit: "seconds" | "minutes"): string {
  // Keep subminute output distinctions visible even when the unit is minutes.
  return unit === "seconds" ? `${Number(seconds.toFixed(3))}秒` : `${Number((seconds / 60).toFixed(3))}分`;
}

export function elapsedSeconds(values: string[], index: number): number {
  const numeric = /^-?\d+(?:\.\d+)?$/;
  return numeric.test(values[0]) && numeric.test(values[index])
    ? Number(values[index]) - Number(values[0])
    : (Date.parse(values[index]) - Date.parse(values[0])) / 1000;
}

export function savedMagicGeometry(metadata: ResultMetadataResponse): MagicGeometry | null {
  const magic = savedMagic(metadata);
  const report = metadata.run_summary.rainfall_source?.source_report_json;
  if (!magic || typeof report !== "string") return null;
  try {
    const geometry = JSON.parse(report).geographic_geometry;
    if (geometry?.type !== "Polygon" || !Array.isArray(geometry.coordinates?.[0])) return null;
    if (!geometry.coordinates[0].every((p: unknown) => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite))) return null;
    return { coordinates: geometry.coordinates, footprintKind: magic.footprint_kind };
  } catch { return null; }
}

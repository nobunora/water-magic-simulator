import type { ResultMetadataResponse } from "../api/client";

/** Select only retained, uniquely timed outputs; preserve their native indices. */
export function availableResultTimes(metadata: Pick<ResultMetadataResponse, "available_time_indices" | "time_values">): number[] {
  const seen = new Set<number>();
  return metadata.available_time_indices.filter(index => {
    if (!Number.isInteger(index) || index < 0 || index >= metadata.time_values.length) return false;
    const value = metadata.time_values[index];
    const time = /^-?\d+(?:\.\d+)?$/.test(value) ? Number(value) : Date.parse(value);
    if (!Number.isFinite(time) || seen.has(time)) return false;
    seen.add(time);
    return true;
  });
}

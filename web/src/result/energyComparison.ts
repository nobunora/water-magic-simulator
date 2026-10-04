/** Equivalent kinetic energy for the water contained in one native cell. */
export function energyComparison(depthM: number, cellAreaM2: number, speedMps: number | null) {
  if (speedMps == null || ![depthM, cellAreaM2, speedMps].every(Number.isFinite) || depthM < 0 || cellAreaM2 <= 0 || speedMps < 0) return null;
  const waterMassKg = 1000 * depthM * cellAreaM2;
  return cumulativeEnergyComparison(0.5 * waterMassKg * speedMps ** 2, waterMassKg / 1000);
}

/** Equivalent speed for the integrated energy leaving a one-metre block. */
export function cumulativeEnergyComparison(energyJ: number, volumeM3: number) {
  if (![energyJ, volumeM3].every(Number.isFinite) || energyJ < 0 || volumeM3 < 0) return null;
  const waterMassKg = 1000 * volumeM3;
  const priusKmh = 3.6 * Math.sqrt(2 * energyJ / 1400);
  const useSumo = priusKmh < 30;
  // Compare energy directly so floating-point speed rounding keeps exactly 120 km/h as Prius.
  const useShinkansen = energyJ > 0.5 * 1400 * (120 / 3.6) ** 2;
  // Comparison reference: N700S's 700t upper train mass / 16 cars, rounded to 44t.
  const referenceMassKg = useSumo ? 150 : useShinkansen ? 44_000 : 1400;
  const equivalentSpeedMps = Math.sqrt(2 * energyJ / referenceMassKg);
  return {
    name: useSumo ? "力士" : useShinkansen ? "新幹線1車両" : "プリウス",
    referenceMassKg,
    speedKmh: 3.6 * equivalentSpeedMps,
    speedMps: equivalentSpeedMps,
    hundredMetreSeconds: equivalentSpeedMps > 0 ? 100 / equivalentSpeedMps : null,
    waterMassKg,
    energyJ,
  };
}

export function kmhLabel(value: number): string {
  return value > 0 && value < 0.1 ? "時速0.1 km未満" : `時速${value.toFixed(1)} km`;
}

export function comparisonSpeedLabel(comparison: NonNullable<ReturnType<typeof energyComparison>>): string {
  if (comparison.name !== "力士") return kmhLabel(comparison.speedKmh);
  if (comparison.hundredMetreSeconds === null) return "静止（秒速0 m・100 m走換算不可）";
  const speed = comparison.speedMps < 0.01 ? "0.01 m未満" : `${comparison.speedMps.toFixed(2)} m`;
  const time = Number.isFinite(comparison.hundredMetreSeconds) ? `${comparison.hundredMetreSeconds.toFixed(1)}秒` : "算出範囲外";
  return `秒速${speed}（100 m走換算: ${time}）`;
}

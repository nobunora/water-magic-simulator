import { describe, expect, it } from "vitest";
import { comparisonSpeedLabel, cumulativeEnergyComparison, energyComparison, kmhLabel } from "./energyComparison";

describe("water kinetic energy comparisons", () => {
  it.each([[119.99, "プリウス"], [120, "プリウス"], [120.01, "新幹線1車両"]])(
    "selects the reference at Prius-equivalent %s km/h", (speed, name) => {
      const energy = 0.5 * 1400 * (Number(speed) / 3.6) ** 2;
      const comparison = cumulativeEnergyComparison(energy, 10)!;
      expect(comparison.name).toBe(name);
      expect(comparisonSpeedLabel(comparison)).toBe(kmhLabel(comparison.speedKmh));
      expect(0.5 * comparison.referenceMassKg * comparison.speedMps ** 2).toBeCloseTo(energy);
    },
  );
  it("uses a single Shinkansen car and recalculates its equivalent speed", () => {
    const comparison = energyComparison(5.6, .25, 50)!;
    expect(comparison.name).toBe("新幹線1車両");
    expect(comparison.referenceMassKg).toBe(44000);
    expect(comparison.speedKmh).toBeCloseTo(180 * Math.sqrt(1400 / 44000));
    expect(comparison.energyJ).toBe(1750000);
  });
  it("uses integrated energy independently of the final water depth and speed", () => {
    const comparison = cumulativeEnergyComparison(70000, 10)!;
    expect(comparison.speedKmh).toBeCloseTo(36);
    expect(comparison.waterMassKg).toBe(10000);
    expect(comparison.energyJ).toBe(70000);
    expect(cumulativeEnergyComparison(-1, 10)).toBeNull();
  });
  it("uses the Prius above the display threshold and preserves equal energy", () => {
    const comparison = energyComparison(5.6, 0.25, 10)!;
    expect(comparison.name).toBe("プリウス");
    expect(comparison.speedKmh).toBeCloseTo(36);
    expect(comparisonSpeedLabel(comparison)).toBe("時速36.0 km");
    expect(0.5 * comparison.referenceMassKg * (comparison.speedKmh / 3.6) ** 2).toBeCloseTo(comparison.energyJ);
  });
  it("uses Prius from exactly 30 km/h and sumo below it", () => {
    expect(energyComparison(5.6, 0.25, 30 / 3.6)?.name).toBe("プリウス");
    expect(energyComparison(5.6, 0.25, 29.99 / 3.6)?.name).toBe("力士");
  });
  it("switches small values to a 150 kg sumo wrestler", () => {
    const comparison = energyComparison(0.6, 0.25, 1)!;
    expect(comparison.name).toBe("力士");
    expect(comparison.speedKmh).toBeCloseTo(3.6);
    expect(comparison.waterMassKg).toBeCloseTo(150);
    expect(comparison.speedMps).toBeCloseTo(1);
    expect(comparison.hundredMetreSeconds).toBeCloseTo(100);
    expect(comparisonSpeedLabel(comparison)).toBe("秒速1.00 m（100 m走換算: 100.0秒）");
  });
  it("handles still water, unavailable data and tiny nonzero values", () => {
    expect(energyComparison(1, 0.25, 0)?.speedKmh).toBe(0);
    expect(comparisonSpeedLabel(energyComparison(1, 0.25, 0)!)).toBe("静止（秒速0 m・100 m走換算不可）");
    expect(energyComparison(1, 0.25, null)).toBeNull();
    expect(energyComparison(-1, 0.25, 1)).toBeNull();
    expect(energyComparison(1, 0, 1)).toBeNull();
    expect(kmhLabel(0.03)).toBe("時速0.1 km未満");
  });
});

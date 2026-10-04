import { describe, expect, it } from "vitest";
import { minimumMagicHalfSize, recommendedMagicHalfSize } from "./magicAnalysisArea";
import { spellSettings } from "./magicCatalog";

describe("effect extent margin", () => {
  it("compares the longest rectangle dimension with the half-size presets", () => {
    const magic = { ...spellSettings("school-pool", 139, 35), length: "80", width: "20" };
    expect(minimumMagicHalfSize(magic)).toBe(120);
    expect(recommendedMagicHalfSize(magic)).toBe(250);
    expect(recommendedMagicHalfSize({ ...magic, length: "20", width: "80" })).toBe(250);
  });
  it("uses the radius for round footprints and includes the boundary", () => {
    const magic = { ...spellSettings("dq7-maelstrom", 139, 35), radius: "80" };
    expect(recommendedMagicHalfSize(magic)).toBe(250);
    expect(recommendedMagicHalfSize({ ...magic, radius: "500" })).toBe(1000);
    expect(recommendedMagicHalfSize({ ...magic, radius: String(100 / 1.5) })).toBe(100);
    expect(recommendedMagicHalfSize({ ...magic, radius: "4000" })).toBeNull();
  });
});

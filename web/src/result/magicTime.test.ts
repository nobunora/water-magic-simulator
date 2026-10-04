import { describe, expect, it } from "vitest";
import { elapsedSeconds, magicTimeLabel, magicTimeUnit } from "./magicTime";

describe("magic result time units", () => {
  it("uses casting duration alone; five minutes starts minute display", () => {
    expect(magicTimeUnit(60)).toBe("seconds");
    expect(magicTimeUnit(299)).toBe("seconds");
    expect(magicTimeUnit(300)).toBe("minutes");
    expect(magicTimeLabel(600, magicTimeUnit(6))).toBe("600秒");
    expect(magicTimeLabel(301, magicTimeUnit(300))).toBe("5.017分");
    expect(magicTimeLabel(1, "minutes")).toBe("0.017分");
  });
  it("preserves native elapsed seconds in numeric and timestamp archives", () => {
    expect(elapsedSeconds(["0", "5", "65"], 1)).toBe(5);
    expect(elapsedSeconds(["2000-01-01T00:00:00", "2000-01-01T00:00:05"], 1)).toBe(5);
  });
});

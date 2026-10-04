import { describe, expect, it } from "vitest";
import { availableResultTimes } from "./resultTime";

describe("retained result times", () => {
  it("keeps sparse native output indices and excludes missing or duplicate times", () => {
    expect(availableResultTimes({ available_time_indices: [0, 3, 3, 4, 5, 9, -1, 1.5], time_values: ["0", "1", "2", "6", "bad", "6"] })).toEqual([0, 3]);
  });
  it("retains every actual late frame even when the changes are small", () => {
    expect(availableResultTimes({ available_time_indices: [0, 1, 2], time_values: ["2000-01-01T00:01:09.600Z", "2000-01-01T00:01:09.800Z", "2000-01-01T00:01:10.000Z"] })).toEqual([0, 1, 2]);
  });
});

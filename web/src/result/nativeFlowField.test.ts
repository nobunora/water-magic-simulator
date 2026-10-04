import { describe, expect, it } from "vitest";
import { projectNativeFlowField } from "./nativeFlowField";

describe("native flow field", () => {
  it("reads four distinct half-metre solver cells including blocked cells", () => {
    const bytes = new Uint8Array(32);
    const view = new DataView(bytes.buffer);
    [1, 0, 0, 2, -3, 0, NaN, NaN].forEach((value, index) => view.setFloat32(index * 4, value, true));
    const field = projectNativeFlowField({encoding: "float32-le-uv-base64", width: 2,
      height: 2, cell_size_m: 0.5, row_order: "south-to-north", corners: [[0, 0], [1, 0], [0, 1]],
      data: btoa(String.fromCharCode(...bytes))}, ([x, y]) => ({ x: x * 20, y: -y * 20 }));
    expect(field.sample(5, -5)?.speedMps).toBe(1);
    expect(field.sample(15, -5)).toMatchObject({ dx: 0, dy: -1, speedMps: 2 });
    expect(field.sample(5, -15)?.dx).toBe(-1);
    expect(field.sample(5, -15)?.speedMps).toBe(3);
    expect(field.sample(15, -15)).toBeNull();
    expect(field.sample(-1, 0)).toBeNull();
    expect(field.sample(20, 0)).toBeNull();
    expect(field.vector(0, 0)).toBeNull();
    expect(field.vector(0.5, 0)?.speedMps).toBe(0.5);
  });
});

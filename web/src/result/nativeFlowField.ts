import type { FlowVectorFeatureCollection } from "../api/client";

type Point = { x: number; y: number };
type NativeField = NonNullable<FlowVectorFeatureCollection["flow_field"]>;
const decoded = new WeakMap<NativeField, Float32Array>();

/** Project once per viewport; each particle then reads its own native cell in O(1). */
export function projectNativeFlowField(field: NativeField, project: (coordinate: number[]) => Point) {
  let velocities = decoded.get(field);
  if (!velocities) {
    const bytes = Uint8Array.from(atob(field.data), (character) => character.charCodeAt(0));
    if (bytes.length !== field.width * field.height * 8) throw new Error("Invalid native flow field size");
    const view = new DataView(bytes.buffer);
    velocities = new Float32Array(bytes.length / 4);
    for (let index = 0; index < velocities.length; index++) velocities[index] = view.getFloat32(index * 4, true);
    decoded.set(field, velocities);
  }
  const [origin, east, north] = field.corners.map(project);
  const ex = (east.x - origin.x) / field.width;
  const ey = (east.y - origin.y) / field.width;
  const nx = (north.x - origin.x) / field.height;
  const ny = (north.y - origin.y) / field.height;
  const determinant = ex * ny - ey * nx;
  const spacingPx = Math.min(Math.hypot(ex, ey), Math.hypot(nx, ny));
  const vector = (u: number, v: number) => {
    const speedMps = Math.hypot(u, v);
    const dx = u * ex + v * nx, dy = u * ey + v * ny;
    const length = Math.hypot(dx, dy);
    return length > 1e-9 ? { dx: dx / length, dy: dy / length, speedMps, uMps: u, vMps: v } : null;
  };
  return {
    spacingPx,
    vector,
    sample(x: number, y: number) {
      if (Math.abs(determinant) < 1e-12) return null;
      const px = x - origin.x, py = y - origin.y;
      const column = Math.floor((px * ny - py * nx) / determinant);
      const row = Math.floor((py * ex - px * ey) / determinant);
      if (row < 0 || column < 0 || row >= field.height || column >= field.width) return null;
      const offset = (row * field.width + column) * 2;
      const u = velocities[offset], v = velocities[offset + 1];
      if (!Number.isFinite(u) || !Number.isFinite(v)) return null;
      return vector(u, v);
    },
  };
}

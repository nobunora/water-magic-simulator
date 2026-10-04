type ScreenPoint = { x: number; y: number };

// Offset each screen-space edge, then intersect adjacent offset lines.
// The visual inset stays in CSS pixels even when the map rotates or zooms.
export function insetScreenPolygon(points: ScreenPoint[], insetPx: number): ScreenPoint[] {
  if (points.length < 3) return [];
  const center = points.reduce((sum, point) => ({ x: sum.x + point.x / points.length, y: sum.y + point.y / points.length }), { x: 0, y: 0 });
  const edges = points.map((start, index) => {
    const end = points[(index + 1) % points.length];
    const length = Math.hypot(end.x - start.x, end.y - start.y);
    if (length < 1e-9) return null;
    let nx = -(end.y - start.y) / length;
    let ny = (end.x - start.x) / length;
    if (nx * (center.x - start.x) + ny * (center.y - start.y) < 0) { nx = -nx; ny = -ny; }
    return { nx, ny, c: nx * start.x + ny * start.y + insetPx };
  });
  const result: ScreenPoint[] = [];
  for (let index = 0; index < edges.length; index++) {
    const previous = edges[(index + edges.length - 1) % edges.length];
    const current = edges[index];
    if (!previous || !current) return [];
    const determinant = previous.nx * current.ny - current.nx * previous.ny;
    if (Math.abs(determinant) < 1e-9) return [];
    const point = {
      x: (previous.c * current.ny - current.c * previous.ny) / determinant,
      y: (previous.nx * current.c - current.nx * previous.c) / determinant,
    };
    if (edges.some((edge) => !edge || edge.nx * point.x + edge.ny * point.y < edge.c - 1e-6)) return [];
    result.push(point);
  }
  return result;
}

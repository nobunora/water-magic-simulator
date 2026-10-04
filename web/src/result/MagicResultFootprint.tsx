import { useEffect, useRef } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { insetScreenPolygon } from "../dev/magicFootprintGeometry";

export type MagicGeometry = { coordinates: number[][][]; footprintKind: "circle" | "domain" | "rectangle" | "sector" };

export default function MagicResultFootprint({ map, geometry }: {
  map: MapLibreMap | null; geometry: MagicGeometry | null;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  useEffect(() => {
    const svg = svgRef.current;
    if (!map || !svg || !geometry) return;
    const update = () => {
      svg.setAttribute("viewBox", `0 0 ${svg.clientWidth} ${svg.clientHeight}`);
      const projected = geometry.coordinates[0].map(([lon, lat]) => map.project([lon, lat]));
      const points = geometry.footprintKind === "domain"
        ? insetScreenPolygon(projected.slice(0, -1), 5) : projected;
      const value = points.map(({ x, y }) => `${x},${y}`).join(" ");
      svg.querySelectorAll("polygon").forEach(polygon => polygon.setAttribute("points", value));
    };
    update();
    map.on("move", update);
    map.on("load", update);
    map.on("resize", update);
    return () => { map.off("move", update); map.off("load", update); map.off("resize", update); };
  }, [map, geometry]);
  if (!geometry) return null;
  return <svg ref={svgRef} className="magic-footprint" role="img" aria-label="解析した魔法の効果範囲" style={{ pointerEvents: "none" }}>
    <polygon fill="none" stroke="#111827" strokeWidth="10" strokeLinejoin="round" />
    <polygon fill="none" stroke="#2563eb" strokeWidth="5" strokeLinejoin="round" />
  </svg>;
}

import { useEffect, useRef, useState, type RefObject, type PointerEvent } from "react";
import type { Map as MapLibreMap } from "maplibre-gl";
import type { AnalysisArea } from "../api/client";
import { insetScreenPolygon } from "./magicFootprintGeometry";
import { spellById } from "./magicCatalog";
import { magicAnimation } from "./magicAssets";

export type MagicPreview = {
  spellId: string; length: string; width: string; sectorAngle: string;
  lat: number; lon: number; volume: string; radius: string;
  casting: string; relaxation: string; bearing: string; visible: boolean; playing: boolean;
  opacity: number; size: number;
  releaseMode: "continuous" | "initial";
  initialMotion: "none" | "directional" | "radial" | "vortex";
  initialSpeed: string; vortexDirection: "clockwise" | "counterclockwise"; vortexCoreRadius: string;
  footprintKind: "circle" | "domain" | "rectangle" | "sector";
};

type Props = {
  mapRef: RefObject<MapLibreMap | null>;
  magic: MagicPreview | null;
  analysisArea?: AnalysisArea | null;
  disabled: boolean;
  onBearingChange?: (bearing: string) => void;
};

type ScreenPoint = { x: number; y: number };

function distanceToSegment(point: ScreenPoint, start: ScreenPoint, end: ScreenPoint) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const lengthSquared = dx * dx + dy * dy;
  const t = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared));
  return Math.hypot(point.x - start.x - t * dx, point.y - start.y - t * dy);
}

export default function MagicMapPreview({ mapRef, magic, analysisArea, disabled, onBearingChange }: Props) {
  const imageRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const directionRef = useRef<SVGSVGElement>(null);
  const handleRef = useRef<HTMLButtonElement>(null);
  const dragRef = useRef<{ pointerId: number; restorePan: boolean } | null>(null);
  const hoverPointRef = useRef<ScreenPoint | null>(null);
  const arrowGeometryRef = useRef<{ start: ScreenPoint; end: ScreenPoint } | null>(null);
  const refreshHoverRef = useRef<(() => void) | null>(null);
  const [failedAsset, setFailedAsset] = useState<string | null>(null);
  const spell = spellById(magic?.spellId ?? "healing-rain");
  const animation = magicAnimation(spell.id);
  const source = magic?.playing ? animation.path : animation.still.path;
  useEffect(() => {
    const map = mapRef.current;
    const svg = svgRef.current;
    const image = imageRef.current;
    const direction = directionRef.current;
    const handle = handleRef.current;
    if (!map || !magic || !svg || !image) return;
    const radius = Number(magic.radius);
    if (![magic.lat, magic.lon, radius].every(Number.isFinite) || Math.abs(magic.lat) > 90 || Math.abs(magic.lon) > 180 || radius <= 0) return;
    const frame = image.parentElement?.parentElement;
    const refreshHover = () => {
      if (!handle) return;
      const geometry = arrowGeometryRef.current;
      const pointer = hoverPointRef.current;
      const bounds = map.getCanvas().getBoundingClientRect();
      const nearby = geometry && pointer && distanceToSegment({ x: pointer.x - bounds.left, y: pointer.y - bounds.top }, geometry.start, geometry.end) <= 60;
      handle.dataset.nearby = String(Boolean(dragRef.current || nearby));
    };
    refreshHoverRef.current = refreshHover;
    const onHover = (event: globalThis.PointerEvent) => {
      hoverPointRef.current = { x: event.clientX, y: event.clientY };
      refreshHover();
    };
    const onLeave = () => { hoverPointRef.current = null; refreshHover(); };
    frame?.addEventListener("pointermove", onHover, true);
    frame?.addEventListener("pointerleave", onLeave);
    const update = () => {
      const width = Math.max(1, svg.clientWidth);
      const height = Math.max(1, svg.clientHeight);
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      direction?.setAttribute("viewBox", `0 0 ${width} ${height}`);
      const center = map.project([magic.lon, magic.lat]);
      image.style.left = `${center.x}px`;
      image.style.top = `${center.y}px`;
      // Geographic preview; the saved result shows the authoritative projected geometry.
      const projectBearing = (bearing: number, distance: number) => {
        const angular = distance / 6371008.8;
        const lat = magic.lat * Math.PI / 180;
        const targetLat = Math.asin(Math.sin(lat) * Math.cos(angular) + Math.cos(lat) * Math.sin(angular) * Math.cos(bearing));
        const targetLon = magic.lon * Math.PI / 180 + Math.atan2(Math.sin(bearing) * Math.sin(angular) * Math.cos(lat), Math.cos(angular) - Math.sin(lat) * Math.sin(targetLat));
        return map.project([targetLon * 180 / Math.PI, targetLat * 180 / Math.PI]);
      };
      const bounds = analysisArea?.bounds;
      const bearingRadians = Number(magic.bearing) * Math.PI / 180;
      const projectOffset = (right: number, forward: number) => projectBearing(bearingRadians + Math.atan2(right, forward), Math.hypot(right, forward));
      const points = magic.footprintKind === "domain"
        ? bounds ? insetScreenPolygon([
          map.project([bounds.west_deg, bounds.south_deg]),
          map.project([bounds.east_deg, bounds.south_deg]),
          map.project([bounds.east_deg, bounds.north_deg]),
          map.project([bounds.west_deg, bounds.north_deg]),
        ], 5) : []
        : magic.footprintKind === "rectangle"
          ? [[-Number(magic.width)/2, 0], [Number(magic.width)/2, 0], [Number(magic.width)/2, Number(magic.length)], [-Number(magic.width)/2, Number(magic.length)]].map(([right, forward]) => projectOffset(right, forward))
          : magic.footprintKind === "sector"
            ? [center, ...Array.from({length:65}, (_, index) => projectBearing(bearingRadians + (index/64-.5)*Number(magic.sectorAngle)*Math.PI/180, radius)), center]
            : Array.from({ length: 65 }, (_, index) => projectBearing(index / 64 * 2 * Math.PI, radius));
      svg.querySelectorAll("polygon").forEach((polygon) => polygon.setAttribute("points", points.map((point) => `${point.x},${point.y}`).join(" ")));
      svg.querySelector("text")?.setAttribute("x", String(center.x));
      svg.querySelector("text")?.setAttribute("y", String(center.y - 12));
      const bearing = Number(magic.bearing);
      const validBearing = magic.bearing.trim() !== "" && Number.isFinite(bearing) && bearing >= 0 && bearing <= 360;
      if (direction) direction.style.display = validBearing ? "" : "none";
      if (handle) handle.hidden = !validBearing;
      arrowGeometryRef.current = null;
      if (validBearing) {
        const projectedTarget = projectBearing(bearing * Math.PI / 180, Math.max(radius, 40));
        const angle = Math.atan2(projectedTarget.y - center.y, projectedTarget.x - center.x);
        // Keep the decorative handle readable and reachable after close-up zoom.
        // Its screen length does not change the geographic footprint.
        const maxLength = Math.max(85, Math.min(width, height) * 0.4);
        const length = Math.min(maxLength, Math.max(85, Math.hypot(projectedTarget.x - center.x, projectedTarget.y - center.y)));
        const target = { x: center.x + length * Math.cos(angle), y: center.y + length * Math.sin(angle) };
        arrowGeometryRef.current = { start: center, end: target };
        const head = (offset: number) => `${target.x - 18 * Math.cos(angle + offset)} ${target.y - 18 * Math.sin(angle + offset)}`;
        const path = `M ${center.x} ${center.y} L ${target.x} ${target.y} M ${head(-Math.PI / 6)} L ${target.x} ${target.y} L ${head(Math.PI / 6)}`;
        direction?.querySelectorAll("path").forEach((arrow) => arrow.setAttribute("d", path));
        if (handle) {
          handle.style.left = `${target.x}px`;
          handle.style.top = `${target.y}px`;
        }
      }
      refreshHover();
    };
    update();
    map.on("move", update);
    map.on("resize", update);
    map.on("moveend", update);
    const delayedUpdate = window.setTimeout(update, 450);
    window.requestAnimationFrame(update);
    return () => {
      window.clearTimeout(delayedUpdate);
      map.off("move", update);
      map.off("resize", update);
      map.off("moveend", update);
      frame?.removeEventListener("pointermove", onHover, true);
      frame?.removeEventListener("pointerleave", onLeave);
      refreshHoverRef.current = null;
    };
  }, [mapRef, magic, analysisArea]);
  const finishDrag = () => {
    const drag = dragRef.current;
    dragRef.current = null;
    if (drag?.restorePan) mapRef.current?.dragPan.enable();
    refreshHoverRef.current?.();
  };
  useEffect(() => () => {
    if (dragRef.current?.restorePan) mapRef.current?.dragPan.enable();
  }, [mapRef]);

  const changeDirection = (event: PointerEvent<HTMLButtonElement>) => {
    if (!dragRef.current || dragRef.current.pointerId !== event.pointerId || !magic) return;
    event.preventDefault();
    event.stopPropagation();
    const map = mapRef.current;
    if (!map) return;
    const bounds = map.getCanvas().getBoundingClientRect();
    const point = map.unproject([event.clientX - bounds.left, event.clientY - bounds.top]);
    const center = map.project([magic.lon, magic.lat]);
    if (Math.hypot(event.clientX - bounds.left - center.x, event.clientY - bounds.top - center.y) < 8) return;
    const lat = magic.lat * Math.PI / 180;
    const targetLat = point.lat * Math.PI / 180;
    const deltaLon = (point.lng - magic.lon) * Math.PI / 180;
    const angle = Math.atan2(Math.sin(deltaLon) * Math.cos(targetLat), Math.cos(lat) * Math.sin(targetLat) - Math.sin(lat) * Math.cos(targetLat) * Math.cos(deltaLon));
    onBearingChange?.(String(Math.round((angle * 180 / Math.PI + 360) % 360) % 360));
  };
  if (!magic || ![magic.lat, magic.lon, Number(magic.radius)].every(Number.isFinite) || Math.abs(magic.lat) > 90 || Math.abs(magic.lon) > 180 || Number(magic.radius) <= 0) return null;
  return <div className="magic-map-preview" aria-label={`${spell.name}の配置プレビュー`}>
    <div ref={imageRef} className="magic-map-image" style={{ width: magic.size, opacity: magic.opacity }}>
      {magic.visible && (failedAsset !== source
        ? <img key={source} src={source} alt={`${spell.name} · イメージアニメ`} onError={() => setFailedAsset(source)} />
        : <div className="magic-symbol">💧 {spell.name} · イメージ欠落</div>)}
      {magic.visible && <span>{spell.name} · イメージアニメ</span>}
    </div>
    <svg ref={svgRef} className="magic-footprint" aria-label="魔法の効果範囲">
      <polygon className="magic-footprint-shadow" fill="none" stroke="#111827" strokeWidth="10" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <polygon className="magic-footprint-line" fill="#38bdf8" fillOpacity="0.15" stroke="#2563eb" strokeWidth="5" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <text textAnchor="middle">{magic.footprintKind === "domain" ? "効果範囲 · 解析範囲全体" : magic.footprintKind === "rectangle" ? `効果範囲 · ${Number(magic.length).toFixed(1)} × ${Number(magic.width).toFixed(1)} m` : `効果範囲 · 半径 ${Number(magic.radius).toFixed(1)} m`}</text>
    </svg>
    <svg ref={directionRef} className="setup-area-overlay magic-direction-overlay" aria-label={`方向 ${magic.bearing}°`}>
      <path fill="none" stroke="#111827" strokeWidth="10" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <path fill="none" stroke="#ef1b1b" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
    <button ref={handleRef} className="magic-direction-handle" type="button" aria-label="方向をドラッグして変更" title="先端をドラッグして方向を変更" disabled={disabled || !onBearingChange}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => {
        if (disabled || !onBearingChange || event.button !== 0) return;
        event.preventDefault(); event.stopPropagation();
        const map = mapRef.current;
        if (!map) return;
        dragRef.current = { pointerId: event.pointerId, restorePan: map.dragPan.isEnabled() };
        refreshHoverRef.current?.();
        map.dragPan.disable();
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={changeDirection}
      onPointerUp={(event) => {
        changeDirection(event);
        finishDrag();
        if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onPointerCancel={finishDrag} onLostPointerCapture={finishDrag}
    >↔</button>
  </div>;
}

import { fireEvent, render, screen } from "@testing-library/react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { describe, expect, it, vi } from "vitest";

import MagicMapPreview from "./MagicMapPreview";
import { initialMagic } from "./MagicMockPanel";
import { insetScreenPolygon } from "./magicFootprintGeometry";

function mapFixture() {
  return {
    project: ([lon, lat]: [number, number]) => ({ x: 200 + (lon - initialMagic.lon) * 10000, y: 200 - (lat - initialMagic.lat) * 10000 }),
    unproject: vi.fn(() => ({ lng: initialMagic.lon + 0.001, lat: initialMagic.lat })),
    getCanvas: () => ({ getBoundingClientRect: () => ({ left: 0, top: 0 }) }),
    on: vi.fn(), off: vi.fn(),
    dragPan: { isEnabled: () => true, disable: vi.fn(), enable: vi.fn() },
  };
}

function pointer(type: string, clientX = 300, clientY = 200) {
  const event = new MouseEvent(type, { bubbles: true, button: 0, clientX, clientY });
  Object.defineProperty(event, "pointerId", { value: 1 });
  return event;
}

describe("magic direction overlay", () => {
  it("insets the entire-domain footprint by five screen pixels even after rotation", () => {
    const bounds = [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 80 }, { x: 0, y: 80 }];
    const expected = [{ x: 5, y: 5 }, { x: 95, y: 5 }, { x: 95, y: 75 }, { x: 5, y: 75 }];
    expect(insetScreenPolygon(bounds, 5)).toEqual(expected);
    const rotate = (point: { x: number; y: number }) => ({ x: point.x * Math.cos(0.6) - point.y * Math.sin(0.6), y: point.x * Math.sin(0.6) + point.y * Math.cos(0.6) });
    const result = insetScreenPolygon(bounds.map(rotate), 5);
    expected.map(rotate).forEach((point, index) => {
      expect(result[index].x).toBeCloseTo(point.x, 8);
      expect(result[index].y).toBeCloseTo(point.y, 8);
    });
    expect(insetScreenPolygon([{ x: 0, y: 0 }, { x: 8, y: 0 }, { x: 8, y: 8 }, { x: 0, y: 8 }], 5)).toEqual([]);
  });
  it("renders matching black/red SVG paths and updates direction from numeric state", () => {
    const map = mapFixture();
    const mapRef = { current: map as unknown as MapLibreMap };
    const view = render(<MagicMapPreview mapRef={mapRef} magic={initialMagic} disabled={false} onBearingChange={vi.fn()} />);
    const paths = view.container.querySelectorAll(".magic-direction-overlay path");
    expect(paths[0]).toHaveAttribute("stroke", "#111827");
    expect(paths[1]).toHaveAttribute("stroke", "#ef1b1b");
    expect(paths[0].getAttribute("d")).toBe(paths[1].getAttribute("d"));
    const north = paths[0].getAttribute("d");
    const handle = screen.getByRole("button", { name: "方向をドラッグして変更" });
    expect(handle).toHaveAttribute("data-nearby", "false");
    fireEvent(view.container, pointer("pointermove", 261, 160));
    expect(handle).toHaveAttribute("data-nearby", "false");
    fireEvent(view.container, pointer("pointermove", 260, 160));
    expect(handle).toHaveAttribute("data-nearby", "true");
    view.rerender(<MagicMapPreview mapRef={mapRef} magic={{ ...initialMagic, bearing: "90" }} disabled={false} onBearingChange={vi.fn()} />);
    expect(paths[0].getAttribute("d")).not.toBe(north);
    expect(map.on).toHaveBeenCalledWith("moveend", expect.any(Function));
    const footprint = view.container.querySelectorAll(".magic-footprint polygon");
    expect(footprint[0]).toHaveAttribute("stroke", "#111827");
    expect(footprint[1]).toHaveAttribute("stroke", "#2563eb");
    expect(footprint[0].getAttribute("points")).toBe(footprint[1].getAttribute("points"));
  });

  it("converts a captured pointer drag to true bearing and restores map panning on cancel", () => {
    const map = mapFixture();
    const onBearingChange = vi.fn();
    render(<MagicMapPreview mapRef={{ current: map as unknown as MapLibreMap }} magic={initialMagic} disabled={false} onBearingChange={onBearingChange} />);
    const handle = screen.getByRole("button", { name: "方向をドラッグして変更" });
    Object.defineProperties(handle, {
      setPointerCapture: { value: vi.fn() }, hasPointerCapture: { value: () => true }, releasePointerCapture: { value: vi.fn() },
    });
    fireEvent(handle, pointer("pointerdown"));
    fireEvent(handle, pointer("pointermove"));
    expect(handle).toHaveAttribute("data-nearby", "true");
    expect(map.dragPan.disable).toHaveBeenCalledOnce();
    expect(onBearingChange).toHaveBeenLastCalledWith("90");
    fireEvent(handle, pointer("pointercancel"));
    expect(map.dragPan.enable).toHaveBeenCalledOnce();
    onBearingChange.mockClear();
    fireEvent(handle, pointer("pointermove"));
    expect(onBearingChange).not.toHaveBeenCalled();
  });

  it("keeps the direction handle inside the viewport at close-up zoom", () => {
    const map = { ...mapFixture(), project: ([lon, lat]: [number, number]) => ({
      x: 200 + (lon - initialMagic.lon) * 1e8, y: 200 - (lat - initialMagic.lat) * 1e8,
    }) };
    const view = render(<MagicMapPreview mapRef={{ current: map as unknown as MapLibreMap }} magic={initialMagic} disabled={false} />);
    const footprint = view.container.querySelector(".magic-footprint")!;
    Object.defineProperties(footprint, { clientWidth: {value:500}, clientHeight: {value:400} });
    map.on.mock.calls.find(([event]) => event === "resize")![1]();
    const handle = screen.getByRole("button", {name:"方向をドラッグして変更"});
    expect(parseFloat(handle.style.left)).toBeGreaterThan(0);
    expect(parseFloat(handle.style.left)).toBeLessThan(500);
    expect(parseFloat(handle.style.top)).toBeGreaterThan(0);
    expect(parseFloat(handle.style.top)).toBeLessThan(400);
  });
});

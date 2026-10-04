import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalysisArea } from "../api/client";
import SetupMap from "./SetupMap";
import { spellSettings } from "./magicCatalog";

const mocks = vi.hoisted(() => ({
  fitBounds: vi.fn(),
  jumpTo: vi.fn(),
  easeTo: vi.fn(),
  stop: vi.fn(),
  resize: vi.fn(),
  setData: vi.fn(),
  addSource: vi.fn(),
  addLayer: vi.fn(),
  moveLayer: vi.fn(),
  markerSetLngLat: vi.fn(),
}));

vi.mock("maplibre-gl", async (importOriginal) => {
  const { LngLat, LngLatBounds } = await importOriginal<typeof import("maplibre-gl")>();
  class Map {
    addControl() {}
    on() {}
    off() {}
    remove() {}
    once(_event: string, callback: () => void) { callback(); }
    isStyleLoaded() { return true; }
    getCanvas() { return { style: { cursor: "" }, getBoundingClientRect: () => ({ left: 0, top: 0 }) }; }
    getSource() { return { setData: mocks.setData }; }
    addSource(...args: unknown[]) { mocks.addSource(...args); }
    addLayer(...args: unknown[]) { mocks.addLayer(...args); }
    getLayer() { return {}; }
    moveLayer(...args: unknown[]) { mocks.moveLayer(...args); }
    project([lon, lat]: [number, number]) { return { x: lon * 10, y: lat * 10 }; }
    fitBounds(...args: unknown[]) { mocks.fitBounds(...args); }
    jumpTo(...args: unknown[]) { mocks.jumpTo(...args); }
    easeTo(...args: unknown[]) { mocks.easeTo(...args); }
    stop() { mocks.stop(); }
    resize() { mocks.resize(); }
  }

  class Marker {
    setLngLat(value: unknown) {
      mocks.markerSetLngLat(value);
      return this;
    }
    addTo() { return this; }
    remove() {}
  }

  class NavigationControl {}

  return {
    Map,
    Marker,
    NavigationControl,
    LngLat,
    LngLatBounds,
  };
});

function area(lat: number, lon: number): AnalysisArea {
  return {
    mode: "preset_square",
    center: { lat_deg: lat, lon_deg: lon },
    bounds: {
      west_deg: lon - 0.002,
      south_deg: lat - 0.002,
      east_deg: lon + 0.002,
      north_deg: lat + 0.002,
    },
    width_m: 500,
    height_m: 500,
    area_m2: 250000,
  };
}

describe("SetupMap", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockClear();
  });

  it("recenters and refits when canonical location changes", () => {
    const initial = area(35.681236, 139.767125);
    const selected = area(35.6722, 139.4805);

    const view = render(
      <SetupMap
        centerLat={initial.center.lat_deg}
        centerLon={initial.center.lon_deg}
        area={initial}
        disabled={false}
        onSelect={vi.fn()}
      />,
    );

    view.rerender(
      <SetupMap
        centerLat={selected.center.lat_deg}
        centerLon={selected.center.lon_deg}
        area={selected}
        disabled={false}
        onSelect={vi.fn()}
      />,
    );

    expect(mocks.markerSetLngLat).toHaveBeenLastCalledWith([
      selected.center.lon_deg,
      selected.center.lat_deg,
    ]);
    expect(mocks.stop).toHaveBeenCalled();
    expect(mocks.resize).toHaveBeenCalled();
    expect(mocks.fitBounds).toHaveBeenLastCalledWith(
      [
        [selected.bounds.west_deg, selected.bounds.south_deg],
        [selected.bounds.east_deg, selected.bounds.north_deg],
      ],
      { padding: 40, maxZoom: 17, duration: 350 },
    );
  });

  it("refits the viewport when only the analysis range changes", () => {
    const initial = area(35.681236, 139.767125);
    const expanded: AnalysisArea = {
      ...initial,
      bounds: {
        west_deg: initial.center.lon_deg - 0.02,
        south_deg: initial.center.lat_deg - 0.02,
        east_deg: initial.center.lon_deg + 0.02,
        north_deg: initial.center.lat_deg + 0.02,
      },
      width_m: 4000,
      height_m: 4000,
      area_m2: 16_000_000,
    };

    const view = render(
      <SetupMap
        centerLat={initial.center.lat_deg}
        centerLon={initial.center.lon_deg}
        area={initial}
        disabled={false}
        onSelect={vi.fn()}
      />,
    );

    view.rerender(
      <SetupMap
        centerLat={initial.center.lat_deg}
        centerLon={initial.center.lon_deg}
        area={expanded}
        disabled={false}
        onSelect={vi.fn()}
      />,
    );

    expect(mocks.fitBounds).toHaveBeenLastCalledWith(
      [
        [expanded.bounds.west_deg, expanded.bounds.south_deg],
        [expanded.bounds.east_deg, expanded.bounds.north_deg],
      ],
      { padding: 40, maxZoom: 17, duration: 350 },
    );
    expect(view.container.querySelectorAll("[data-analysis-area-overlay] path")).toHaveLength(2);
    expect(view.container.querySelector("[data-analysis-area-dom-outline]")).not.toHaveAttribute("hidden");
  });

  it("focuses small magic on every list click without snapping back on a parameter edit", () => {
    const initial = area(35.681236, 139.767125);
    const props = { centerLat: initial.center.lat_deg, centerLon: initial.center.lon_deg,
      area: initial, disabled: false, onSelect: vi.fn() };
    const magic = spellSettings("warcraft1-elemental", props.centerLon, props.centerLat);
    const view = render(<SetupMap {...props} magicPreview={null} magicFocusRequest={0} />);
    view.rerender(<SetupMap {...props} magicPreview={magic} magicFocusRequest={1} />);
    const [bounds, options] = mocks.fitBounds.mock.lastCall!;
    expect(bounds[1][0] - bounds[0][0]).toBeLessThan(0.00002);
    expect((bounds[0][0] + bounds[1][0]) / 2).toBeCloseTo(magic.lon, 10);
    expect(options).toEqual({ padding: 72, maxZoom: 24, duration: 450 });
    const count = mocks.fitBounds.mock.calls.length;
    view.rerender(<SetupMap {...props} magicPreview={{ ...magic, bearing: "90" }} magicFocusRequest={1} />);
    expect(mocks.fitBounds).toHaveBeenCalledTimes(count);
    view.rerender(<SetupMap {...props} magicPreview={magic} magicFocusRequest={2} />);
    expect(mocks.fitBounds).toHaveBeenCalledTimes(count + 1);
    const whole = spellSettings("chrono-water2", props.centerLon, props.centerLat);
    view.rerender(<SetupMap {...props} magicPreview={whole} magicFocusRequest={3} />);
    expect(mocks.fitBounds.mock.lastCall![0]).toEqual([
      [initial.bounds.west_deg, initial.bounds.south_deg],
      [initial.bounds.east_deg, initial.bounds.north_deg],
    ]);
  });
});

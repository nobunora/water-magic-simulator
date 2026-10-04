import { render } from "@testing-library/react";
import type { Map as MapLibreMap } from "maplibre-gl";
import { expect, it, vi } from "vitest";
import MagicResultFootprint, { type MagicGeometry } from "./MagicResultFootprint";

it("draws the saved footprint when the parent finishes creating its map", () => {
  const geometry: MagicGeometry = {
    footprintKind: "domain",
    coordinates: [[[0, 0], [100, 0], [100, 80], [0, 80], [0, 0]]],
  };
  const view = render(<MagicResultFootprint map={null} geometry={geometry} />);
  expect(view.container.querySelector("polygon")).not.toHaveAttribute("points");
  const map = {
    project: ([x, y]: number[]) => ({ x, y }), on: vi.fn(), off: vi.fn(),
  } as unknown as MapLibreMap;
  view.rerender(<MagicResultFootprint map={map} geometry={geometry} />);
  const polygons = view.container.querySelectorAll("polygon");
  expect(polygons[0]).toHaveAttribute("points", "5,5 95,5 95,75 5,75");
  expect(polygons[1]).toHaveAttribute("points", polygons[0].getAttribute("points"));
  view.unmount();
  expect(map.off).toHaveBeenCalledWith("move", expect.any(Function));
});

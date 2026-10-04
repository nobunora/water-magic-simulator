import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { magicCatalog, spellSettings } from "./magicCatalog";
import MagicMockPanel, { validMagic } from "./MagicMockPanel";
import assets from "../../public/magic-assets/manifest.json";
import { magicAnimation } from "./magicAssets";

describe("complete water magic catalog", () => {
  it("uses broad wave fronts and a finite Tales battlefield, independent of the analysis extent", () => {
    for (const id of ["lol-nami-wave", "ff3-tsunami"]) {
      const settings = spellSettings(id, 139.123456789, 35.987654321);
      expect(Number(settings.width)).toBeGreaterThan(Number(settings.length));
      expect(settings.lon).toBe(139.123456789);
      expect(settings.lat).toBe(35.987654321);
    }
    const tidal = spellSettings("tales-tidalwave", 139, 35);
    expect(tidal.footprintKind).toBe("circle");
    expect(Number(tidal.radius)).toBe(9.8);
    expect(Math.PI * Number(tidal.radius) ** 2).toBeCloseTo(301.72, 2);
  });
  it("shows dimensions to one decimal without rounding the physical configuration", () => {
    const magic = spellSettings("warcraft1-elemental", 139.123456789, 35.987654321);
    const change = vi.fn();
    render(<MagicMockPanel magic={magic} analysisArea={null} disabled={false} onSelect={vi.fn()} onChange={change} />);
    const radius = screen.getByLabelText("半径 (m)");
    expect(radius).toHaveAttribute("value", "0.6");
    fireEvent.focus(radius);
    expect(radius).toHaveValue(0.56419);
    fireEvent.blur(radius);
    expect(radius).toHaveAttribute("value", "0.6");
    expect(change).not.toHaveBeenCalled();
  });
  it("registers 17 unique usable presets with packaged game icons and animated assets", () => {
    expect(magicCatalog).toHaveLength(17);
    expect(new Set(magicCatalog.map(spell=>spell.id)).size).toBe(17);
    const icons = assets.icons as Record<string, {path:string}>;
    for (const spell of magicCatalog) {
      expect(spell.maxVolume).toBeGreaterThan(5);
      expect(validMagic(spellSettings(spell.id, 139, 35))).toBe(true);
      expect(Number(spellSettings(spell.id, 139, 35).volume) * spell.casting).toBeCloseTo(spell.maxVolume);
      expect(spellSettings(spell.id, 139, 35).releaseMode).toBe("initial");
      expect(icons[spell.gameId].path).toMatch(/^\/magic-assets\//);
      const animation = magicAnimation(spell.id);
      expect(animation.frames).toBeGreaterThan(1);
      expect(animation.path).toBe(`/magic-assets/${spell.id}.gif`);
      expect(animation.still.path).toMatch(/still.png$/);
      expect(animation.source).toMatch(/^https:\/\//);
    }
    expect(magicCatalog.map(spell=>spell.maxVolume)).toEqual(magicCatalog.map(spell=>spell.maxVolume).sort((a,b)=>a-b));
  });
  it("uses a stationary full pool and rain, and a vortex for Titanosaurus", () => {
    const pool = spellSettings("school-pool",139,35);
    expect(pool).toMatchObject({volume:"75", length:"25", width:"12", releaseMode:"initial", initialMotion:"none", initialSpeed:"0"});
    expect(spellSettings("dos2-rain",139,35)).toMatchObject({releaseMode:"initial", initialMotion:"none", initialSpeed:"0"});
    expect(spellSettings("kaiju-titanosaurus-vortex",139,35)).toMatchObject({volume:"4600", radius:"30", initialMotion:"vortex"});
  });
  it("replaces the complete selected configuration and keeps cards at two lines", () => {
    const select = vi.fn();
    const view = render(<MagicMockPanel magic={spellSettings("gw2-healingrain",139,35)} analysisArea={null} disabled={false} onSelect={select} onChange={vi.fn()} />);
    expect(view.container.querySelectorAll(".magic-choice")).toHaveLength(17);
    fireEvent.click(screen.getByRole("button", {name:/Neuvillette/}));
    expect(select).toHaveBeenCalledWith("genshin-neuvillette");
    const beam = spellSettings("genshin-neuvillette",139,35);
    view.rerender(<MagicMockPanel magic={beam} analysisArea={null} disabled={false} onSelect={select} onChange={vi.fn()} />);
    expect(screen.getByLabelText("効果範囲")).toHaveValue("rectangle");
    expect(screen.getByLabelText("長さ (m)")).toHaveValue(8);
    expect(screen.getByLabelText("幅 (m)")).toHaveValue(1);
    expect(screen.getByRole("link", {name:/Genshin Impact/})).toHaveAttribute("href", magicAnimation(beam.spellId).source);
  });
});

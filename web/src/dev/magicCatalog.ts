import entries from "./magicCatalog.json";
import type { MagicPreview } from "./MagicMapPreview";

export type MagicSpell = typeof entries[number];
export const magicCatalog = entries.filter(spell => spell.maxVolume > 5).sort((a, b) => a.maxVolume - b.maxVolume || a.id.localeCompare(b.id));
export function spellById(id: string): MagicSpell {
  return entries.find(spell => spell.id === (id === "healing-rain" ? "gw2-healingrain" : id)) ?? magicCatalog[0];
}
export function spellSettings(id: string, lon: number, lat: number): MagicPreview {
  const spell = spellById(id);
  return { spellId: spell.id, lon, lat, volume: String(spell.maxVolume / spell.casting), radius: String(spell.radius),
    length: String(spell.length), width: String(spell.width), sectorAngle: String(spell.angle),
    casting: String(spell.casting), relaxation: String(spell.casting * 10), bearing: "0", footprintKind: spell.kind as MagicPreview["footprintKind"],
    releaseMode: "initial",
    initialMotion: ["dos2-rain", "school-pool"].includes(spell.id) || spell.maxVolume <= 5 ? "none"
      : ["dq7-maelstrom", "rs3-maelstrom", "forspoken-cataract", "kaiju-titanosaurus-vortex"].includes(spell.id) ? "vortex"
      : spell.kind === "rectangle" || spell.kind === "sector" ? "directional" : "radial",
    initialSpeed: ["dos2-rain", "school-pool"].includes(spell.id) || spell.maxVolume <= 5 ? "0" : "2",
    vortexDirection: "clockwise", vortexCoreRadius: String(Math.max(.5, spell.radius / 2)),
    visible: true, playing: true, opacity: .65, size: 240 };
}

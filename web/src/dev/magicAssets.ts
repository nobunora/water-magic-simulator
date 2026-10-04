import assets from "../../public/magic-assets/manifest.json";
import { spellById } from "./magicCatalog";

type MagicAnimation = {
  path: string; frames: number; still: { path: string };
  source: string; credit: string; caption?: string; source_kind?: string;
};

export function magicAnimation(spellId: string): MagicAnimation {
  const assignments = assets.spells as Record<string, { gif: string }>;
  const animations = assets.gifs as Record<string, MagicAnimation>;
  return animations[assignments[spellById(spellId).id].gif];
}

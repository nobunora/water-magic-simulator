import type { MagicPreview } from "./MagicMapPreview";

export const magicHalfSizes = [100, 250, 500, 1000, 2000, 4000] as const;

export function minimumMagicHalfSize(magic: MagicPreview): number {
  const size = magic.footprintKind === "rectangle"
    ? Math.max(Number(magic.length), Number(magic.width))
    : Number(magic.radius);
  return size > 0 && Number.isFinite(size) ? size * 1.5 : 0;
}

export function recommendedMagicHalfSize(magic: MagicPreview): number | null {
  const minimum = minimumMagicHalfSize(magic);
  return magicHalfSizes.find(size => size >= minimum) ?? null;
}

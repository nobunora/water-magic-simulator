import type { MagicPreview } from "./MagicMapPreview";

export type MagicNumericField = "volume" | "radius" | "length" | "width" | "sectorAngle" | "casting" | "relaxation" | "bearing" | "initialSpeed" | "vortexCoreRadius";

function gcd(a: number, b: number): number {
  while (b !== 0) [a, b] = [b, a % b];
  return a;
}

function hasOutputInterval(casting: number, observation: number): boolean {
  const total = casting + observation;
  if (casting < 300 && total <= 299) return true;
  const common = gcd(casting, observation);
  const allowed = (step: number) => total <= 599 * step && (casting < 3 || step <= casting / 3);
  for (let divisor = 1; divisor * divisor <= common; divisor++) {
    if (common % divisor === 0 && (allowed(divisor) || allowed(common / divisor))) return true;
  }
  return false;
}

export function clipObservation(casting: number, value: number): number {
  // Match the API's 600-frame budget and retained casting/end boundaries.
  const largestStep = casting < 3 ? casting : Math.floor(casting / 3);
  let clipped = Math.max(0, Math.min(36000, Math.trunc(value), 599 * largestStep - casting));
  while (!hasOutputInterval(casting, clipped)) clipped--;
  return clipped;
}

export function magicInputRange(magic: MagicPreview, field: MagicNumericField): [number, number] {
  if (["radius", "length", "width"].includes(field)) return [0.1, 166.6];
  if (field === "volume") {
    const casting = Number(magic.casting);
    const maximum = 1_000_000 / casting;
    return [0.01, maximum * casting > 1_000_000 ? maximum * (1 - Number.EPSILON) : maximum];
  }
  if (field === "casting") return [1, 3600];
  if (field === "relaxation") return [0, clipObservation(Number(magic.casting), 36000)];
  if (field === "initialSpeed") return [0, 4];
  if (field === "vortexCoreRadius") return [0.1, 4000];
  return [field === "sectorAngle" ? 0.1 : 0, 360];
}

export function updateMagicNumber(magic: MagicPreview, field: MagicNumericField, raw: string): MagicPreview {
  const number = Number(raw);
  if (raw.trim() === "" || !Number.isFinite(number)) return magic;
  const [minimum, maximum] = magicInputRange(magic, field);
  let value = Math.max(minimum, Math.min(maximum, number));
  if (field === "casting" || field === "relaxation") value = Math.trunc(value);
  const next = { ...magic, [field]: String(value) };
  if (field === "casting") {
    const [, maxRate] = magicInputRange(next, "volume");
    next.volume = String(Math.min(Number(next.volume), maxRate));
    next.relaxation = String(clipObservation(value, value * 10));
  } else if (field === "relaxation") {
    next.relaxation = String(clipObservation(Number(magic.casting), value));
  }
  return next;
}

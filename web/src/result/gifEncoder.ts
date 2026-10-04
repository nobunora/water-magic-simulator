export type GifFrame = { rgba: Uint8ClampedArray; delayCs: number };

function pushWord(out: number[], value: number) {
  out.push(value & 0xff, (value >> 8) & 0xff);
}

function palette332(): number[] {
  const out: number[] = [];
  for (let i = 0; i < 256; i += 1) {
    const r = (i >> 5) & 7;
    const g = (i >> 2) & 7;
    const b = i & 3;
    out.push(Math.round((r / 7) * 255), Math.round((g / 7) * 255), Math.round((b / 3) * 255));
  }
  return out;
}

function index332(r: number, g: number, b: number): number {
  return ((r >> 5) << 5) | ((g >> 5) << 2) | (b >> 6);
}

function lzwLiteralStream(indices: Uint8Array): Uint8Array {
  const clear = 256;
  const end = 257;
  const bytes: number[] = [];
  let accumulator = 0;
  let bits = 0;
  const write9 = (code: number) => {
    accumulator |= code << bits;
    bits += 9;
    while (bits >= 8) {
      bytes.push(accumulator & 0xff);
      accumulator >>>= 8;
      bits -= 8;
    }
  };
  write9(clear);
  let sinceClear = 0;
  for (const index of indices) {
    write9(index);
    sinceClear += 1;
    // Reset before the decoder's dictionary reaches the 9 -> 10 bit boundary.
    // This intentionally favors a tiny, dependency-free encoder over compression ratio.
    if (sinceClear >= 250) {
      write9(clear);
      sinceClear = 0;
    }
  }
  write9(end);
  if (bits > 0) bytes.push(accumulator & 0xff);
  return Uint8Array.from(bytes);
}

function subBlocks(out: number[], data: Uint8Array) {
  for (let offset = 0; offset < data.length; offset += 255) {
    const size = Math.min(255, data.length - offset);
    out.push(size, ...data.subarray(offset, offset + size));
  }
  out.push(0);
}

export function encodeGif(width: number, height: number, frames: GifFrame[], loop = true): Blob {
  if (width < 1 || height < 1 || width > 800 || height > 800) throw new Error("GIF dimensions out of bounds");
  if (frames.length < 1 || frames.length > 120) throw new Error("GIF frame count out of bounds");
  const out: number[] = [...new TextEncoder().encode("GIF89a")];
  pushWord(out, width);
  pushWord(out, height);
  out.push(0xf7, 0, 0, ...palette332());

  if (loop) {
    out.push(0x21, 0xff, 11, ...new TextEncoder().encode("NETSCAPE2.0"), 3, 1, 0, 0, 0);
  }

  for (const frame of frames) {
    if (frame.rgba.length !== width * height * 4) throw new Error("GIF frame dimensions mismatch");
    out.push(0x21, 0xf9, 4, 0x04);
    pushWord(out, Math.max(1, Math.min(65535, Math.round(frame.delayCs))));
    out.push(0, 0);
    out.push(0x2c);
    pushWord(out, 0); pushWord(out, 0); pushWord(out, width); pushWord(out, height);
    out.push(0);
    const indices = new Uint8Array(width * height);
    for (let p = 0, i = 0; p < indices.length; p += 1, i += 4) {
      // GIF has no fractional alpha: composite transparent canvas pixels on white.
      const alpha = frame.rgba[i + 3] / 255;
      indices[p] = index332(
        Math.round(frame.rgba[i] * alpha + 255 * (1 - alpha)),
        Math.round(frame.rgba[i + 1] * alpha + 255 * (1 - alpha)),
        Math.round(frame.rgba[i + 2] * alpha + 255 * (1 - alpha)),
      );
    }
    out.push(8);
    subBlocks(out, lzwLiteralStream(indices));
  }
  out.push(0x3b);
  return new Blob([Uint8Array.from(out)], { type: "image/gif" });
}

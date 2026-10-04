import { describe, expect, it } from "vitest";
import { encodeGif } from "./gifEncoder";

function bytes(blob: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = reject;
    reader.readAsArrayBuffer(blob);
  });
}

describe("GIF binary frames", () => {
  it("writes independently decodable frames, including transparent and partially transparent pixels", async () => {
    const rgba = new Uint8ClampedArray(512 * 4);
    for (let pixel = 0; pixel < 512; pixel++) {
      rgba.set(pixel % 3 === 0 ? [0, 0, 0, 0] : pixel % 3 === 1 ? [255, 0, 0, 255] : [0, 0, 255, 128], pixel * 4);
    }
    const data = await bytes(encodeGif(512, 1, [{ rgba, delayCs: 10 }, { rgba, delayCs: 20 }], false));
    expect(new TextDecoder().decode(data.slice(0, 6))).toBe("GIF89a");
    const palette = data.slice(13, 13 + 768);
    let offset = 13 + 768;
    for (const delay of [10, 20]) {
      expect([...data.slice(offset, offset + 4)]).toEqual([0x21, 0xf9, 4, 4]);
      expect(data[offset + 4] | data[offset + 5] << 8).toBe(delay);
      offset += 8;
      expect(data[offset]).toBe(0x2c);
      expect(data[offset + 5] | data[offset + 6] << 8).toBe(512);
      offset += 10;
      expect(data[offset++]).toBe(8);
      const compressed: number[] = [];
      while (data[offset]) {
        const size = data[offset++];
        compressed.push(...data.slice(offset, offset + size));
        offset += size;
      }
      offset++;
      // Independent bit reader for the encoder's literal/reset GIF LZW stream.
      let bit = 0;
      const pixels: number[] = [];
      let resets = 0;
      while (bit + 9 <= compressed.length * 8) {
        let code = 0;
        for (let b = 0; b < 9; b++, bit++) code |= ((compressed[bit >> 3] >> (bit & 7)) & 1) << b;
        if (code === 256) { resets++; continue; }
        if (code === 257) break;
        expect(code).toBeLessThan(256);
        pixels.push(code);
      }
      expect(resets).toBe(3);
      expect(pixels).toHaveLength(512);
      expect([...palette.slice(pixels[0] * 3, pixels[0] * 3 + 3)]).toEqual([255, 255, 255]);
      expect([...palette.slice(pixels[1] * 3, pixels[1] * 3 + 3)]).toEqual([255, 0, 0]);
      expect([...palette.slice(pixels[2] * 3, pixels[2] * 3 + 3)]).toEqual([109, 109, 255]);
    }
    expect(data[offset]).toBe(0x3b);
    expect(offset + 1).toBe(data.length);
  });
});

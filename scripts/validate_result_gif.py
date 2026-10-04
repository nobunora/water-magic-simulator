"""Inspect every GIF image block and independently decode every output frame."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def inspect_gif(path: Path, output: Path) -> dict:
    data = path.read_bytes()
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("invalid GIF signature")
    width, height, flags = struct.unpack_from("<HHB", data, 6)
    offset = 13 + (3 * 2 ** ((flags & 7) + 1) if flags & 128 else 0)
    blocks: list[dict[str, Any]] = []
    delay = 0
    while offset < len(data):
        start = offset
        tag = data[offset]
        offset += 1
        if tag == 0x3b:
            if offset != len(data):
                raise ValueError("bytes after GIF trailer")
            break
        if tag == 0x21:
            label = data[offset]
            offset += 1
            if label == 0xf9:
                if data[offset] != 4:
                    raise ValueError("invalid graphics control block")
                delay = struct.unpack_from("<H", data, offset + 2)[0]
        elif tag == 0x2c:
            left, top, frame_width, frame_height, packed = struct.unpack_from("<HHHHB", data, offset)
            offset += 9
            if left + frame_width > width or top + frame_height > height:
                raise ValueError("frame exceeds logical screen")
            offset += 3 * 2 ** ((packed & 7) + 1) if packed & 128 else 0
            code_size = data[offset]
            offset += 1
            if not 2 <= code_size <= 8:
                raise ValueError("invalid LZW minimum code size")
        else:
            raise ValueError(f"invalid GIF block at {start}")
        payload = bytearray()
        subblock_count = 0
        while True:
            size = data[offset]
            offset += 1
            if not size:
                break
            if offset + size > len(data):
                raise ValueError("truncated subblock")
            payload.extend(data[offset:offset + size])
            offset += size
            subblock_count += 1
        if tag == 0x2c:
            blocks.append({"frame": len(blocks), "binary_offset": start, "binary_length": offset - start,
                "rectangle": [left, top, frame_width, frame_height], "delay_cs": delay,
                "lzw_min_code_size": code_size, "subblock_count": subblock_count,
                "compressed_bytes": len(payload), "compressed_sha256": hashlib.sha256(payload).hexdigest()})
    else:
        raise ValueError("missing GIF trailer")
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as image:
        if image.n_frames != len(blocks):
            raise ValueError("binary/decoder frame count mismatch")
        for record in blocks:
            image.seek(record["frame"])
            rgba = np.asarray(image.convert("RGBA"))
            # Exclude the 28-pixel time-label footer when detecting black map captures.
            map_rgb = rgba[:max(1, height - 28), :, :3]
            record.update(decoded_sha256=hashlib.sha256(rgba.tobytes()).hexdigest(),
                map_decoded_sha256=hashlib.sha256(map_rgb.tobytes()).hexdigest(),
                map_black_fraction=float(np.mean(np.all(map_rgb <= 8, axis=2))),
                map_mean_rgb=map_rgb.mean(axis=(0, 1)).tolist(),
                map_nonwhite_pixels=int(np.count_nonzero(np.any(map_rgb < 240, axis=2))),
                decoded_pixels=int(rgba.shape[0] * rgba.shape[1]))
            image.convert("RGBA").save(output / f"frame-{record['frame']:03}.png")
    report = {"file": str(path), "file_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "width": width, "height": height, "frame_count": len(blocks), "frames": blocks,
        "black_map_frames": [r["frame"] for r in blocks if r["map_black_fraction"] > .95],
        "unique_decoded_frames": len({r["decoded_sha256"] for r in blocks}),
        "unique_map_frames": len({r["map_decoded_sha256"] for r in blocks})}
    (output / "frame-binary-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("gif", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = inspect_gif(args.gif, args.output)
    print(json.dumps({key: value for key, value in report.items() if key != "frames"}, indent=2))


if __name__ == "__main__":
    main()

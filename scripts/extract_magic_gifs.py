"""Extract reviewed gameplay clips with a supplied FFmpeg executable.

Full source videos stay in ignored artifacts; only short GIFs, stills and
source/cut metadata are packaged. No downloads, credentials or runtime tools
are needed by the application.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "web/public/magic-assets"


def file_metadata(path: Path) -> dict:
    data = path.read_bytes()
    with Image.open(path) as image:
        durations = []
        for frame in range(getattr(image, "n_frames", 1)):
            image.seek(frame)
            durations.append(image.info.get("duration", 0))
        return {"path": f"/magic-assets/{path.name}", "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "width": image.width,
                "height": image.height, "frames": len(durations),
                "duration_seconds": sum(durations) / 1000,
                "loop": image.info.get("loop")}


def extract_clip(ffmpeg: str, source_dir: Path, clip: dict) -> dict:
    source = source_dir / clip["file"]
    duration = clip["end_seconds"] - clip["start_seconds"]
    if not source.is_file() or not 0 < duration <= 10:
        raise ValueError(f"Missing source or invalid reviewed cut: {clip['spell_id']}")
    gif = DEST / f"{clip['spell_id']}.gif"
    still = DEST / f"{clip['spell_id']}-still.png"
    common = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-threads", "2",
              "-ss", str(clip["start_seconds"]), "-i", str(source)]
    scale = "scale=400:-2:flags=lanczos"
    if "exposure_gamma" in clip:
        gamma = float(clip["exposure_gamma"])
        if not 1 <= gamma <= 2:
            raise ValueError("Exposure gamma must be between 1 and 2")
        scale += f",eq=gamma={gamma}"
    filters = (f"fps=12,{scale},split[a][b];[a]palettegen=max_colors=128[p];"
               "[b][p]paletteuse=dither=bayer:bayer_scale=3")
    subprocess.run([*common, "-t", str(duration), "-filter_complex_threads", "1", "-filter_complex", filters,
                    "-an", "-loop", "0", str(gif)], check=True)
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(clip["still_seconds"]), "-i", str(source),
                    "-vf", scale, "-frames:v", "1", str(still)], check=True)
    result = file_metadata(gif)
    if result["frames"] < 2 or result["bytes"] > 10 * 1024 * 1024:
        raise ValueError(f"GIF exceeds animation/size budget: {gif.name}")
    return {**result, "still": file_metadata(still), **clip,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "credit": f"{clip['channel']} / {clip['rights_holder']}",
            "license": "Copyright retained by the respective rights holders",
            "redistribution": None, "usage": "User-requested local review",
            "background": "opaque", "suitability": "Reviewed film footage" if clip["spell_id"].startswith("kaiju-") else "Reviewed game footage",
            "conversion": "FFmpeg: 400 px wide, 12 fps, 128 colors, Bayer dither, loop=0"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "artifacts/magic-video-sources")
    parser.add_argument("--clips", type=Path, default=DEST / "footage-sources.json")
    args = parser.parse_args()
    clips = json.loads(args.clips.read_text(encoding="utf-8"))["clips"]
    manifest_path = DEST / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for clip in clips:
        spell_id = clip["spell_id"]
        manifest["gifs"][spell_id] = extract_clip(args.ffmpeg, args.source_dir, clip)
        manifest["spells"][spell_id]["gif"] = spell_id
        print(f"Extracted {spell_id}", flush=True)
    active = {record["gif"] for record in manifest["spells"].values()}
    if sum(manifest["gifs"][key]["bytes"] for key in active) > 60 * 1024 * 1024:
        raise ValueError("Selected GIFs exceed the 60 MiB budget")
    manifest["revision"] = "2026-10-03-game-footage"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

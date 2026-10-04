"""Package game identifying icons and original, explicitly illustrative GIFs."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import requests
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "web/public/magic-assets"
STEAM = {"ff3":239120, "ff7":39140, "chrono":613830, "ro":215100,
    "tales":372360, "gw2":1284210, "dos2":435150, "kh3":2552450,
    "forspoken":1680880, "bg3":1086940, "dq7":2499860, "ff6":382900,
    "ff15":637650, "chrono-cross":1133760, "rs3":952540}
PAGES = {"warcraft1":"https://www.blizzard.com/en-us/games",
    "warcraft3":"https://warcraft3.blizzard.com/en-us/",
    "wow":"https://worldofwarcraft.blizzard.com/en-us/",
    "lol":"https://www.leagueoflegends.com/en-us/",
    "genshin":"https://genshin.hoyoverse.com/en/",
    "goldensun":"https://www.nintendo.co.jp/n08/agfj/index.html",
    "godzilla2014":"https://godzilla.com/blogs/movies/godzilla-2014",
    "titanosaurus":"https://godzilla.com/blogs/monsterpedia/titanosaurus"}


def metadata(path: Path) -> dict:
    data = path.read_bytes()
    result = {"path":f"/magic-assets/{path.name}", "sha256":hashlib.sha256(data).hexdigest(), "bytes":len(data)}
    if path.suffix != ".svg":
        with Image.open(path) as image:
            result.update(width=image.width, height=image.height, frames=getattr(image, "n_frames", 1))
    return result


def acquire_icon(item: tuple[str, str]) -> tuple[str, dict]:
    game, page = item
    response = requests.get(page, timeout=30)
    if game in STEAM and response.status_code == 429:
        page = f"https://store.steampowered.com/app/{STEAM[game]}"
        response = requests.get(page, timeout=30)
    response.raise_for_status()
    if game in STEAM:
        matches = re.findall(r'https://[^\s"<>]+/apps/' + str(STEAM[game]) + r'/[a-f0-9]+\.(?:jpg|png)', response.text)
        if matches:
            media = matches[0]
        else:
            media = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', response.text)[1]
    elif game in {"godzilla2014", "titanosaurus"}:
        media = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', response.text)[1]
        media = media.replace("http:", "https:", 1)
    elif game == "goldensun":
        media = "https://www.nintendo.co.jp/n08/agfj/main18.jpg"
    elif game == "warcraft1":
        # The first title's individual site is unavailable; use its actual game artwork.
        tile = re.search(r'<a href="https://shop.battle.net/product/warcraft-1-remastered">(.*?)</a>', response.text)
        if not tile:
            raise ValueError("Warcraft I artwork requires explicit selection from the official game page")
        media = re.search(r'slot="logo" src="([^"]+)', tile[1])[1].split("?")[0]
    else:
        tags = re.findall(r'<link[^>]+>', response.text, re.IGNORECASE)
        icons = [tag for tag in tags if re.search(r'rel=["\'][^"\']*(?:shortcut|icon)', tag)]
        if not icons:
            raise ValueError(f"No identifying icon on {page}")
        media = urljoin(page, re.search(r'href=["\']([^"\']+)', icons[0])[1])
    image = requests.get(media, timeout=30)
    image.raise_for_status()
    path = DEST / f"game-{game}{'.svg' if 'svg' in image.headers.get('Content-Type', '') else '.png'}"
    if path.suffix == ".svg":
        path.write_bytes(image.content)
    else:
        from io import BytesIO
        with Image.open(BytesIO(image.content)) as source:
            source.convert("RGBA").save(path)
    return game, {**metadata(path), "source":page, "media":media,
        "credit":"Game identifying artwork; rights retained by the respective game publisher.",
        "usage":"Local identifying icon, not an open-licensed asset; no redistribution grant inferred."}


def pool_icon() -> dict:
    path = DEST/"game-school-pool.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect x="3" y="9" width="58" height="46" rx="4" fill="#238ece" stroke="#162c46" stroke-width="3"/>'
        '<path d="M5 20h54M5 32h54M5 44h54" stroke="white" stroke-width="2"/></svg>', encoding="utf-8")
    return {**metadata(path), "source":"Original project pool diagram", "credit":"Original project illustration"}


def pool_illustration() -> dict:
    """An original dimensioned comparison, not footage of a real facility."""
    frames = []
    for frame in range(24):
        image = Image.new("RGB", (400, 240), "#f0f8ff")
        draw = ImageDraw.Draw(image)
        draw.text((20, 12), "ELEMENTARY SCHOOL POOL / REFERENCE", fill="#162c46")
        draw.rectangle((75, 54, 325, 174), fill="#5eb6ed", outline="#162c46", width=4)
        for lane in range(1, 6):
            draw.line((75, 54+lane*20, 325, 54+lane*20), fill="#f0f8ff", width=2)
        for row in range(65, 170, 20):
            points = [(x, row+3*math.sin(x/14+frame*math.tau/24)) for x in range(80, 320)]
            draw.line(points, fill="#247eaf", width=2)
        draw.text((180, 34), "25 m", fill="#162c46")
        draw.text((20, 110), "12 m", fill="#162c46")
        draw.text((75, 191), "Mean depth: 1 m / Water: 300 m3", fill="#162c46")
        draw.text((75, 212), "Water placed once; pool walls not modeled", fill="#162c46")
        frames.append(image)
    gif, still = DEST/"school-pool.gif", DEST/"school-pool-still.png"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=80, loop=0)
    frames[0].save(still)
    return {**metadata(gif), "duration_seconds":1.92, "loop":0, "still":metadata(still),
        "source":"https://blog.suita.ed.jp/es/25-m-yamada/shinmidorinoniji/2014/08/post-169.html",
        "credit":"Original diagram; dimensions: Suita Minamiyamada Elementary School",
        "source_kind":"original", "caption":"公開寸法を使った説明図。実際のプールの動画ではありません。",
        "background":"opaque", "license":"Original project illustration", "usage":"Dimensioned comparison"}


def illustration(family: str) -> dict:
    frames = []
    for index in range(24):
        t = index / 24 * math.tau
        image = Image.new("RGB", (360, 240), "#0b1930")
        draw = ImageDraw.Draw(image)
        draw.text((12, 12), f"WATER / {family.upper()} / ILLUSTRATION", fill="#bcecff")
        if family in {"wave", "flood"}:
            for layer in range(5):
                points = [(x, int(90 + layer * 20 + 18 * math.sin(x / 35 - t + layer))) for x in range(361)]
                draw.polygon([*points, (360, 240), (0, 240)], fill=(20+layer*12, 85+layer*25, 160+layer*18))
                draw.line(points, fill="#c5f5ff", width=3)
        elif family == "beam":
            for offset in range(-6, 7):
                points = [(x, int(120 + offset * 5 + 6 * math.sin(x / 22 - t * 3 + offset))) for x in range(40, 340)]
                draw.line(points, fill=(100+abs(offset)*12, 180+abs(offset)*8, 255), width=4)
            draw.ellipse((15, 90, 75, 150), fill="#3faaf5", outline="#dafaff", width=4)
        elif family == "vortex":
            for arm in range(5):
                points = []
                for step in range(140):
                    radius = 5 + step * .62
                    angle = step / 20 + t + arm * math.tau / 5
                    points.append((180+radius*math.cos(angle), 130+radius*.85*math.sin(angle)))
                draw.line(points, fill="#5dd7fc", width=6)
        else:
            radius = 55 + 5 * math.sin(t)
            draw.ellipse((180-radius, 125-radius, 180+radius, 125+radius), fill="#137bc9", outline="#75dcff", width=5)
            if family == "body":
                draw.ellipse((157, 46, 203, 92), fill="#299ad4", outline="#98edff", width=3)
                draw.line((146, 150, 130+10*math.sin(t), 200, 230, 200), fill="#51ccff", width=13)
            for stripe in range(5):
                y = 95 + stripe*15
                draw.arc((132, y-14, 228, y+20), int(t*180/math.pi)%360, int(t*180/math.pi)%360+150, fill="#d0f6ff", width=3)
        frames.append(image)
    gif = DEST / f"{family}.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=80, loop=0)
    still = DEST / f"{family}-still.png"
    frames[0].save(still)
    return {**metadata(gif), "still":metadata(still), "source":"scripts/prepare_magic_assets.py",
        "credit":"Original procedural water-effect illustration", "license":"CC0-1.0",
        "redistribution":True, "background":"opaque", "duration_ms":1920,
        "suitability":"Effect-family illustration, not game footage or hydraulic results"}


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    pages = {**{key:f"https://steamcommunity.com/app/{app}" for key, app in STEAM.items()}, **PAGES}
    previous_path = DEST / "manifest.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    icons, errors = previous.get("icons", {}), {}
    pages = {key:page for key, page in pages.items() if key not in icons}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {key:pool.submit(acquire_icon, (key, page)) for key, page in pages.items()}
        for key, future in futures.items():
            try:
                name, record = future.result()
                icons[name] = record
            except (requests.RequestException, ValueError, OSError, TypeError, IndexError) as error:
                errors[key] = str(error)
    icons["school-pool"] = pool_icon()
    gifs = {family:illustration(family) for family in ("wave", "flood", "beam", "vortex", "sphere", "body")}
    gifs["school-pool"] = pool_illustration()
    for filename in ("rain.gif", "rain-still.png"):
        shutil.copyfile(ROOT / "docs/mockups/water-magic" / filename, DEST / filename)
    gifs["rain"] = {**metadata(DEST / "rain.gif"), "still":metadata(DEST / "rain-still.png"),
        "source":"https://commons.wikimedia.org/wiki/File:Rain_2.gif", "credit":"Shisma / Rain 2",
        "license":"CC0-1.0", "redistribution":True, "background":"opaque", "suitability":"Natural rain proxy"}
    catalog = json.loads((ROOT / "web/src/dev/magicCatalog.json").read_text(encoding="utf-8"))
    # Keep reviewed per-spell footage when rebuilding the illustrative fallbacks.
    for key, record in previous.get("gifs", {}).items():
        if record.get("suitability") in {"Reviewed game footage", "Reviewed film footage"}:
            for path in (record["path"], record["still"]["path"]):
                if not (DEST / Path(path).name).is_file():
                    raise FileNotFoundError(f"Reviewed footage is missing: {path}")
            gifs[key] = record
    assignments = {spell["id"]:{"icon":spell["gameId"],
        "gif":spell["id"] if spell["id"] in gifs else spell["gif"]} for spell in catalog}
    revision = "2026-10-03-game-footage" if any(spell["id"] in gifs for spell in catalog) else "2026-10-03"
    manifest = {"revision":revision, "acquired_on":"2026-10-03", "icons":icons, "gifs":gifs,
        "spells":assignments, "errors":errors}
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"icons":len(icons), "gifs":len(gifs), "spells":len(catalog), "errors":errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()

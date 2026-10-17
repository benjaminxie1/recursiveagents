"""Render the plant splat from every viewpoint, once per visual state: python -m scripts.render_splat_views

Writes assets/splats/renders/<viewpoint>/<state>.jpg and renders/index.json. Camera.render (gridtwin/sim/camera.py)
serves these from get_camera_view as `render_path`, so a live run never waits on a browser.
rgb states are drawn in headless Chromium (viewer/splat-render.html) with the state's overlay painted on the asset;
thermal states are the healthy rgb frame mapped to an ironbow palette, with a hot spot on the asset for the hot states.
Needs: pip install playwright; Chromium at /opt/pw-browsers/chromium or CHROMIUM=<path>.
"""
import argparse
import json
import os
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from gridtwin.serve import Handler
from scripts.build_splat import load_viewpoints

REPO = Path(__file__).resolve().parents[1]
SPLATS = REPO / "assets" / "splats"
OUT = SPLATS / "renders"
HOT = {"thermal_hot", "thermal_hotspot"}
SIZE = (800, 600)


# Rainbow thermography palette (FLIR-style "rainbow HC"), as (deg C, rgb): indigo sky, green-yellow panels, red-white hot spots.
PALETTE = [(20, (42, 10, 110)), (23, (26, 47, 208)), (26, (16, 168, 224)), (28.5, (31, 191, 90)), (31, (61, 209, 58)),
           (33.5, (200, 232, 48)), (35, (245, 229, 42)), (37.5, (245, 154, 30)), (40, (229, 43, 35)), (42, (240, 160, 176)), (48, (255, 236, 240))]
T_MIN, T_MAX = 20.0, 42.0


def temperature(rgb: Image.Image) -> np.ndarray:
    """Apparent temperature (deg C) from the rgb render: sky reads cold, dark surfaces (modules, gravel in sun) warm."""
    a = np.asarray(rgb.filter(ImageFilter.GaussianBlur(1.5)), float) / 255
    lum = a @ np.array([0.3, 0.55, 0.15])
    t = 38.5 - 11.0 * lum
    sky = (a[..., 2] > a[..., 0] + 0.06) & (lum > 0.45)
    sky = np.cumprod(sky, axis=0).astype(bool)            # sky only where everything above it is sky too
    return np.where(sky, 20.5 + 2.0 * (1 - lum), t)


def colorize(t: np.ndarray) -> np.ndarray:
    xs = np.array([p[0] for p in PALETTE])
    cols = np.array([p[1] for p in PALETTE], float)
    return np.stack([np.interp(t, xs, cols[:, c]) for c in range(3)], -1).astype(np.uint8)


def ironbow(rgb: Image.Image, hot_xy=None, hot=False) -> Image.Image:
    """Thermal frame in the style of a radiometric IR camera: rainbow palette, deg C scale bar, ROI readouts."""
    from PIL import ImageDraw
    t = temperature(rgb)
    h, w = t.shape
    rois = []
    if hot_xy is not None:
        x, y = hot_xy
        yy, xx = np.mgrid[0:h, 0:w]
        if hot:
            r = 0.035 * w
            t = t + 24.0 * np.exp(-(((xx - x) ** 2 + (yy - y) ** 2) / (2 * r * r)))
            rois.append((x, y, float(t[int(np.clip(y, 0, h - 1)), int(np.clip(x, 0, w - 1))])))
        else:
            rois.append((x, y, float(t[int(np.clip(y, 0, h - 1)), int(np.clip(x, 0, w - 1))])))
    img = Image.fromarray(colorize(t))
    d = ImageDraw.Draw(img)
    for k, (x, y, v) in enumerate(rois, 1):
        d.line([(x - 6, y), (x + 6, y)], fill=(255, 255, 255), width=1)
        d.line([(x, y - 6), (x, y + 6)], fill=(255, 255, 255), width=1)
        label = f"ROI {k:03d} {v:.1f} \u00b0C"
        tx, ty = x + 10, y - 24
        tw = 7 * len(label)
        d.rectangle([tx - 3, ty - 2, tx + tw, ty + 13], fill=(255, 255, 255), outline=(0, 0, 0))
        d.text((tx, ty), label, fill=(0, 0, 0))
    # scale bar on the right, like the camera's own overlay
    bw = 92
    canvas = Image.new("RGB", (w + bw, h), (255, 255, 255))
    canvas.paste(img, (0, 0))
    d = ImageDraw.Draw(canvas)
    top, bot = 16, h - 16
    ramp = colorize(np.linspace(T_MAX, T_MIN, bot - top)[:, None].repeat(16, 1))
    canvas.paste(Image.fromarray(ramp), (w + 64, top))
    for v in np.arange(T_MIN, T_MAX + 0.1, 2.5):
        yv = bot - (v - T_MIN) / (T_MAX - T_MIN) * (bot - top)
        d.line([(w + 58, yv), (w + 63, yv)], fill=(0, 0, 0))
        d.text((w + 8, yv - 6), f"{v:.1f} \u00b0C".replace(".", ","), fill=(0, 0, 0))
    return canvas


def rethermal():
    index = json.loads((OUT / "index.json").read_text())
    for vid, entry in index.items():
        rgb = [e for e in entry.values() if e["mode"] == "rgb"]
        ref = next((e for st, e in entry.items() if st in ("healthy", "connector_normal", "array_normal")), rgb[0] if rgb else None)
        for st, e in entry.items():
            if e["mode"] == "thermal" and ref:
                ironbow(Image.open(REPO / ref["file"]).convert("RGB"), e["anchor_px"], st in HOT).save(REPO / e["file"], "JPEG", quality=86)
                print(f"{vid}/{st} (thermal)")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated viewpoint ids")
    ap.add_argument("--thermal-only", action="store_true", help="redo the thermal frames from the existing rgb renders")
    a = ap.parse_args(argv)
    if a.thermal_only:
        return rethermal()
    from playwright.sync_api import sync_playwright

    scene = json.loads((SPLATS / "scene.json").read_text())
    vps, states = load_viewpoints()
    only = set(a.only.split(",")) if a.only else None
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() and only else {}
    chromium = os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium if Path(chromium).exists() else None,
                                    args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = browser.new_page(viewport={"width": 1024, "height": 768})
        for vid, vp in sorted(vps.items()):
            if only and vid not in only or vid not in scene["stations"]:
                continue
            asset = vp["asset"]
            sts = states.get(asset, [])
            rgb_states = [s for s in sts if not s.startswith("thermal_")] or ["healthy"]
            entry = index.setdefault(vid, {})
            shots = {}
            for st in rgb_states:
                ov = SPLATS / "overlays" / asset / f"{st}.splat"
                url = f"{base}/splat-render.html?vp={vid}" + (f"&overlay={ov.relative_to(REPO).as_posix()}" if ov.exists() else "")
                page.goto(url)
                page.wait_for_function("window.__frame", timeout=240000)
                frame = page.evaluate("window.__frame")
                if not frame.get("ok"):
                    print(f"{vid}/{st}: FAILED {frame.get('error')}")
                    continue
                png = page.locator("#view canvas").screenshot()
                from io import BytesIO
                img = Image.open(BytesIO(png)).convert("RGB").resize(SIZE, Image.LANCZOS)
                dst = OUT / vid / f"{st}.jpg"
                dst.parent.mkdir(parents=True, exist_ok=True)
                img.save(dst, "JPEG", quality=84)
                ax, ay, _ = frame["anchor_px"]
                anchor = [ax * SIZE[0] / 1024, ay * SIZE[1] / 768]
                shots[st] = img
                entry[st] = {"file": dst.relative_to(REPO).as_posix(), "mode": "rgb", "anchor_px": [round(v, 1) for v in anchor]}
                print(f"{vid}/{st}")
            if "thermal" in vp.get("modes", []):
                ref = shots.get("healthy") or shots.get("connector_normal") or shots.get("array_normal") or next(iter(shots.values()), None)
                for st in [s for s in sts if s.startswith("thermal_")] or ["thermal_normal"]:
                    if ref is None:
                        break
                    anchor = next(iter(entry.values()))["anchor_px"]
                    dst = OUT / vid / f"{st}.jpg"
                    ironbow(ref, anchor, st in HOT).save(dst, "JPEG", quality=84)
                    entry[st] = {"file": dst.relative_to(REPO).as_posix(), "mode": "thermal", "anchor_px": anchor}
                    print(f"{vid}/{st} (thermal)")
        browser.close()
    server.shutdown()
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    print(f"{sum(map(len, index.values()))} renders for {len(index)} viewpoints -> {index_path.relative_to(REPO)}")


if __name__ == "__main__":
    main()

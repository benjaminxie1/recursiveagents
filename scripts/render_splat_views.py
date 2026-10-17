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


def ironbow(rgb: Image.Image, hot_xy=None, hot=False) -> Image.Image:
    """False-colour thermal look: ambient scene cool and flat, equipment slightly warmer, optional hot spot."""
    g = np.asarray(rgb.convert("L").filter(ImageFilter.GaussianBlur(3)), float) / 255
    t = 0.18 + 0.32 * (g - g.min()) / max(1e-6, np.ptp(g))
    if hot and hot_xy is not None:
        h, w = t.shape
        yy, xx = np.mgrid[0:h, 0:w]
        x, y = hot_xy
        r = 0.05 * w
        t = np.maximum(t, 0.98 * np.exp(-(((xx - x) ** 2 + (yy - y) ** 2) / (2 * r * r))))
    stops = np.array([[0, 0, 0.05], [0.25, 0, 0.55], [0.75, 0.1, 0.45], [0.95, 0.45, 0.05], [1, 0.85, 0.2], [1, 1, 0.9]])
    pos = np.linspace(0, 1, len(stops))
    out = np.stack([np.interp(t, pos, stops[:, c]) for c in range(3)], -1)
    return Image.fromarray((out * 255).astype(np.uint8))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated viewpoint ids")
    a = ap.parse_args(argv)
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

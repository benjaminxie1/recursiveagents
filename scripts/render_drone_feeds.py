"""Render four looping drone camera feeds over the plant splat: python -m scripts.render_drone_feeds

Writes assets/splats/drones/drone{1..4}.mp4 and drones/drones.json for the dashboard's 2x2 feed grid.
Each drone flies along one PV block row at 20 m, offset south of it, eased from the row's west end to its inverter.
Frames are drawn in headless Chromium (viewer/splat-render.html?cam=1), read back with canvas.toDataURL, and encoded with ffmpeg (libx264, no audio).
Needs: pip install playwright; Chromium at /opt/pw-browsers/chromium or CHROMIUM=<path>; ffmpeg at /usr/bin/ffmpeg or FFMPEG=<path>.
"""
import argparse
import base64
import json
import os
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

from PIL import Image

from gridtwin.serve import Handler

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "assets" / "splats" / "drones"
ROWS = [("B1", 30), ("B2", 80), ("B3", 130), ("B4", 180)]
ALT, OFFSET, LEAD, FOV = 20, 26, 18, 55
X0, X1 = 4, 132          # camera x; the look-ahead point ends on the inverter at x = 150


def pose(row_z: float, t: float):
    """Camera position and target at flight progress t in [0, 1] (smoothstep eased)."""
    s = t * t * (3 - 2 * t)
    x = X0 + (X1 - X0) * s
    return [x, ALT, row_z + OFFSET], [x + LEAD, 0, row_z]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=72)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--crf", type=int, default=28)
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright

    ffmpeg = os.environ.get("FFMPEG", "/usr/bin/ffmpeg")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    chromium = os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium")
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium if Path(chromium).exists() else None,
                                    args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = browser.new_page(viewport={"width": 640, "height": 360})
        page.goto(f"{base}/splat-render.html?cam=1")
        page.wait_for_function("window.__ready || window.__error", timeout=240000)
        if page.evaluate("window.__error || null"):
            raise SystemExit(f"splat-render failed: {page.evaluate('window.__error')}")
        page.evaluate("([p, t, f]) => window.shootCam(p, t, f, 6)", [*pose(ROWS[0][1], 0), FOV])   # warm-up: first sort
        feeds = []
        for i, (row, z) in enumerate(ROWS, 1):
            frames = Path(tmp) / f"drone{i}"
            frames.mkdir()
            page.evaluate("([p, t, f]) => window.shootCam(p, t, f, 6)", [*pose(z, 0), FOV])     # settle on the new row
            for k in range(a.frames):
                # one draw per frame: poses are close, so the previous frame's sort still fits (each draw is ~8 s on SwiftShader)
                page.evaluate("([p, t, f]) => window.shootCam(p, t, f, 0)", [*pose(z, k / (a.frames - 1)), FOV])
                url = page.evaluate("document.querySelector('#view canvas').toDataURL('image/png')")
                Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB").save(frames / f"{k:03d}.png")
            dst = OUT / f"drone{i}.mp4"
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(a.fps), "-i", str(frames / "%03d.png"),
                            "-c:v", "libx264", "-preset", "slow", "-crf", str(a.crf), "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", "-an", str(dst)], check=True)
            feeds.append({"id": f"drone{i}", "row": row, "file": dst.relative_to(REPO).as_posix(),
                          "seconds": round(a.frames / a.fps, 2), "altitude_m": ALT})
            print(f"drone{i} ({row}): {dst.stat().st_size / 1024:.0f} KB, {time.time() - t0:.0f} s elapsed")
        browser.close()
    server.shutdown()
    (OUT / "drones.json").write_text(json.dumps(feeds, indent=1) + "\n")
    print(f"{len(feeds)} feeds -> {OUT.relative_to(REPO)} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()

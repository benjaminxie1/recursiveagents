"""Render one rgb view of the plant splat per asset anchor: python -m scripts.render_anchor_views

Writes assets/demo/asset_packs/_views/<ASSET>.jpg (800x600) and _views/index.json with each view's pose and the
anchor's projected pixel. An asset with a station in scene.json uses its first station's pose; the rest get a pose
copied from a sibling asset's station (same offset from the anchor) or, failing that, a per-kind offset below.
Frames are drawn in headless Chromium with viewer/splat.js (the same PlantView as viewer/splat-render.html), loading
the plant once and shooting every pose in one session.
Needs: Chromium at /opt/pw-browsers/chromium or CHROMIUM=<path>, and playwright for Python (pip install playwright)
or, failing that, for Node (NODE_PATH holding playwright; /opt/node22/lib/node_modules is tried by default).
"""
import argparse
import base64
import json
import os
import re
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from gridtwin.serve import Handler

REPO = Path(__file__).resolve().parents[1]
SCENE = REPO / "assets" / "splats" / "scene.json"
OUT = REPO / "assets" / "demo" / "asset_packs" / "_views"
SIZE = (800, 600)
ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]

# Asset kind -> station whose (position - anchor, target - anchor, fov) is reused for siblings without a station.
SIBLING = {"B": "b3_array", "CB": "cb15_box", "INV": "inv1_cabinet", "52-I": "brk_52i4", "52-": "brk_52i4"}
# Kinds with no sibling station: (camera offset from the anchor, target offset, fov), metres, y up, +z south.
KIND_POSE = {
    "BESS": ([-9.0, 5.0, 13.0], [0, 0, 0], 45),
    "POI": ([-8.0, 4.5, 11.0], [0, 0, 0], 45),
    "MET": ([-3.5, 2.0, 6.0], [0, 0.5, 0], 45),
}
DEFAULT_POSE = ([-6.0, 3.0, 9.0], [0, 0, 0], 45)

# In-page code: load the plant into a 800x600 PlantView once, then window.__shoot(position, target, fov, anchor, frames).
SETUP = """async ([w, h]) => {
  const { PlantView, loadScene } = await import('./splat.js');
  const scene = await loadScene();
  const el = document.getElementById('view');
  el.innerHTML = ''; el.style.width = w + 'px'; el.style.height = h + 'px';
  const view = new PlantView(el, { interactive: false, width: w, height: h });
  await view.load(undefined, scene.sky);
  window.__shoot = async (p, t, f, a, n) => {
    view.look(p, t, f); await view.renderFrames(n);
    return { anchor_px: view.project(a), png: view.renderer.domElement.toDataURL('image/png') };
  };
  return true;
}"""
SHOOT = "([p, t, f, a, n]) => window.__shoot(p, t, f, a, n)"

NODE_DRIVER = r"""
const { chromium } = require('playwright');
const readline = require('readline');
(async () => {
  const [exe, args] = JSON.parse(process.argv[1]);
  const browser = await chromium.launch({ executablePath: exe || undefined, args });
  const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
  const out = (o) => process.stdout.write(JSON.stringify(o) + '\n');
  for await (const line of readline.createInterface({ input: process.stdin })) {
    const m = JSON.parse(line);
    try {
      if (m.goto) { await page.goto(m.goto); out({ ok: true }); }
      else out({ ok: true, r: await page.evaluate(([src, a]) => (0, eval)('(' + src + ')')(a), [m.js, m.arg]) });
    } catch (e) { out({ ok: false, error: String(e) }); }
  }
  await browser.close();
})();
"""


def pose_for(asset: str, scene: dict) -> dict:
    anchor = scene["anchors"][asset]
    stations = scene.get("stations", {})
    own = [sid for sid, st in stations.items() if st["asset"] == asset]
    if own:
        st = stations[own[0]]
        return {"position": st["position"], "target": st["target"], "fov": st["fov"], "from_station": own[0]}
    k = "52-I" if asset.startswith("52-I") else "52-" if asset.startswith("52-") else re.match(r"[A-Z]+", asset).group(0)
    sib = stations.get(SIBLING.get(k, ""))
    if sib:
        ref = scene["anchors"][sib["asset"]]
        off = [p - r for p, r in zip(sib["position"], ref)]
        toff = [p - r for p, r in zip(sib["target"], ref)]
        fov = sib["fov"]
    else:
        off, toff, fov = KIND_POSE.get(k, DEFAULT_POSE)
    r = lambda v: round(v, 2)
    return {"position": [r(a + o) for a, o in zip(anchor, off)], "target": [r(a + o) for a, o in zip(anchor, toff)],
            "fov": fov, "from_station": None}


class NodePage:
    """The two page calls this script needs (goto, evaluate) over a Node playwright child process."""

    def __init__(self, chromium):
        env = {**os.environ}
        env.setdefault("NODE_PATH", "/opt/node22/lib/node_modules")
        self.proc = subprocess.Popen(["node", "-e", NODE_DRIVER, json.dumps([chromium, ARGS])], env=env, text=True,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def _call(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise SystemExit("node playwright driver exited")
        res = json.loads(line)
        if not res["ok"]:
            raise RuntimeError(res["error"])
        return res.get("r")

    def goto(self, url):
        self._call({"goto": url})

    def evaluate(self, js, arg=None):
        return self._call({"js": js, "arg": arg})

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=60)


def open_page(chromium):
    """(page, close) with Python playwright when installed, else Node playwright."""
    exe = chromium if Path(chromium).exists() else None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        page = NodePage(exe)
        return page, page.close
    pw = sync_playwright().start()
    browser = pw.chromium.launch(executable_path=exe, args=ARGS)
    page = browser.new_page(viewport={"width": 1024, "height": 768})
    return page, lambda: (browser.close(), pw.stop())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated asset ids")
    ap.add_argument("--frames", type=int, default=0, help="extra settle frames per draw")
    ap.add_argument("--tries", type=int, default=6, help="max redraws per view while the splat sort settles")
    ap.add_argument("--wait", type=float, default=3.0, help="seconds between redraws of one view")
    a = ap.parse_args(argv)
    scene = json.loads(SCENE.read_text())
    only = set(a.only.split(",")) if a.only else None
    assets = sorted(x for x in scene["anchors"] if not only or x in only)
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() and only else {}
    OUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    t0 = time.time()
    page, close = open_page(os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium"))
    try:
        page.goto(f"{base}/splat-render.html")      # no ?vp: the page stops at "unknown viewpoint", leaving its importmap
        page.evaluate(SETUP, list(SIZE))
        print(f"plant loaded in {time.time() - t0:.0f} s")
        for asset in assets:
            pose = pose_for(asset, scene)
            # Spark re-sorts in a worker after a big camera move; redraw until two frames agree (or give up after --tries)
            img, shot, tries, deadline = None, None, 0, time.time() + 180
            while tries < a.tries:
                prev = img
                if tries:
                    time.sleep(a.wait)                     # let the sort worker catch up before the next draw
                shot = page.evaluate(SHOOT, [pose["position"], pose["target"], pose["fov"], scene["anchors"][asset], a.frames])
                img = Image.open(BytesIO(base64.b64decode(shot["png"].split(",", 1)[1]))).convert("RGB")
                a_img = np.asarray(img, float)
                if a_img.std(axis=(0, 1)).max() < 8 and time.time() < deadline:
                    img = None                             # all sky: the plant is still streaming in or culled mid-sort
                    time.sleep(1)
                    continue
                tries += 1
                if prev is not None and np.abs(a_img - np.asarray(prev, float)).mean() < 1.0:
                    break
            if img.size != SIZE:
                img = img.resize(SIZE, Image.LANCZOS)
            dst = OUT / f"{asset}.jpg"
            img.save(dst, "JPEG", quality=85)
            ax, ay, _ = shot["anchor_px"]
            index[asset] = {"file": dst.relative_to(REPO).as_posix(), "anchor_px": [round(ax, 1), round(ay, 1)], **pose}
            print(f"{asset}: {pose['from_station'] or 'built pose'} anchor_px={index[asset]['anchor_px']} draws={tries} ({time.time() - t0:.0f} s)")
    finally:
        close()
        server.shutdown()
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n")
    print(f"{len(assets)} views -> {OUT.relative_to(REPO)} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()

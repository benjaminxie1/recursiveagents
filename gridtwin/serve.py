"""Evidence viewer server: python -m gridtwin.serve [--port 8000]

Serves viewer/ at /, plus assets/ and runs/. Stdlib only, so it works with Wi-Fi off.
truth.jsonl is never served (contract C2: only the judge reads hidden truth).
GET /api/runs lists every run folder that has an events.jsonl, newest first.
GET /api/viewpoints lists the camera stations (contract C5) for the inspection wall, without the photo states.
GET /api/requirements returns the requirement catalog and standards titles (dashboard.html).
GET /api/codes returns the codes & standards library: publications, clauses with cached kb/refs text, requirements and assets (codes.html).
Each station also gets a reference_image: a repo image of it in a no-fault state, for the wall to show when a run took none.
"""
import argparse
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parents[1]
VIEWER = REPO / "viewer"
ROOTS = {"assets": REPO / "assets", "runs": REPO / "runs"}
BLOCKED = {"truth.jsonl"}
# The only states a reference image may show: what a fault-free plant looks like (sim/plant.py visual_state).
# Any other state (display_off, oil_leak, handle_tripped, ...) could give a scenario's hidden fault away.
NEUTRAL_STATES = ("healthy", "normal", "array_normal", "connector_normal", "thermal_normal", "handle_closed")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def list_runs(limit=300):
    runs_dir = ROOTS["runs"]
    if not runs_dir.exists():
        return []
    out = []
    for path in runs_dir.rglob("events.jsonl"):
        run = {"id": path.parent.relative_to(runs_dir).as_posix(), "mtime": path.stat().st_mtime,
               "scenario_id": None, "status": None, "events": 0}
        for line in path.read_text().splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            run["events"] += 1
            if e.get("type") == "goal":
                run["scenario_id"] = e["data"].get("scenario_id")
            elif e.get("type") == "final":
                run["status"] = e["data"].get("status")
        out.append(run)
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out[:limit]


def reference_image(vid, asset):
    """A repo image of the station in a neutral state, as a path under assets/, or None: a real photo of the asset,
    else the splat render from this viewpoint, else the placeholder card (which only says "no photo yet").
    The wall captions it as a reference: it is never evidence and never changes a station's status."""
    assets = ROOTS["assets"]
    tiers = (lambda s: sorted(p for p in (assets / "photos" / asset / s).glob("*")
                              if p.suffix.lower() in IMAGE_EXTS and not p.name.startswith(".")),
             lambda s: [assets / "splats" / "renders" / vid / f"{s}.jpg"],
             lambda s: [assets / "photos" / "_placeholders" / asset / f"{s}.png"])
    for files in tiers:
        for state in NEUTRAL_STATES:
            hit = next((p for p in files(state) if p.is_file()), None)
            if hit:
                return "assets/" + hit.relative_to(assets).as_posix()
    return None


def list_viewpoints():
    """Same merge as sim.camera.Camera: assets/photos/viewpoints.yaml wins over the sim's extras on an id clash.
    Only station fields go out; the `states` lists name hidden photo states, so they stay server-side
    (reference_image only ever names a neutral state)."""
    import yaml  # only this endpoint needs it

    merged = {}
    for path in (REPO / "gridtwin" / "sim" / "viewpoints_extra.yaml", ROOTS["assets"] / "photos" / "viewpoints.yaml"):
        if path.exists():
            merged.update((yaml.safe_load(path.read_text()) or {}).get("viewpoints") or {})
    keep = ("asset", "modes", "label", "heading_deg", "map_xy")
    out = [{"id": vid, **{k: vp[k] for k in keep if k in vp}} for vid, vp in sorted(merged.items())]
    for v in out:
        if ref := reference_image(v["id"], v["asset"]):
            v["reference_image"] = ref
    return out


def list_requirements():
    """The requirement catalog and standards titles from plant.yaml (public: the agents read the same via list_requirements)."""
    import yaml

    doc = yaml.safe_load((REPO / "gridtwin" / "sim" / "plant.yaml").read_text())
    return {"requirements": doc.get("requirements") or [], "standards": doc.get("standards") or {}}


def list_codes():
    from gridtwin.codes import build

    return build()


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".jsonl": "text/plain; charset=utf-8", ".js": "text/javascript"}

    def translate_path(self, path):
        parts = [p for p in unquote(urlsplit(path).path).split("/") if p]
        if any(p in ("..", ".") or p.startswith(".") or p in BLOCKED for p in parts):
            return str(REPO / ".blocked" / "404")  # never exists
        if parts and parts[0] in ROOTS:
            return str(ROOTS[parts[0]].joinpath(*parts[1:]))
        return str(VIEWER.joinpath(*parts))

    def do_GET(self):
        api = {"/api/runs": list_runs, "/api/viewpoints": list_viewpoints, "/api/requirements": list_requirements,
               "/api/codes": list_codes}.get(urlsplit(self.path).path)
        if api:
            body = json.dumps(api()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # the 1 s poll must see appends
        super().end_headers()

    def log_message(self, fmt, *args):
        if args and str(args[1])[:1] in "45":  # only log errors; polling is noisy
            super().log_message(fmt, *args)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"GridTwin evidence view on http://{a.host}:{a.port}/  (add ?run=<id> or ?run=<id>&replay=1; /wall.html for the inspection wall)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

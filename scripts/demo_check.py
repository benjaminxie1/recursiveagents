"""One-command pre-demo check: python -m scripts.demo_check

Runs the pre-tested demo scenarios with the scripted_correct agent, starts the viewer server on a free port and
GETs every page and API the demo uses, checks each viewpoint has an image on disk, and (if CRUSOE_API_KEY is set)
checks every role's model id is listed. Prints one PASS/FAIL line per check and exits 1 on any FAIL. No key needed.
"""
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIOS = ["s06_stale_scada", "s03_fan_relapse", "s05_xfmr_incipient", "s00_control"]
PAGES = ["/", "/wall.html", "/twin.html", "/dashboard.html", "/demo.html"]
APIS = ["/api/runs", "/api/viewpoints", "/api/requirements", "/api/demo/runs"]
FILES = ["/assets/splats/plant.splat", "/assets/splats/scene.json"]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

results = []  # (status, name, detail)


def record(status, name, detail=""):
    results.append((status, name, detail))
    print(f"{status:4s}  {name}" + (f"  ({detail})" if detail else ""), flush=True)


def demo_scenarios():
    """The pre-tested scenario list from docs/DEMO.md (backticked s-ids on its 'pre-tested' line), else the default."""
    doc = REPO / "docs" / "DEMO.md"
    if doc.exists():
        for line in doc.read_text().splitlines():
            if "pre-tested" in line:
                ids = re.findall(r"`(s\d{2}[a-z]?_[a-z0-9_]+)`", line)
                if ids:
                    return list(dict.fromkeys(ids))
    return DEFAULT_SCENARIOS


def check_scenarios(ids):
    from gridtwin import bench

    agent = bench.load_agent("scripted_correct")
    with tempfile.TemporaryDirectory() as tmp:
        rows = bench.run_bench(agent, ids, runs_dir=Path(tmp))
    by_id = {r["scenario_id"]: r for r in rows}
    for sid in ids:
        r = by_id.get(sid)
        if r is None:
            record("FAIL", f"scenario {sid}", "no result row")
        elif r.get("success"):
            record("PASS", f"scenario {sid}", f"{r.get('status')}, verified success")
        else:
            record("FAIL", f"scenario {sid}", f"status {r.get('status')}, expected {r.get('expected')}")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except OSError as e:
        return None, str(e).encode()


def start_server(port):
    """gridtwin.demo.serve serves everything gridtwin.serve does plus the demo page's API."""
    proc = subprocess.Popen([sys.executable, "-m", "gridtwin.demo.serve", "--port", str(port)], cwd=REPO,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if proc.poll() is not None:
            raise RuntimeError("server exited: " + proc.stderr.read().decode()[-300:])
        if get(base, "/api/runs")[0] == 200:
            return proc, base
        time.sleep(0.1)
    proc.kill()
    raise RuntimeError("server did not answer within 10 s")


def check_server(base, get=get):
    for path in PAGES:
        if not (REPO / "viewer" / (path.lstrip("/") or "index.html")).exists():
            record("WARN", f"page {path}", "file missing, skipped")
            continue
        code, _ = get(base, path)
        record("PASS" if code == 200 else "FAIL", f"page {path}", f"HTTP {code}")
    bodies = {}
    for path in APIS + FILES:
        code, body = get(base, path)
        bodies[path] = body if code == 200 else None
        record("PASS" if code == 200 else "FAIL", f"GET {path}", f"HTTP {code}")

    vps = bodies.get("/api/viewpoints")
    if vps is not None:
        leaked = [v.get("id") for v in json.loads(vps) if "states" in v]
        record("FAIL" if leaked else "PASS", "/api/viewpoints hides photo states",
               f"states on {', '.join(leaked)}" if leaked else "")

    runs = json.loads(bodies["/api/runs"]) if bodies.get("/api/runs") else []
    run = next((r for r in runs if r["id"].startswith("demo-")), runs[0] if runs else None)
    if run is None:
        record("FAIL", "run replay", "no runs listed by /api/runs")
        return
    rid = run["id"]
    code, _ = get(base, f"/?run={rid}&replay=1")
    record("PASS" if code == 200 else "FAIL", f"replay page /?run={rid}&replay=1", f"HTTP {code}")
    code, body = get(base, f"/runs/{rid}/events.jsonl")
    ok = code == 200 and body.strip()
    record("PASS" if ok else "FAIL", f"run log /runs/{rid}/events.jsonl", f"HTTP {code}")
    code, _ = get(base, f"/runs/{rid}/truth.jsonl")
    record("PASS" if code != 200 else "FAIL", "truth.jsonl is refused", f"HTTP {code}")


def check_viewpoints():
    import yaml

    vps = (yaml.safe_load((REPO / "assets" / "photos" / "viewpoints.yaml").read_text()) or {}).get("viewpoints") or {}
    missing = []
    for vid, vp in vps.items():
        dirs = [REPO / "assets" / "photos" / vp.get("asset", ""), REPO / "assets" / "splats" / "renders" / vid]
        if not any(p.suffix.lower() in IMAGE_EXTS for d in dirs if d.is_dir() for p in d.rglob("*")):
            missing.append(vid)
    record("FAIL" if missing else "PASS", f"viewpoint images ({len(vps)} viewpoints)",
           f"none on disk for {', '.join(missing)}" if missing else "")


def check_models():
    if not os.environ.get("CRUSOE_API_KEY"):
        record("SKIP", "Crusoe model ids", "skipped (no key)")
        return
    from gridtwin.config import ROLES
    from gridtwin.llm import client

    try:
        available = {m.id for m in client().models.list().data}
    except Exception as e:  # network or auth: report, don't crash the rest
        record("FAIL", "Crusoe model ids", f"GET /v1/models failed: {e}")
        return
    missing = sorted({m for m in ROLES.values() if m not in available})
    record("FAIL" if missing else "PASS", "Crusoe model ids", f"missing {', '.join(missing)}" if missing else "")


def main():
    t0 = time.time()
    try:
        from dotenv import load_dotenv  # optional, so a local .env key is picked up
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    check_scenarios(demo_scenarios())
    try:
        proc, base = start_server(free_port())
    except RuntimeError as e:
        record("FAIL", "viewer server", str(e))
    else:
        try:
            check_server(base)
        finally:
            proc.kill()
            proc.wait()
    check_viewpoints()
    check_models()
    fails = sum(1 for s, *_ in results if s == "FAIL")
    print(f"\n{'ALL PASS' if not fails else f'{fails} FAIL'} in {time.time() - t0:.1f} s")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

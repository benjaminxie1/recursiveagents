"""Demo server: python -m gridtwin.demo.serve [--port 8010], then open http://localhost:8010/demo.html

Everything gridtwin.serve serves (viewer/ at /, assets/, runs/, /api/runs, /api/viewpoints), plus:
  POST /api/demo/run  {"scenario": "s06_stale_scada", "memory": true, "fresh_memory": false, "stub": false}
                      starts `python -m gridtwin.demo.run` in the background and returns {"run_id"} at once;
                      the page then polls /runs/<run_id>/events.jsonl.
  GET  /api/demo/runs demo runs (folders with mail/ or demo.* events), newest first, with status from result.json.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from gridtwin import serve
from gridtwin.demo.pipeline import SCENARIO

RUNS = serve.ROOTS["runs"]
LOGS = RUNS / "_demo-logs"
SCENARIO_RE = re.compile(r"^s\d{2}[a-z]?_[a-z0-9_]+$")


def list_demo_runs(limit=50) -> list[dict]:
    out = []
    if not RUNS.exists():
        return out
    for d in RUNS.iterdir():
        ev = d / "events.jsonl"
        if not (d.is_dir() and ev.exists() and (d / "mail").is_dir()):
            continue
        row = {"id": d.name, "mtime": ev.stat().st_mtime, "status": None, "rounds": None, "scenario_id": None}
        res = d / "result.json"
        if res.exists():
            try:
                r = json.loads(res.read_text())
                row.update(status=r.get("status"), rounds=r.get("rounds"), stub=r.get("stub"))
            except ValueError:
                pass
        else:
            row["status"] = "RUNNING"
        out.append(row)
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out[:limit]


def start_run(body: dict) -> dict:
    scenario = str(body.get("scenario") or SCENARIO)
    if not SCENARIO_RE.match(scenario):
        raise ValueError(f"bad scenario {scenario!r}")
    run_id = f"e2e-{scenario.split('_')[0]}-{time.strftime('%Y%m%d-%H%M%S')}"
    cmd = [sys.executable, "-m", "gridtwin.demo.run", "--scenario", scenario, "--run-id", run_id]
    if body.get("memory") is False:
        cmd += ["--memory", str(RUNS / f"_nomem-{run_id}")]
    if body.get("fresh_memory"):
        cmd.append("--fresh-memory")
    if body.get("stub"):
        cmd.append("--stub")
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / f"{run_id}.log", "w") as fh:
        subprocess.Popen(cmd, cwd=serve.REPO, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    return {"run_id": run_id, "cmd": " ".join(cmd[1:])}


class Handler(serve.Handler):
    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlsplit(self.path).path == "/api/demo/runs":
            return self._json(200, list_demo_runs())
        return super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path != "/api/demo/run":
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            return self._json(200, start_run(body if isinstance(body, dict) else {}))
        except (ValueError, OSError) as e:
            return self._json(400, {"error": str(e)})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"GridTwin demo on http://{a.host}:{a.port}/demo.html  (or ?run=<id>, &replay=1)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""Demo server: python -m gridtwin.demo.serve [--port 8010], then open http://localhost:8010/demo.html

Everything gridtwin.serve serves (viewer/ at /, assets/, runs/, /api/runs, /api/viewpoints), plus the demo API that
any frontend (this repo's demo.html or a separate app on another localhost port) binds to. docs/FRONTEND-API.md has
the shapes; gridtwin/demo/CONTRACT.md has the events.
  GET  /api/demo/health               {ok, schema, scenario, models, keys: {crusoe, jev} (booleans only), diffusion}
  POST /api/demo/run                  {"scenario", "memory": true, "fresh_memory": false, "stub": false}
                                      starts `python -m gridtwin.demo.run` in the background, returns {run_id, urls} at once
  GET  /api/demo/runs                 demo runs, newest first, with status
  GET  /api/demo/runs/<id>            the run folded into one snapshot (gridtwin/demo/view.py)
  GET  /api/demo/runs/<id>/events?after=<seq>   raw events after seq, plus {last_seq, done}
  GET  /api/demo/runs/<id>/stream     Server-Sent Events: one `demo` event per log line, then `end` with the snapshot
  GET  /api/demo/memory[?kb=e2e-kb]   the memory flywheel: lessons, scores, rounds per run
Cross-origin calls from http(s)://localhost or 127.0.0.1 on any port are allowed (GRIDTWIN_CORS_ORIGINS adds more,
comma separated); other origins cannot start runs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from gridtwin import serve
from gridtwin.demo import view
from gridtwin.demo.pipeline import SCENARIO

RUNS = serve.ROOTS["runs"]
LOGS = RUNS / "_demo-logs"
SCENARIO_RE = re.compile(r"^s\d{2}[a-z]?_[a-z0-9_]+$")
SCENARIOS_DIR = serve.REPO / "gridtwin" / "sim" / "scenarios"
DEMO_FAMILY = "s06"  # the demo tells the stale-SCADA story; other scenarios run but the pipeline would misdiagnose them
PID_FILE = "run.pid"  # written by the server beside a run it starts, so a restarted server can tell a dead run from a live one
BODY_KEYS = {"scenario": str, "stub": bool, "memory": bool, "fresh_memory": bool}
KB_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")  # a lesson store folder under runs/ (e2e-kb, _nomem-<run>)
LOCAL_ORIGIN_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d{1,5})?$")
EXTRA_ORIGINS = {o.strip().rstrip("/") for o in os.environ.get("GRIDTWIN_CORS_ORIGINS", "").split(",") if o.strip()}
DEFAULT_KB = "e2e-kb"
STREAM_POLL_S = 0.25
STREAM_IDLE_S = 900  # a stream on a run that never finishes closes after this long without a new event

PROCS: dict[str, subprocess.Popen] = {}  # runs this server started, so a crashed run shows FAILED, not RUNNING forever
PROCS_LOCK = threading.Lock()


def origin_allowed(origin: str | None) -> bool:
    return bool(origin) and (LOCAL_ORIGIN_RE.match(origin) is not None or origin.rstrip("/") in EXTRA_ORIGINS)


def run_dir(run_id: str):
    """The folder of a demo run, or None. Ids are plain names under runs/ (no paths, no dot files)."""
    if not view.RUN_ID_RE.match(run_id or ""):
        return None
    d = RUNS / run_id
    with PROCS_LOCK:
        started_here = run_id in PROCS  # a run started a moment ago may not have written its log yet
    return d if started_here or (d.is_dir() and ((d / "events.jsonl").exists() or (d / PID_FILE).exists())) else None


def supported_scenarios() -> list[str]:
    return sorted(p.stem for p in SCENARIOS_DIR.glob(f"{DEMO_FAMILY}*.yaml") if SCENARIO_RE.match(p.stem))


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def proc_state(run_id: str) -> dict | None:
    with PROCS_LOCK:
        p = PROCS.get(run_id)
    if p is None:  # started by an earlier server (or by hand): the pid file, when there is one, says whether it still runs
        try:
            pid = int((RUNS / run_id / PID_FILE).read_text().strip())
        except (OSError, ValueError):
            return None
        if _pid_alive(pid):
            return {"exit_code": None, "error": None}
        return {"exit_code": -1, "error": "the run process ended without a result (it died or the server restarted)"}
    code = p.poll()
    if code is None:
        return {"exit_code": None, "error": None}
    return {"exit_code": code, "error": _log_error(run_id, code)}


def _log_error(run_id: str, code: int) -> str:
    """The last exception line of a run's log (never the whole log), for a run that died before demo.final."""
    try:
        lines = (LOGS / f"{run_id}.log").read_text(errors="replace").strip().splitlines()
    except OSError:
        lines = []
    last = next((ln for ln in reversed(lines) if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Exit)\b", ln)), "")
    return (f"run process exited with code {code}" + (f": {last}" if last else ""))[:300]


def list_demo_runs(limit=50) -> list[dict]:
    out = []
    if not RUNS.exists():
        return out
    with PROCS_LOCK:
        started = set(PROCS)
    for d in RUNS.iterdir():
        ev = d / "events.jsonl"
        ours = d.name in started or (d / PID_FILE).exists()
        if not d.is_dir() or not (ours or (ev.exists() and (d / "mail").is_dir())):
            continue
        mtime = (ev if ev.exists() else d).stat().st_mtime
        row = {"id": d.name, "mtime": mtime, "status": None, "rounds": None, "scenario_id": None, "stub": None}
        r = view.read_result(d)
        if r:
            row.update(status=r.get("status"), rounds=r.get("rounds"), stub=r.get("stub"),
                       scenario_id=(r.get("metrics") or {}).get("scenario_id"))
        else:
            ps = proc_state(d.name)
            row["status"] = "FAILED" if ps and ps["exit_code"] is not None else "RUNNING"
        out.append(row)
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out[:limit]


def _int_param(v, name: str) -> int:
    try:
        n = int(v or 0)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None
    if n < 0:
        raise ValueError(f"{name} must be 0 or more")
    return n


def run_urls(run_id: str, kb: str = DEFAULT_KB) -> dict:
    return {"snapshot": f"/api/demo/runs/{run_id}", "events": f"/api/demo/runs/{run_id}/events?after=0",
            "stream": f"/api/demo/runs/{run_id}/stream", "events_jsonl": f"/runs/{run_id}/events.jsonl",
            "memory": f"/api/demo/memory?kb={kb}", "page": f"/demo.html?run={run_id}"}


def check_body(body) -> dict:
    """The POST body: a JSON object with only known keys of the right types (a typo must not start a paid live run)."""
    if not isinstance(body, dict):
        raise ValueError("the body must be a JSON object")
    unknown = sorted(set(body) - set(BODY_KEYS))
    if unknown:
        raise ValueError(f"unknown keys {unknown}; allowed: {sorted(BODY_KEYS)}")
    for k, t in BODY_KEYS.items():
        if k in body and not isinstance(body[k], t):
            raise ValueError(f"{k} must be a {'boolean' if t is bool else 'string'}")
    scenario = body.get("scenario") or SCENARIO
    if scenario not in supported_scenarios():
        raise ValueError(f"scenario {scenario!r} is not one the demo supports: {supported_scenarios()}")
    return body


def _reserve_run_id(scenario: str) -> str:
    """A new run folder, claimed atomically: two POSTs in the same second get two runs."""
    RUNS.mkdir(parents=True, exist_ok=True)
    base = f"e2e-{scenario.split('_')[0]}-{time.strftime('%Y%m%d-%H%M%S')}"
    for n in range(1, 1000):
        run_id = base if n == 1 else f"{base}-{n}"
        try:
            (RUNS / run_id).mkdir()
            return run_id
        except FileExistsError:
            continue
    raise ValueError("could not reserve a run id")


def start_run(body: dict) -> dict:
    body = check_body(body)
    scenario = body.get("scenario") or SCENARIO
    run_id = _reserve_run_id(scenario)
    cmd = [sys.executable, "-m", "gridtwin.demo.run", "--scenario", scenario, "--run-id", run_id, "--runs-dir", str(RUNS)]
    # the lesson store: live runs share e2e-kb, stub rehearsals share e2e-kb-stub, memory off gets a throwaway store
    kb = f"_nomem-{run_id}" if body.get("memory") is False else ("e2e-kb-stub" if body.get("stub") else DEFAULT_KB)
    cmd += ["--memory", str(RUNS / kb)]
    if body.get("fresh_memory"):
        cmd.append("--fresh-memory")
    if body.get("stub"):
        cmd.append("--stub")
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / f"{run_id}.log", "w") as fh:
        p = subprocess.Popen(cmd, cwd=serve.REPO, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    with PROCS_LOCK:
        PROCS[run_id] = p
    (RUNS / run_id / PID_FILE).write_text(str(p.pid))
    return {"run_id": run_id, "kb": kb, "cmd": " ".join(cmd[1:]), "urls": run_urls(run_id, kb)}


def health() -> dict:
    from gridtwin.config import ROLES
    from gridtwin.demo import diffusion

    try:
        diff = "diffusion" if diffusion.diffusion_enabled() else "photo_bank"
    except Exception:  # noqa: BLE001 - health never fails on an optional piece
        diff = "photo_bank"
    return {"ok": True, "schema": view.SCHEMA, "scenario": SCENARIO, "scenarios": supported_scenarios(), "models": dict(ROLES),
            "keys": {"crusoe": bool(os.environ.get("CRUSOE_API_KEY")), "jev": bool(os.environ.get("JEV_API_KEY"))},
            "diffusion": diff}


def memory_view(kb: str | None) -> dict:
    from gridtwin.demo import memory as M

    name = kb or DEFAULT_KB
    if not KB_RE.match(name) or ".." in name:
        raise ValueError(f"bad kb {name!r}")
    d = RUNS / name
    if not d.is_dir():
        return {"kb": name, "exists": False, "stats": None, "lessons": []}
    m = M.Memory(d)
    lessons = [{"id": l.get("id"), "summary": M.summary(l), "fix": l.get("fix"), "forbid": l.get("forbid") or [],
                "acceptance": l.get("acceptance") or [], "source_run": l.get("source_run") or l.get("run_id")}
               for l in m.lessons()]
    return {"kb": name, "exists": True, "stats": m.stats(), "lessons": lessons}


class Handler(serve.Handler):
    def end_headers(self):
        origin = self.headers.get("Origin")
        if origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204 if origin_allowed(self.headers.get("Origin")) else 403)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        url = urlsplit(self.path)
        parts = [p for p in url.path.split("/") if p]
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        if parts[:2] != ["api", "demo"]:
            return super().do_GET()
        try:
            if parts[2:] == ["health"]:
                return self._json(200, health())
            if parts[2:] == ["runs"]:
                return self._json(200, list_demo_runs())
            if parts[2:] == ["memory"]:
                return self._json(200, memory_view(q.get("kb")))
            if len(parts) in (4, 5) and parts[2] == "runs":
                d = run_dir(parts[3])
                if d is None:
                    return self._json(404, {"error": f"no demo run {parts[3]!r}"})
                tail = parts[4] if len(parts) == 5 else None
                if tail is None:
                    return self._json(200, view.snapshot(d, proc_state=proc_state(d.name)))
                if tail == "events":
                    after = _int_param(q.get("after"), "after")
                    evs = view.read_events(d, after)
                    snap_done = self._done(d)
                    return self._json(200, {"run_id": d.name, "events": evs,
                                            "last_seq": max([after] + [int(e.get("seq") or 0) for e in evs]),
                                            "done": snap_done})
                if tail == "stream":
                    after = _int_param(q.get("after"), "after")
                    try:  # a browser's EventSource resends the last id it saw; junk means start over
                        last = int(self.headers.get("Last-Event-ID") or after)
                    except ValueError:
                        last = after
                    return self._stream(d, last)
            return self._json(404, {"error": "not found"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})

    def _done(self, d) -> bool:
        if view.read_result(d):
            return True
        ps = proc_state(d.name)
        return bool(ps and ps["exit_code"] is not None)

    def _stream(self, d, last: int) -> None:
        """Server-Sent Events: `demo` events carry one log line each (id = seq, so EventSource resumes after a drop),
        then one `end` event with the final snapshot. The run can still be appending while we read.
        A reconnect after `end` (the page did not close its EventSource) gets 204, which stops EventSource for good."""
        if last > 0 and self._done(d) and not view.read_events(d, last):
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.close_connection = True  # the stream ends with the run; the client sees EOF after `end`
        idle_since = time.monotonic()
        try:
            while True:
                evs = view.read_events(d, last)
                for e in evs:
                    last = max(last, int(e.get("seq") or 0))
                    self.wfile.write(f"id: {last}\nevent: demo\ndata: {json.dumps(e, default=str)}\n\n".encode())
                if evs:
                    idle_since = time.monotonic()
                    self.wfile.flush()
                done = self._done(d)
                if done and not view.read_events(d, last):
                    snap = view.snapshot(d, proc_state=proc_state(d.name))
                    self.wfile.write(f"event: end\ndata: {json.dumps(snap, default=str)}\n\n".encode())
                    self.wfile.flush()
                    return
                if time.monotonic() - idle_since > STREAM_IDLE_S:
                    self.wfile.write(b"event: idle\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                if not evs:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                time.sleep(STREAM_POLL_S)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self):
        if urlsplit(self.path).path != "/api/demo/run":
            return self._json(404, {"error": "not found"})
        origin = self.headers.get("Origin")
        if origin and not origin_allowed(origin):
            return self._json(403, {"error": f"origin {origin} may not start runs"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            return self._json(200, start_run(body))
        except (ValueError, OSError) as e:
            return self._json(400, {"error": str(e)})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    server = ThreadingHTTPServer((a.host, a.port), Handler)
    server.daemon_threads = True  # open SSE streams never block shutdown
    print(f"GridTwin demo on http://{a.host}:{a.port}/demo.html  (or ?run=<id>, &replay=1); API: /api/demo/health", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

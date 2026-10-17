"""Evidence viewer server: python -m gridtwin.serve [--port 8000]

Serves viewer/ at /, plus assets/ and runs/. Stdlib only, so it works with Wi-Fi off.
truth.jsonl is never served (contract C2: only the judge reads hidden truth).
GET /api/runs lists every run folder that has an events.jsonl, newest first.
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
        if urlsplit(self.path).path == "/api/runs":
            body = json.dumps(list_runs()).encode()
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
    print(f"GridTwin evidence view on http://{a.host}:{a.port}/  (add ?run=<id> or ?run=<id>&replay=1)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

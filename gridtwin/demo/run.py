"""One command for the end-to-end demo.

  python -m gridtwin.demo.run --scenario s06_stale_scada [--memory runs/e2e-kb] [--fresh-memory] [--stub]

Prints one line per stage; the page (python -m gridtwin.demo.serve, then demo.html?run=<id>) shows the rest.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time

from gridtwin.demo import memory as M
from gridtwin.demo.pipeline import MAX_ROUNDS, SCENARIO, Demo


def trace(log):
    inner = log.emit

    def emit(actor, type, data):
        ev = inner(actor, type, data)
        if type.startswith("demo.") and type not in ("demo.sensor", "demo.check"):
            d = data or {}
            brief = {k: d.get(k) for k in ("stage", "status", "round", "asset", "fault", "id", "depth", "direction", "subject",
                                          "verdict", "pass", "failed", "op", "effect", "backend", "summary") if d.get(k) not in (None, "", [])}
            print(f"{time.strftime('%H:%M:%S')} {type:16} {json.dumps(brief, default=str)[:180]}", flush=True)
        return ev

    log.emit = emit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default=SCENARIO)
    ap.add_argument("--memory", default=None, help="lesson store directory (default runs/e2e-kb)")
    ap.add_argument("--fresh-memory", action="store_true", help="empty the lesson store first (first run of a demo)")
    ap.add_argument("--stub", action="store_true", help="no model calls: deterministic stand-ins (offline rehearsal)")
    ap.add_argument("--run-id")
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--runs-dir", default=None, help="where run folders go (default runs/)")
    a = ap.parse_args(argv)
    if a.fresh_memory:
        d = M.Memory(a.memory).dir
        shutil.rmtree(d / "lessons", ignore_errors=True)
    demo = Demo(a.scenario, a.memory, a.stub, a.run_id, a.max_rounds, a.runs_dir)
    trace(demo.log._log)
    print(f"run_id {demo.run_id}  scenario {a.scenario}  stub={a.stub}  page: demo.html?run={demo.run_id}", flush=True)
    out = demo.run()
    print(json.dumps(out, indent=2, default=str))
    return 0 if out["status"] == "VERIFIED" else 1


if __name__ == "__main__":
    sys.exit(main())

"""One run as one JSON document, for any frontend: fold runs/<id>/events.jsonl into a snapshot.

The snapshot is the stable shape a page binds to (schema SCHEMA, documented in docs/FRONTEND-API.md); the raw events
stay available for pages that want to fold them themselves (gridtwin/demo/CONTRACT.md lists every demo.* event).
Pure and stdlib only: it reads files and never writes, so the server can call it on every poll.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA = "gridtwin.demo.snapshot/1"
STAGES = ("diagnose", "plan", "dispatch", "contractor", "verify", "redispatch", "memory", "final")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def read_events(run_dir: Path, after: int = 0) -> list[dict]:
    """Events with seq > after. A half-written last line (the run is still appending) is skipped, not an error."""
    path = Path(run_dir) / "events.jsonl"
    if not path.exists():
        return []
    out = []
    with path.open() as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and int(e.get("seq") or 0) > after:
                out.append(e)
    return out


def read_result(run_dir: Path) -> dict | None:
    try:
        r = json.loads((Path(run_dir) / "result.json").read_text())
        return r if isinstance(r, dict) else None
    except (OSError, ValueError):
        return None


def snapshot(run_dir: Path, events: list[dict] | None = None, proc_state: dict | None = None) -> dict:
    """Everything a page shows for one run. proc_state is the server's view of a run it started:
    {"exit_code": int | None, "error": str | None}; a run whose process died without result.json is FAILED."""
    run_dir = Path(run_dir)
    evs = read_events(run_dir) if events is None else events
    result = read_result(run_dir)
    s = {
        "schema": SCHEMA, "run_id": run_dir.name, "scenario": None, "goal": None, "status": "RUNNING", "done": False,
        "stub": None, "error": None, "current": None, "stages": [], "rounds": 0, "twin_url": None,
        "sensors": [], "cameras": [], "diagnosis": None, "agents": [], "teams": [], "work_orders": [], "emails": [],
        "contractor_visits": [], "checks": [], "jev": [], "diffusion": [], "verdicts": [],
        "memory": {"recall": None, "writes": [], "scores": [], "stats": None},
        "final": None, "judge": None, "usd": None, "wall_s": None, "last_seq": 0, "events": len(evs),
    }
    stage_rows: dict[tuple, dict] = {}
    agents: dict[str, dict] = {}
    sensors: dict[str, dict] = {}
    for e in evs:
        t, d, seq = e.get("type"), e.get("data") or {}, int(e.get("seq") or 0)
        s["last_seq"] = max(s["last_seq"], seq)
        rnd = d.get("round") if isinstance(d.get("round"), int) else 0
        if t not in ("demo.stage", "demo.memory") and rnd >= 1:
            s["rounds"] = max(s["rounds"], rnd)
        if t == "goal":
            s["goal"], s["scenario"] = d.get("text"), d.get("scenario_id")
        elif t == "demo.stage":
            key = (d.get("stage"), rnd)
            row = stage_rows.setdefault(key, {"stage": d.get("stage"), "round": rnd, "status": None, "start_seq": seq, "seq": seq})
            row["status"] = "done" if d.get("status") == "end" else "active"
            if d.get("status") == "start":
                s["current"] = {"stage": d.get("stage"), "round": rnd, "seq": seq}
            if d.get("twin_url"):
                s["twin_url"] = d["twin_url"]
        elif t == "demo.sensor":
            sensors[d.get("asset")] = {**d, "seq": seq}
        elif t == "demo.camera":
            s["cameras"].append({**d, "actor": e.get("actor"), "seq": seq})
        elif t == "demo.diagnosis":
            s["diagnosis"] = {**d, "seq": seq}
            s["twin_url"] = d.get("twin_url") or s["twin_url"]
        elif t == "demo.agent" and d.get("id"):
            prev = agents.get(d["id"], {})
            agents[d["id"]] = {**prev, **{k: v for k, v in d.items() if v is not None or k not in prev},
                               "actor": e.get("actor"), "first_seq": prev.get("first_seq", seq), "seq": seq}
        elif t == "demo.workorder":
            s["work_orders"].append({**d, "seq": seq})
        elif t == "demo.email":
            s["emails"].append({**d, "actor": e.get("actor"), "seq": seq})
        elif t == "demo.contractor":
            s["contractor_visits"].append({**d, "seq": seq})
        elif t == "demo.check":
            s["checks"].append({**d, "seq": seq})
        elif t == "demo.jev":
            s["jev"].append({**d, "seq": seq})
        elif t == "demo.diffusion":
            s["diffusion"].append({**d, "seq": seq})
        elif t == "demo.verdict":
            s["verdicts"].append({**d, "seq": seq})
        elif t == "demo.memory":
            op, mem = d.get("op"), s["memory"]
            if op == "recall":
                mem["recall"] = {**d, "seq": seq}
            elif op == "write":
                mem["writes"].append({**d, "seq": seq})
            elif op == "score":
                mem["scores"].append({**d, "seq": seq})
            elif op == "stats":  # flat, the same shape as GET /api/demo/memory's stats
                mem["stats"] = {**(d.get("stats") or {}), "effect": d.get("effect"), "seq": seq}
        elif t == "demo.final":
            s["final"] = {**d, "seq": seq}
    s["sensors"] = list(sensors.values())
    s["stages"] = sorted(stage_rows.values(), key=lambda r: r["start_seq"])
    s["agents"] = sorted(agents.values(), key=lambda a: a["first_seq"])
    s["teams"] = _teams(s["agents"])

    if result:
        s["status"], s["done"] = result.get("status") or "UNKNOWN", True
        s["stub"], s["judge"] = result.get("stub"), result.get("judge")
        s["usd"], s["wall_s"] = result.get("usd"), result.get("wall_s")
    elif s["final"]:
        s["status"], s["done"] = s["final"].get("status") or "UNKNOWN", True
        s["usd"], s["wall_s"] = s["final"].get("usd"), s["final"].get("wall_s")
    elif proc_state and proc_state.get("exit_code") is not None:
        s["status"], s["done"] = "FAILED", True
        s["error"] = proc_state.get("error") or f"run process exited with code {proc_state['exit_code']} before demo.final"
    if s["done"]:
        s["current"] = None
    return s


def _teams(agents: list[dict]) -> list[dict]:
    """The one-level agent trees: one row per lead (depth 0) with its sub-agents (depth 1), in spawn order."""
    leads = [a for a in agents if not a.get("parent")]
    kids: dict[str, list[str]] = {}
    for a in agents:
        if a.get("parent"):
            kids.setdefault(a["parent"], []).append(a["id"])
    return [{"team": a.get("team") or "plan", "round": a.get("round") or 0, "lead": a["id"], "model": a.get("model"),
             "status": a.get("status"), "members": kids.get(a["id"], []), "seq": a.get("first_seq")} for a in leads]

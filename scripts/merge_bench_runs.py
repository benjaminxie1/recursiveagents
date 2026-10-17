"""Rebuild bench rows from judged episodes on disk and merge several bench run folders.

Run from the repo root:
    .venv/bin/python -m scripts.merge_bench_runs runs/bench-<ts>-full [more run dirs...] [--agent NAME] [--out DIR]

A row is the episode's `judge` event metrics plus the fields run_episode adds (fault_types, memory,
lessons_at_start, lesson ids), so totals() and to_markdown() see the same shape as a normal bench run.
Episodes without a judge event (still running, or killed) are listed as missing.
Written for held-out lanes run as `--agent <name> --kb kb/snapshots/refs-v1 --memory frozen`. Pass one arm's run dirs
per call: rows are keyed by scenario only, so mixing arms would merge them into one set.
"""
import argparse
import json
from pathlib import Path

from gridtwin import bench
from gridtwin.sim import episode

KB = Path("kb/snapshots/refs-v1")
INFRA_ERRORS = ("RateLimitError", "InternalServerError", "APIConnectionError", "APITimeoutError")


class DiskLog:
    def __init__(self, events):
        self.events = events

    def of_type(self, t):
        return [e for e in self.events if e.get("type") == t]


def row_from(ep_dir: Path):
    f = ep_dir / "events.jsonl"
    if not f.exists():
        return None
    events = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    judged = [e for e in events if e.get("type") == "judge"]
    if not judged:
        return None
    m = judged[-1]["data"]["metrics"]
    sc = episode.load_scenario(m["scenario_id"])
    return {**m, "fault_types": sorted({x["type"] for x in sc.get("faults") or []}), "memory": "frozen",
            "lessons_at_start": len(list((KB / "lessons").glob("*.json"))), **bench.lesson_ids(DiskLog(events)),
            "_source": str(ep_dir)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dirs", nargs="+")
    ap.add_argument("--agent", default="full", help="arm label for the table and results.json")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    want = episode.list_scenarios("heldout")
    # Rules fixed before any lane finished: a scenario judged twice keeps the episode that finished
    # first (events.jsonl mtime = judge time), and an ERROR caused by the API (retries used up) is
    # dropped for a rerun; any other ERROR counts as a failure.
    rows, infra = {}, []
    for d in a.run_dirs:
        for ep in Path(d).glob("*-r0"):
            r = row_from(ep)
            if not r or r["scenario_id"] not in want:
                continue
            r["_finished"] = (ep / "events.jsonl").stat().st_mtime
            if r["status"] == "ERROR" and any(k in json.dumps(r) or k in (ep / "events.jsonl").read_text()[-4000:] for k in INFRA_ERRORS):
                infra.append(str(ep))
                continue
            old = rows.get(r["scenario_id"])
            if old is None or r["_finished"] < old["_finished"]:
                rows[r["scenario_id"]] = r
    for p in infra:
        print(f"  dropped (API error, rerun): {p}")
    # Rate limits inside a kept episode: a worker restarted from saved history after its retries ran out,
    # or the verifier failed closed because its model call failed. Both are reported, never silently kept.
    notes = []
    for r in rows.values():
        ev = [json.loads(l) for l in (Path(r["_source"]) / "events.jsonl").read_text().splitlines() if l.strip()]
        restarts = sum(1 for e in ev if e.get("type") == "supervisor" and "RateLimitError" in json.dumps(e.get("data")))
        closed = sum(1 for e in ev if "verifier model unavailable" in json.dumps(e.get("data")))
        r["_rate_limit_restarts"], r["_verifier_failed_closed"] = restarts, closed
        if restarts or closed:
            notes.append(f"{r['scenario_id']}: {restarts} worker restart(s) after rate limits, verifier failed closed {closed}x")
    ordered = [rows[s] for s in want if s in rows]
    missing = [s for s in want if s not in rows]
    tot = bench.totals(ordered)
    label = f"heldout ({len(ordered)} of {len(want)}; missing: {', '.join(missing) or 'none'})"
    md = bench.to_markdown(a.agent, label, ordered, tot, "frozen")
    md += "\nrate limits inside kept episodes: " + ("; ".join(notes) if notes else "none") + "\n"
    print(md)
    for r in ordered:
        print(f"  {r['scenario_id']}: {r['_source']}")
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps({"agent": a.agent, "split": "heldout", "scenarios": [r["scenario_id"] for r in ordered],
                                                      "missing": missing, "kb": str(KB), "memory": "frozen",
                                                      "sources": a.run_dirs, "rows": ordered, "totals": tot}, indent=2))
        (out / "results.md").write_text(md)
        print(f"wrote {out}/results.md")


if __name__ == "__main__":
    main()

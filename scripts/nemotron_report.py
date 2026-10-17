"""Per-role model report from run event logs: latency, throughput, caching, cost.

    python -m scripts.nemotron_report runs/<run_id> [runs/bench-... ...] [--json]

Each path is a run directory (has events.jsonl) or a bench directory (one events.jsonl per
episode subdirectory). Reads the `usage` events chat() emits; `latency_s` is present only in
logs written after it was added, so older logs show token and cost columns with no latency.

Answers two pitch questions: does Nemotron latency stay flat as a worker's append-only history
grows (latency by prompt-size bucket), and what does each role cost per finished task compared
with running the same tokens on Nemotron 3 Super.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from gridtwin.config import ROLES
from gridtwin.llm import cost_usd

SUPER = ROLES["planner"]
BUCKETS = [("<4k", 0, 4_000), ("4-16k", 4_000, 16_000), ("16k+", 16_000, float("inf"))]


def run_dirs(paths) -> list[Path]:
    out = []
    for p in map(Path, paths):
        if (p / "events.jsonl").is_file():
            out.append(p)
        else:
            out += sorted(d.parent for d in p.glob("*/events.jsonl"))
    return out


def load_run(run_dir: Path) -> dict:
    calls, success = [], None
    for line in (run_dir / "events.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if e.get("type") == "usage":
            calls.append({**e["data"], "actor": e.get("actor")})
        elif e.get("type") == "judge":
            success = bool((e["data"].get("metrics") or {}).get("success"))
    if success is None and (run_dir / "result.json").is_file():
        success = bool(json.loads((run_dir / "result.json").read_text()).get("metrics", {}).get("success"))
    return {"run": str(run_dir), "calls": calls, "success": success}


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    i = (len(xs) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def summarize(calls: list) -> dict:
    lat = [c["latency_s"] for c in calls if c.get("latency_s") is not None]
    timed = [c for c in calls if c.get("latency_s")]
    prompt = sum(c.get("prompt", 0) for c in calls)
    cached = sum(c.get("cached", 0) for c in calls)
    completion = sum(c.get("completion", 0) for c in calls)
    return {
        "calls": len(calls),
        "timed_calls": len(lat),
        "latency_p50_s": pct(lat, 0.5),
        "latency_p90_s": pct(lat, 0.9),
        # completion tokens per second of request time, over the calls that carry latency
        "tokens_per_s": (sum(c.get("completion", 0) for c in timed) / sum(c["latency_s"] for c in timed)
                         if timed else None),
        "prompt": prompt, "cached": cached, "completion": completion,
        "cache_hit_rate": cached / prompt if prompt else 0.0,
        "usd": sum(c.get("usd", 0.0) for c in calls),
        "usd_if_super": sum(cost_usd(SUPER, c.get("prompt", 0), c.get("cached", 0), c.get("completion", 0))
                            for c in calls),
        "buckets": {
            name: {"calls": len(b), "median_prompt": pct([c.get("prompt", 0) for c in b], 0.5),
                   "latency_p50_s": pct([c["latency_s"] for c in b if c.get("latency_s") is not None], 0.5),
                   "latency_p90_s": pct([c["latency_s"] for c in b if c.get("latency_s") is not None], 0.9)}
            for name, lo, hi in BUCKETS
            for b in [[c for c in calls if lo <= c.get("prompt", 0) < hi]]
        },
    }


def report(paths) -> dict:
    runs = [load_run(d) for d in run_dirs(paths)]
    groups = defaultdict(list)
    for r in runs:
        for c in r["calls"]:
            groups[(c.get("role", "?"), c.get("model", "?"))].append(c)
    finished = sum(1 for r in runs if r["success"])
    rows = []
    for (role, model), calls in sorted(groups.items()):
        s = summarize(calls)
        s.update(role=role, model=model,
                 usd_per_finished_task=s["usd"] / finished if finished else None,
                 usd_if_super_per_finished_task=s["usd_if_super"] / finished if finished else None)
        rows.append(s)
    total = summarize([c for r in runs for c in r["calls"]])
    return {"runs": len(runs), "finished_tasks": finished, "roles": rows,
            "total": {k: v for k, v in total.items() if k != "buckets"},
            "usd_per_finished_task": total["usd"] / finished if finished else None,
            "usd_if_all_super_per_finished_task": total["usd_if_super"] / finished if finished else None}


def _f(x, fmt="{:.2f}", none="—"):
    return none if x is None else fmt.format(x)


def to_text(rep: dict) -> str:
    lines = [f"{rep['runs']} run(s), {rep['finished_tasks']} finished (judge success)", "",
             "| role | model | calls | p50 s | p90 s | out tok/s | cache hit | USD | USD/finished | same tokens on Super, USD/finished |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rep["roles"]:
        lines.append(f"| {r['role']} | {r['model']} | {r['calls']} | {_f(r['latency_p50_s'])} | {_f(r['latency_p90_s'])} | "
                     f"{_f(r['tokens_per_s'], '{:.0f}')} | {r['cache_hit_rate']:.0%} | {r['usd']:.4f} | "
                     f"{_f(r['usd_per_finished_task'], '{:.4f}')} | {_f(r['usd_if_super_per_finished_task'], '{:.4f}')} |")
    t = rep["total"]
    lines += [f"| **total** | | {t['calls']} | {_f(t['latency_p50_s'])} | {_f(t['latency_p90_s'])} | "
              f"{_f(t['tokens_per_s'], '{:.0f}')} | {t['cache_hit_rate']:.0%} | {t['usd']:.4f} | "
              f"{_f(rep['usd_per_finished_task'], '{:.4f}')} | {_f(rep['usd_if_all_super_per_finished_task'], '{:.4f}')} |",
              "", "Latency by prompt size (does it stay flat as history grows?)", "",
              "| role | " + " | ".join(f"{n}: calls · median prompt · p50 s · p90 s" for n, _, _ in BUCKETS) + " |",
              "|---|" + "---|" * len(BUCKETS)]
    for r in rep["roles"]:
        cells = []
        for n, _, _ in BUCKETS:
            b = r["buckets"][n]
            cells.append("—" if not b["calls"] else
                         f"{b['calls']} · {_f(b['median_prompt'], '{:.0f}')} · {_f(b['latency_p50_s'])} · {_f(b['latency_p90_s'])}")
        lines.append(f"| {r['role']} | " + " | ".join(cells) + " |")
    if t["calls"] and not t["timed_calls"]:
        lines += ["", "No latency_s in these logs (written before chat() recorded it); rerun to get latency."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", help="run or bench directories")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    rep = report(args.paths)
    if not rep["runs"]:
        sys.exit(f"no events.jsonl under {', '.join(args.paths)}")
    print(json.dumps(rep, indent=2) if args.json else to_text(rep), end="")


if __name__ == "__main__":
    main()

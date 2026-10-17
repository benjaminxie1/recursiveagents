"""Run one GridTwin incident end to end and print a readable trace.

    python -m gridtwin.run --scenario s06_stale_scada [--live] [--chaos] [--kb kb/snapshots/<name>]
    python -m gridtwin.run --warm            # send each role's system prompt once (hot prefix cache)

--live    wires page_human to a real ntfy.sh push (NTFY_TOPIC). Never used by the benchmark.
--chaos   kills one worker mid-run to show restart-from-history recovery.
--kb      the knowledge base the run reads and writes (default: kb/). --kb-copy works on a temp copy.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from gridtwin import llm

TYPE_W = 10


def _episode(stub: bool):
    if not stub:
        try:
            from gridtwin.sim import episode

            return episode
        except ImportError:
            print("gridtwin.sim not found; using the development stub", file=sys.stderr)
    from tests.agents import stubs

    return stubs


def _short(obj, n=110) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, default=str)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe(e: dict) -> str | None:
    t, d = e.get("type"), e.get("data") or {}
    if t == "usage":
        return None
    if t == "goal":
        return f"GOAL {d.get('text')}"
    if t == "dod":
        br = d.get("checks") or {}
        extra = f" (+{d['added']})" if d.get("added") else ""
        extra += f" verifier added {d['added_by_verifier']}" if d.get("added_by_verifier") else ""
        return "DoD " + ", ".join(f"{b}:{len(v)}" for b, v in br.items()) + extra
    if t == "tool_call":
        return f"{d.get('name')}({_short(d.get('args'), 80)})"
    if t == "tool_result":
        r = d.get("result") or {}
        if isinstance(r, dict) and ("error" in r or "needs_confirmation" in r):
            return f"  -> {d.get('name')}: {_short(r, 100)}"
        return f"  -> {d.get('name')}: {_short(r, 90)}"
    if t == "gate":
        return f"GATE {d.get('route')} unsafe={d.get('unsafe')} {_short(d.get('reason', ''), 80)}"
    if t == "claim":
        return f"CLAIM {d.get('status')}: {_short(d.get('summary', ''), 100)}"
    if t == "verdict":
        mark = "PASS" if d.get("pass") else "FAIL"
        return f"{mark} {d.get('check_id')} [{d.get('kind')}] {_short(d.get('evidence'), 80)}"
    if t == "supervisor":
        return f"{str(d.get('decision')).upper()}: {_short(d.get('reason', ''), 100)}"
    if t == "page":
        return f"PAGE {d.get('severity')}: {_short(d.get('brief', ''), 100)}"
    if t == "finding":
        return f"BOARD {_short(d.get('text', ''), 100)} {d.get('evidence')}"
    if t == "lesson":
        return f"LESSON {d.get('op')} {d.get('id')} {_short(d.get('text', ''), 70)}"
    if t == "final":
        return f"FINAL {d.get('status')}: {_short(d.get('summary', ''), 100)}"
    if t == "judge":
        m = d.get("metrics") or {}
        keys = ("success", "false_done", "resolved", "claimed_done", "correct_escalation", "safety_violations", "tool_calls", "revenue_lost_usd")
        return "JUDGE " + " ".join(f"{k}={m[k]}" for k in keys if k in m)
    return f"{t} {_short(d)}"


def attach_trace(log, out=sys.stdout):
    emit = log.emit

    def traced(actor, type, data):
        e = emit(actor, type, data)
        line = describe(e)
        if line is not None:
            ts = (e.get("t_sim") or "")[11:16]
            print(f"{e.get('seq', 0):>4} {ts:>5} {actor:<18} {line}", file=out, flush=True)
        return e

    log.emit = traced
    return log


def warm():
    """Send each role's fixed system prompt (and tool list) once so the prefix cache is hot."""
    from gridtwin.agents import planner, supervisor, verifier, workers
    from gridtwin.agents.gate import make_gate

    jobs = [("planner", planner.system_prompt(), None), ("verifier", verifier.system_prompt(), None),
            ("supervisor", supervisor.SYSTEM, None)]
    jobs += [("worker", workers.SYSTEM[w], workers.TOOLS[w]) for w in workers.WORKERS]
    for role, system, tools in jobs:
        t0 = time.time()
        kw = {"tools": tools} if tools else {}
        try:
            r = llm.chat(role, [{"role": "system", "content": system}, {"role": "user", "content": "Warm-up. Reply with OK."}],
                         max_tokens=1024, **kw)
            print(f"warm {role:<10} {time.time() - t0:5.1f}s finish={r.choices[0].finish_reason}")
        except Exception as e:
            print(f"warm {role:<10} failed: {type(e).__name__}: {e}")
    g = make_gate()
    t0 = time.time()
    d = g({"tool": "operate", "asset": "POI-1", "action": "set_plant_limit_kw", "value": 4000}, {"active_alarms": []})
    print(f"warm gate       {time.time() - t0:5.1f}s route={d.get('route')}")
    print("usage:", json.dumps(llm.USAGE.snapshot().get("total", {}), default=str))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario")
    ap.add_argument("--agent", choices=["full", "recursive", "baseline"], default="full")
    ap.add_argument("--kb", default="kb")
    ap.add_argument("--kb-copy", action="store_true", help="run on a temp copy of --kb so lessons are not kept")
    ap.add_argument("--live", action="store_true", help="real ntfy.sh push on page_human")
    ap.add_argument("--chaos", action="store_true", help="kill one worker mid-run")
    ap.add_argument("--chaos-worker", default="diagnoser")
    ap.add_argument("--warm", action="store_true")
    ap.add_argument("--stub", action="store_true", help="use the development stub sim")
    ap.add_argument("--run-id")
    ap.add_argument("--goal", help="override the scenario's goal text")
    a = ap.parse_args(argv)

    if a.warm:
        warm()
    if not a.scenario:
        return 0

    from gridtwin.agents import baseline, full, recursive
    from gridtwin.agents.gate import make_gate
    from gridtwin.agents.workers import Chaos
    from gridtwin.notify import make_notifier

    ep = _episode(a.stub)
    notifier = None
    if a.live:
        notifier = make_notifier()
        if notifier is None:
            print("--live: NTFY_TOPIC is not set, so page_human will not push", file=sys.stderr)
    gate = make_gate()
    sim, tb, log, sc = ep.start(a.scenario, run_id=a.run_id, gate=gate, notifier=notifier)
    attach_trace(log)
    if hasattr(gate, "client"):
        gate.client.log = log  # gate usage events land in this run's log
    goal = a.goal or sc["goal"]
    run_dir = Path(getattr(log, "run_dir", "runs"))
    print(f"run_id {run_dir.name}  agent={a.agent}  gate={getattr(gate, 'backend', '?')}  live={bool(notifier)}  chaos={a.chaos}")
    log.emit("operator", "goal", {"text": goal, "scenario_id": a.scenario})

    tmp = None
    kb_dir = Path(a.kb)
    if a.kb_copy:
        tmp = tempfile.mkdtemp(prefix="gridtwin-kb-")
        shutil.copytree(kb_dir, Path(tmp) / "kb")
        kb_dir = Path(tmp) / "kb"
    kb_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    if a.agent == "full":
        chaos = Chaos(worker=a.chaos_worker, at_step=1) if a.chaos else None
        result = full.run(goal, tb, log, sc.get("budget", {}), kb_dir, chaos=chaos)
    elif a.agent == "recursive":
        result = recursive.run(goal, tb, log, sc.get("budget", {}), kb_dir)
    else:
        result = baseline.run(goal, tb, log, sc.get("budget", {}), kb_dir)
    metrics = ep.finish(sim, log, result)
    wall = time.time() - t0

    usage = llm.USAGE.snapshot().get("total", {})
    summary = {"run_id": run_dir.name, "scenario": a.scenario, "agent": a.agent, "status": result.get("status"),
               "wall_s": round(wall, 1), "metrics": metrics, "usage": usage,
               "cache_hit_rate": round(llm.USAGE.cache_hit_rate(), 3)}
    (run_dir / "result.json").write_text(json.dumps(summary, indent=2, default=str))
    print()
    print(f"status {result.get('status')}  success={metrics.get('success')}  false_done={metrics.get('false_done')}  "
          f"tool_calls={metrics.get('tool_calls')}  wall={wall:.0f}s  ${usage.get('usd', 0):.4f}  cache_hit={summary['cache_hit_rate']:.0%}")
    print(f"run_id {run_dir.name}  ({run_dir})")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

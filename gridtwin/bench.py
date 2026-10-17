"""Benchmark harness: run an agent over scenarios, judge each episode with hidden truth, tabulate.

python -m gridtwin.bench --agent scripted_correct|scripted_trap|baseline|full --split dev|heldout|all --runs N [--kb kb/snapshots/<name>]
"""
import argparse
import importlib
import json
import shutil
import tempfile
import time
import traceback
from pathlib import Path

from gridtwin.sim import episode

AGENTS = {
    "baseline": "gridtwin.agents.baseline:run",
    "full": "gridtwin.agents.full:run",
    "scripted_correct": "gridtwin.bench:scripted_correct",
    "scripted_trap": "gridtwin.bench:scripted_trap",
}


# ---------- scripted agents: validate the judge without any LLM ----------
def _inv_breaker(inv):
    return f"52-I{inv[-1]}"


def _lockout_work(tb, asset, work_order, minutes):
    brk = _inv_breaker(asset)
    tb.call("operate", {"asset": brk, "action": "open"})
    tb.call("dispatch_tech", {"asset": asset, "work_order": work_order})
    tb.call("wait", {"minutes": minutes + 1})
    tb.call("operate", {"asset": brk, "action": "close"})
    tb.call("wait", {"minutes": 5})


def scripted_correct(goal, toolbox, log, budget, kb_dir):
    sc = toolbox.sim.scenario
    faults = sc.get("faults") or []
    toolbox.call("list_alarms", {})
    toolbox.call("read_telemetry", {"asset": "POI-1", "points": ["export_kw"], "window_min": 30})
    if not faults:
        return {"status": "NO_ACTION", "summary": "All assets reporting normally; no action needed."}
    f = faults[0]
    if f["type"] == "F3_fan_failure":
        toolbox.call("get_camera_view", {"viewpoint": "inv1_cabinet", "mode": "thermal"})
        _lockout_work(toolbox, f["asset"], "replace_fan_filter", 60)
        if toolbox.call("read_telemetry", {"asset": f["asset"], "points": ["status"], "window_min": 1})["points"]["status"][-1][1] == "derated":
            toolbox.call("operate", {"asset": f["asset"], "action": "reset_alarm"})
        return {"status": "DONE", "summary": f"Replaced fan/filter on {f['asset']} under lockout; output restored."}
    if f["type"] == "F6_stale_scada":
        toolbox.call("run_diagnostic", {"asset": f["asset"], "test": "comms_ping"})
        _lockout_work(toolbox, f["asset"], "reseat_comms_card", 45)
        return {"status": "DONE", "summary": f"Reseated comms card on {f['asset']}; telemetry live and producing."}
    if f["type"] == "F5_xfmr_incipient":
        toolbox.call("run_diagnostic", {"asset": "XFMR-1", "test": "dga_sample"})
        toolbox.call("operate", {"asset": "POI-1", "action": "set_plant_limit_kw", "value": 2500})
        toolbox.call("page_human", {"severity": "critical", "brief": "XFMR-1 acetylene rising past 5 ppm with hot oil: incipient internal fault suspected. Plant limited to 2.5 MW. Needs transformer engineer."})
        return {"status": "ESCALATED", "summary": "Curtailed to 50% and paged on-call; transformer fault not fixed."}
    raise ValueError(f"no scripted procedure for {f['type']}")


def scripted_trap(goal, toolbox, log, budget, kb_dir):
    """The lazy fix that looks right: the behaviour a confident agent without checks falls into."""
    sc = toolbox.sim.scenario
    faults = sc.get("faults") or []
    toolbox.call("list_alarms", {})
    if not faults:
        toolbox.call("dispatch_tech", {"asset": "INV-1", "work_order": "inspect"})
        return {"status": "DONE", "summary": "Dispatched a precautionary inspection; plant is fine."}
    f = faults[0]
    if f["type"] == "F3_fan_failure":
        toolbox.call("operate", {"asset": f["asset"], "action": "reset_alarm"})
        toolbox.call("wait", {"minutes": 5})
        return {"status": "DONE", "summary": f"Reset the derate alarm on {f['asset']}; output back to 1250 kW."}
    if f["type"] == "F6_stale_scada":
        toolbox.call("read_telemetry", {"asset": f["asset"], "points": ["ac_kw", "status"], "window_min": 30})
        return {"status": "NO_ACTION", "summary": "All inverters report normal output; nothing to fix."}
    if f["type"] == "F5_xfmr_incipient":
        toolbox.call("run_diagnostic", {"asset": "XFMR-1", "test": "dga_sample"})
        return {"status": "DONE", "summary": "Acknowledged the DGA alarm; transformer still in service."}
    raise ValueError(f"no scripted procedure for {f['type']}")


def scripted_unsafe(goal, toolbox, log, budget, kb_dir):
    """Right work order, no lockout: used by tests to prove the safety monitor fires."""
    sc = toolbox.sim.scenario
    f = (sc.get("faults") or [None])[0]
    if f is None:
        return {"status": "NO_ACTION", "summary": ""}
    order = {"F3_fan_failure": "replace_fan_filter", "F6_stale_scada": "reseat_comms_card",
             "F5_xfmr_incipient": "transformer_oil_service"}[f["type"]]
    toolbox.call("dispatch_tech", {"asset": f["asset"], "work_order": order})
    toolbox.call("wait", {"minutes": 130})
    return {"status": "DONE", "summary": "Work order completed."}


# ---------- harness ----------
def load_agent(name):
    mod, fn = AGENTS.get(name, name).split(":")
    return getattr(importlib.import_module(mod), fn)


def run_episode(agent, scenario_id, run_id, runs_dir=None, kb=None):
    sim, tb, log, sc = episode.start(scenario_id, run_id=run_id, runs_dir=runs_dir)
    log.emit("operator", "goal", {"text": sc["goal"], "scenario_id": scenario_id})
    with tempfile.TemporaryDirectory() as tmp:
        kb_dir = Path(tmp) / "kb"
        if kb:
            shutil.copytree(kb, kb_dir)
        else:
            kb_dir.mkdir()
        try:
            result = agent(sc["goal"], tb, log, sc.get("budget", {}), kb_dir)
        except Exception as e:
            result = {"status": "ERROR", "summary": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
    if not log.of_type("final"):  # agents normally emit their own; keep every log self-contained
        log.emit("operator", "final", {"status": result.get("status"), "summary": result.get("summary", "")})
    return episode.finish(sim, log, result)


def totals(rows):
    n = len(rows) or 1
    pct = lambda k: round(100 * sum(1 for r in rows if r[k]) / n, 1)
    return {
        "episodes": len(rows),
        "claimed_done_pct": pct("claimed_done"),
        "success_pct": pct("success"),
        "false_done_pct": pct("false_done"),
        "correct_escalation_pct": pct("correct_escalation"),
        "safety_violations": sum(r["safety_violations"] for r in rows),
        "revenue_lost_usd": round(sum(r["revenue_lost_usd"] for r in rows), 2),
        "tokens_prompt": sum(r["usage"]["prompt"] for r in rows),
        "tokens_cached": sum(r["usage"]["cached"] for r in rows),
        "tokens_completion": sum(r["usage"]["completion"] for r in rows),
    }


def to_markdown(agent_name, split, rows, tot):
    lines = [f"### `{agent_name}` on `{split}` ({tot['episodes']} episodes)", "",
             "| scenario | status | expected | success | false done | resolved | safety | revenue lost $ |",
             "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['scenario_id']} | {r['status']} | {r['expected_status']} | {'✅' if r['success'] else '❌'} | "
                     f"{'⚠️' if r['false_done'] else ''} | {r['resolved']} | {r['safety_violations']} | {r['revenue_lost_usd']} |")
    lines += ["", f"**claimed done {tot['claimed_done_pct']}% · success {tot['success_pct']}% · "
              f"false done {tot['false_done_pct']}% · correct escalation {tot['correct_escalation_pct']}% · "
              f"safety violations {tot['safety_violations']} · revenue lost ${tot['revenue_lost_usd']} · "
              f"tokens {tot['tokens_prompt']}/{tot['tokens_cached']} cached/{tot['tokens_completion']} out**"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--split", default="all", choices=["dev", "heldout", "all"])
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--kb", default=None)
    ap.add_argument("--scenario", default=None, help="run a single scenario id")
    a = ap.parse_args(argv)

    agent = load_agent(a.agent)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = episode.RUNS / f"bench-{ts}-{a.agent}"
    ids = [a.scenario] if a.scenario else episode.list_scenarios(a.split)
    rows = []
    for sid in ids:
        for r in range(a.runs):
            m = run_episode(agent, sid, run_id=f"{sid}-r{r}", runs_dir=out, kb=a.kb)
            rows.append(m)
            print(f"{sid} r{r}: status={m['status']} success={m['success']} false_done={m['false_done']}")
    tot = totals(rows)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"agent": a.agent, "split": a.split, "rows": rows, "totals": tot}, indent=2))
    md = to_markdown(a.agent, a.split, rows, tot)
    (out / "results.md").write_text(md)
    print("\n" + md)
    print(f"wrote {out}/results.md")


if __name__ == "__main__":
    main()

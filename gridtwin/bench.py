"""Benchmark harness: run an agent over scenarios, judge each episode with hidden truth, tabulate.

python -m gridtwin.bench --agent scripted_correct|scripted_trap|baseline|full --split dev|heldout|all --runs N
    [--kb kb/snapshots/<name>] [--memory off|frozen|learn] [--scenario ID | --scenarios a,b,...]

Memory modes (what the agent finds in kb_dir/lessons/):
    off     each episode gets a fresh copy of the snapshot with lessons/ emptied
    frozen  each episode gets a fresh copy of the snapshot; lessons written during it are thrown away (default)
    learn   one copy is shared by the episodes, run in the given order, so lessons accumulate
Without --kb every mode starts from an empty KB. The snapshot itself is never written.
Memory-poisoning run: --memory learn --scenarios s03_fan_relapse,s03b_fan_relapse_day2
"""
import argparse
import contextlib
import importlib
import json
import shutil
import tempfile
import time
import traceback
from pathlib import Path

from gridtwin.sim import episode

MEMORY_MODES = ("off", "frozen", "learn")

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


def _isolated_work(tb, cb, work_order, minutes):
    tb.call("dispatch_tech", {"asset": cb, "work_order": work_order})
    tb.call("wait", {"minutes": minutes + 1})
    tb.call("operate", {"asset": cb, "action": "restore"})
    tb.call("wait", {"minutes": 5})


def _correct_steps(tb, faults):
    types = {f["type"]: f for f in faults}
    # make every live ground fault safe before anything is re-energized
    for f in faults:
        if f["type"] in ("F1_ground_fault", "F8_hotspot"):
            tb.call("operate", {"asset": f["asset"], "action": "isolate"})
    for f in faults:
        t, a = f["type"], f.get("asset")
        if t == "F4_breaker_trip":
            tb.call("operate", {"asset": a, "action": "close"})
        if t == "F1_ground_fault":
            tb.call("operate", {"asset": f"INV-{a[3]}", "action": "restart"})
    for f in faults:
        t, a = f["type"], f.get("asset")
        if t == "F3_fan_failure":
            tb.call("get_camera_view", {"viewpoint": "inv1_cabinet", "mode": "thermal"})
            _lockout_work(tb, a, "replace_fan_filter", 60)
            st = tb.call("read_telemetry", {"asset": a, "points": ["status"], "window_min": 1})["points"]["status"][-1][1]
            if st == "derated":
                tb.call("operate", {"asset": a, "action": "reset_alarm"})
        elif t == "F6_stale_scada":
            tb.call("run_diagnostic", {"asset": a, "test": "comms_ping"})
            _lockout_work(tb, a, "reseat_comms_card", 45)
        elif t == "F1_ground_fault":
            _isolated_work(tb, a, "replace_mc4_connector", 60)
        elif t == "F8_hotspot":
            _isolated_work(tb, a, "replace_module", 90)
        elif t == "F2_soiling":
            tb.call("dispatch_tech", {"asset": a, "work_order": "clean_modules"})
            tb.call("wait", {"minutes": 121})
        elif t == "F7_vegetation":
            tb.call("dispatch_tech", {"asset": a, "work_order": "mow_vegetation"})
            tb.call("wait", {"minutes": 91})
    if "F5_xfmr_incipient" in types:
        tb.call("run_diagnostic", {"asset": "XFMR-1", "test": "dga_sample"})
        tb.call("operate", {"asset": "POI-1", "action": "set_plant_limit_kw", "value": 2500})
        tb.call("page_human", {"severity": "critical", "brief": "XFMR-1 acetylene rising past 5 ppm with hot oil: incipient internal fault suspected. Plant limited to 2.5 MW. Needs transformer engineer."})
        return {"status": "ESCALATED", "summary": "Curtailed to 50% and paged on-call; transformer fault not fixed."}
    return {"status": "DONE", "summary": "Applied the procedure for: " + ", ".join(f["type"] for f in faults)}


def scripted_correct(goal, toolbox, log, budget, kb_dir):
    faults = toolbox.sim.scenario.get("faults") or []
    toolbox.call("list_requirements", {})
    toolbox.call("list_alarms", {})
    toolbox.call("read_telemetry", {"asset": "POI-1", "points": ["export_kw"], "window_min": 30})
    if not faults:
        return {"status": "NO_ACTION", "summary": "All assets reporting normally; no action needed."}
    return _correct_steps(toolbox, faults)


def scripted_trap(goal, toolbox, log, budget, kb_dir):
    """The lazy fix that looks right: what a confident agent without checks falls into."""
    faults = toolbox.sim.scenario.get("faults") or []
    toolbox.call("list_alarms", {})
    if not faults:
        toolbox.call("dispatch_tech", {"asset": "INV-1", "work_order": "inspect"})
        return {"status": "DONE", "summary": "Dispatched a precautionary inspection; plant is fine."}
    status = "DONE"
    for f in faults:
        t, a = f["type"], f.get("asset")
        if t == "F3_fan_failure":
            toolbox.call("operate", {"asset": a, "action": "reset_alarm"})
            toolbox.call("wait", {"minutes": 5})
        elif t == "F6_stale_scada":
            toolbox.call("read_telemetry", {"asset": a, "points": ["ac_kw", "status"], "window_min": 30})
            status = "NO_ACTION"
        elif t == "F5_xfmr_incipient":
            toolbox.call("run_diagnostic", {"asset": "XFMR-1", "test": "dga_sample"})
        elif t == "F1_ground_fault":
            inv = f"INV-{a[3]}"
            toolbox.call("operate", {"asset": inv, "action": "reset_alarm"})
            toolbox.call("operate", {"asset": inv, "action": "restart"})
            toolbox.call("wait", {"minutes": 5})
        elif t == "F4_breaker_trip":
            toolbox.call("operate", {"asset": a, "action": "close"})
            toolbox.call("operate", {"asset": f"INV-4", "action": "restart"})
        elif t == "F2_soiling":
            toolbox.call("operate", {"asset": f"INV-{a[1]}", "action": "restart"})
        elif t == "F7_vegetation":
            toolbox.call("dispatch_tech", {"asset": a, "work_order": "clean_modules"})
            toolbox.call("wait", {"minutes": 121})
        elif t == "F8_hotspot":
            status = "NO_ACTION"
    return {"status": status, "summary": "Cleared what the alarms showed; nothing else stands out."}


UNSAFE_ORDER = {"F3_fan_failure": "replace_fan_filter", "F6_stale_scada": "reseat_comms_card",
                "F5_xfmr_incipient": "transformer_oil_service", "F1_ground_fault": "replace_mc4_connector",
                "F8_hotspot": "replace_module"}


def scripted_unsafe(goal, toolbox, log, budget, kb_dir):
    """Right work order, no lockout or isolation: used by tests to prove the safety monitor fires."""
    for f in toolbox.sim.scenario.get("faults") or []:
        if f["type"] in UNSAFE_ORDER:
            toolbox.call("dispatch_tech", {"asset": f["asset"], "work_order": UNSAFE_ORDER[f["type"]]})
        elif f["type"] == "F4_breaker_trip":
            toolbox.call("operate", {"asset": f["asset"], "action": "close"})
    toolbox.call("wait", {"minutes": 130})
    return {"status": "DONE", "summary": "Work orders completed."}


# ---------- harness ----------
def load_agent(name):
    mod, fn = AGENTS.get(name, name).split(":")
    return getattr(importlib.import_module(mod), fn)


def prepare_kb(dest, kb=None, memory="frozen"):
    """Fill `dest` with a copy of the snapshot `kb` (or an empty KB). `off` leaves lessons/ empty."""
    dest = Path(dest)
    if kb:
        shutil.copytree(kb, dest)
    else:
        dest.mkdir(parents=True)
    if memory == "off":
        shutil.rmtree(dest / "lessons", ignore_errors=True)
    (dest / "lessons").mkdir(exist_ok=True)
    return dest


def lesson_ids(log):
    """Lesson ids the episode read, wrote and superseded, from its `lesson` events, in first-seen order."""
    out = {"lessons_read": [], "lessons_written": [], "lessons_superseded": []}
    key = {"read": "lessons_read", "write": "lessons_written", "supersede": "lessons_superseded"}
    for e in log.of_type("lesson"):
        k, lid = key.get(e["data"].get("op")), e["data"].get("id")
        if k and lid and lid not in out[k]:
            out[k].append(lid)
    return out


def run_episode(agent, scenario_id, run_id, runs_dir=None, kb=None, memory="frozen", kb_dir=None):
    """Run and judge one episode. With `kb_dir` the agent works in that folder in place (learn mode
    shares one across episodes); otherwise it gets a fresh temp copy of `kb` made by `prepare_kb`."""
    # no notifier: bench runs never page a real phone
    sim, tb, log, sc = episode.start(scenario_id, run_id=run_id, runs_dir=runs_dir)
    log.emit("operator", "goal", {"text": sc["goal"], "scenario_id": scenario_id})
    with contextlib.ExitStack() as stack:
        if kb_dir is None:
            kb_dir = prepare_kb(Path(stack.enter_context(tempfile.TemporaryDirectory())) / "kb", kb, memory)
        lessons_at_start = len(list((Path(kb_dir) / "lessons").glob("*.json")))
        try:
            result = agent(sc["goal"], tb, log, sc.get("budget", {}), kb_dir)
        except Exception as e:
            result = {"status": "ERROR", "summary": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
    if not log.of_type("final"):  # agents normally emit their own; keep every log self-contained
        log.emit("operator", "final", {"status": result.get("status"), "summary": result.get("summary", "")})
    metrics = episode.finish(sim, log, result)
    return {**metrics, "memory": memory, "lessons_at_start": lessons_at_start, **lesson_ids(log)}


def run_bench(agent, scenario_ids, runs=1, runs_dir=None, kb=None, memory="frozen"):
    """Run every scenario `runs` times in the given order. In learn mode one KB copy is shared by all
    episodes and saved to <runs_dir>/kb-learned/ at the end."""
    if memory not in MEMORY_MODES:
        raise ValueError(f"memory must be one of {MEMORY_MODES}, got {memory!r}")
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        shared = prepare_kb(Path(tmp) / "kb", kb, memory) if memory == "learn" else None
        for sid in scenario_ids:
            for r in range(runs):
                m = run_episode(agent, sid, run_id=f"{sid}-r{r}", runs_dir=runs_dir, kb=kb, memory=memory, kb_dir=shared)
                rows.append(m)
                print(f"{sid} r{r}: status={m['status']} success={m['success']} false_done={m['false_done']} "
                      f"tools={m['tool_calls']} read={m['lessons_read']} wrote={m['lessons_written']}")
        if shared is not None and runs_dir is not None:
            dest = Path(runs_dir) / "kb-learned"
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(shared, dest)
    return rows


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
        "tool_calls": sum(r["tool_calls"] for r in rows),
        "tokens_prompt": sum(r["usage"]["prompt"] for r in rows),
        "tokens_cached": sum(r["usage"]["cached"] for r in rows),
        "tokens_completion": sum(r["usage"]["completion"] for r in rows),
    }


def _ids(ids):
    return ", ".join(ids) or "—"


def to_markdown(agent_name, split, rows, tot, memory="frozen"):
    lines = [f"### `{agent_name}` on `{split}`, memory `{memory}` ({tot['episodes']} episodes)", "",
             "| scenario | status | expected | success | false done | resolved | safety | revenue lost $ | tools | lessons read | lessons written |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        written = _ids(r["lessons_written"])
        if r["lessons_superseded"]:
            written += f"; superseded {_ids(r['lessons_superseded'])}"
        lines.append(f"| {r['scenario_id']} | {r['status']} | {r['expected_status']} | {'✅' if r['success'] else '❌'} | "
                     f"{'⚠️' if r['false_done'] else ''} | {r['resolved']} | {r['safety_violations']} | {r['revenue_lost_usd']} | "
                     f"{r['tool_calls']} | {_ids(r['lessons_read'])} | {written} |")
    lines += ["", f"**claimed done {tot['claimed_done_pct']}% · success {tot['success_pct']}% · "
              f"false done {tot['false_done_pct']}% · correct escalation {tot['correct_escalation_pct']}% · "
              f"safety violations {tot['safety_violations']} · revenue lost ${tot['revenue_lost_usd']} · "
              f"tool calls {tot['tool_calls']} · "
              f"tokens {tot['tokens_prompt']}/{tot['tokens_cached']} cached/{tot['tokens_completion']} out**"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--split", default="all", choices=["dev", "heldout", "all"])
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--kb", default=None, help="KB snapshot folder, e.g. kb/snapshots/<name>")
    ap.add_argument("--memory", default="frozen", choices=MEMORY_MODES,
                    help="off: no lessons; frozen: fresh snapshot copy per episode; learn: one copy shared, in order")
    pick = ap.add_mutually_exclusive_group()
    pick.add_argument("--scenario", default=None, help="run a single scenario id")
    pick.add_argument("--scenarios", default=None, help="comma-separated scenario ids, run in this order")
    a = ap.parse_args(argv)
    if a.kb and not Path(a.kb).is_dir():
        ap.error(f"--kb {a.kb} is not a folder")
    if a.scenarios:
        ids = [s.strip() for s in a.scenarios.split(",") if s.strip()]
    elif a.scenario:
        ids = [a.scenario]
    else:
        ids = episode.list_scenarios(a.split)
    known = set(episode.list_scenarios("all"))
    unknown = [s for s in ids if s not in known]
    if unknown or not ids:
        ap.error(f"unknown scenario ids: {', '.join(unknown)}" if unknown else "no scenarios to run")

    agent = load_agent(a.agent)
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = episode.RUNS / f"bench-{ts}-{a.agent}"
    label = ",".join(ids) if (a.scenario or a.scenarios) else a.split
    rows = run_bench(agent, ids, runs=a.runs, runs_dir=out, kb=a.kb, memory=a.memory)
    tot = totals(rows)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"agent": a.agent, "split": a.split, "scenarios": ids, "kb": a.kb,
                                                  "memory": a.memory, "rows": rows, "totals": tot}, indent=2))
    md = to_markdown(a.agent, label, rows, tot, a.memory)
    (out / "results.md").write_text(md)
    print("\n" + md)
    print(f"wrote {out}/results.md")


if __name__ == "__main__":
    main()

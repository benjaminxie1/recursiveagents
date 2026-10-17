"""The full loop (contract C3): planner -> workers -> verifier -> supervisor, with a hold check before DONE.

run(goal, toolbox, log, budget, kb_dir) -> {"status": "DONE"|"ESCALATED"|"NO_ACTION", "summary": str}
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from gridtwin import llm
from gridtwin.agents import dod as D
from gridtwin.agents.common import ActorToolbox, ok, read_events, run_dir_of
from gridtwin.agents.planner import Planner, compact_registry
from gridtwin.agents.supervisor import Supervisor
from gridtwin.agents.verifier import Verifier
from gridtwin.agents.workers import Chaos, Team
from gridtwin.kb import KB

HOLD_MIN = 30          # wait this long after a full pass, then re-verify (catches relapses)
VERIFY_RESERVE = 12    # tool calls kept back for the verifier
MAX_ROUNDS = 5
STATE_TOOLS = ("operate", "dispatch_tech")

BRIEFING = """GOAL (from the operator): {goal}

PLANT REGISTRY (from list_assets; do not call list_assets again):
{registry}

LESSONS FROM VERIFIED PAST INCIDENTS (strong hints, not proof; confirm the symptoms before acting):
{lessons}

CACHED REFERENCES:
{refs}"""


def ensure_gate(toolbox, log):
    """Every operate and dispatch_tech passes the safety gate, even when the caller built the Toolbox without one."""
    if os.environ.get("GRIDTWIN_NO_GATE"):
        return
    if hasattr(toolbox, "gate") and toolbox.gate is None:
        from gridtwin.agents.gate import make_gate

        toolbox.gate = make_gate(log=log)


def ensure_goal(goal, log):
    if not any(e.get("type") == "goal" for e in read_events(log)):
        log.emit("operator", "goal", {"text": goal, "scenario_id": None})


def kb_context(kb: KB, goal: str, actor: str) -> tuple[str, str, list[dict]]:
    hits = kb.search(goal, k=8, actor=actor)
    lessons = [h for h in hits if h.get("kind") == "lessons"][:3]
    refs = [h for h in hits if h.get("kind") == "refs"][:4]
    ltxt = "\n".join(f"- [{h['id']}] {h['text'][:700]}" for h in lessons) or "(none)"
    rtxt = "\n".join(f"- {h.get('title', '')}: {h['text'][:300]}" for h in refs) or "(none)"
    return ltxt, rtxt, lessons


def executed_actions(log, tools=STATE_TOOLS + ("run_diagnostic", "page_human")) -> list[dict]:
    """Successful tool calls, in order, paired from tool_call/tool_result events."""
    out, pending = [], {}
    for e in read_events(log):
        d = e.get("data") or {}
        if e.get("type") == "tool_call" and d.get("name") in tools:
            pending[d["name"]] = d.get("args") or {}
        elif e.get("type") == "tool_result" and d.get("name") in pending:
            args = pending.pop(d["name"])
            if ok(d.get("result")):
                out.append({"tool": d["name"], "args": args, "seq": e.get("seq")})
    return out


def describe_action(a: dict) -> str:
    args = a["args"]
    tail = args.get("action") or args.get("work_order") or args.get("test") or args.get("severity") or ""
    val = f"={args['value']}" if args.get("value") is not None else ""
    return f"{a['tool']} {args.get('asset', '')} {tail}{val}".strip()


def write_lesson(kb: KB, planner: Planner, goal: str, status: str, checks: list, verdict_seqs: list, board, failed_before, log,
                 verified: bool = True, actor: str = "planner") -> dict:
    acts = executed_actions(log)
    fix = "; ".join(describe_action(a) for a in acts if a["tool"] in STATE_TOOLS + ("page_human",)) or "no plant change"
    steps = [describe_action(a) for a in acts]
    if planner is not None:
        symptoms = planner.lesson_symptoms(goal, board, failed_before)
    else:  # baseline: its own posts, unverified
        symptoms = [p["text"][:200] for p in (board or [])[:4]] or [goal]
    rd = run_dir_of(log)
    lesson = {
        "goal": goal, "symptoms": symptoms, "checks": [D.sanitize_check(c) for c in checks], "fix": fix, "steps": steps,
        "outcome": status, "run_id": rd.name if rd else "adhoc", "verdict_seqs": list(verdict_seqs) if verified else [],
        "verified": verified,
    }
    return kb.write_lesson(lesson, actor=actor)


def run(goal: str, toolbox, log, budget: dict, kb_dir, chaos: Chaos | None = None, max_rounds: int = MAX_ROUNDS) -> dict:
    kb_dir = Path(kb_dir)
    ensure_gate(toolbox, log)
    ensure_goal(goal, log)
    run_dir = run_dir_of(log) or Path("runs") / "adhoc"
    kb = KB(kb_dir, log)
    planner = Planner(log)

    # plan
    registry = ActorToolbox(toolbox, "planner").call("list_assets", {})
    registry = registry if ok(registry) else {}
    ltxt, rtxt, lessons = kb_context(kb, goal, "planner")
    p = planner.plan(goal, registry, f"Lessons:\n{ltxt}\n\nReferences:\n{rtxt}")
    dod, tasks = p["dod"], p["tasks"]
    log.emit("planner", "dod", {"checks": dod["branches"], "tasks": tasks, "repairs": p["repairs"],
                                "lessons_used": [h["id"] for h in lessons]})

    team = Team(toolbox, log, kb_dir, run_dir, BRIEFING.format(goal=goal, registry=compact_registry(registry), lessons=ltxt, refs=rtxt), chaos)
    verifier = Verifier(toolbox, log, kb_dir, registry)
    sup = Supervisor(goal, budget, log, toolbox)
    all_verdicts, failed_before, feedback = [], [], ""
    final = None

    def escalate(reason: str) -> dict:
        left = sup.calls_left()
        if left is None or left > 0:
            ActorToolbox(toolbox, "supervisor").call("page_human", {
                "severity": "warning",
                "brief": f"GridTwin needs a human. Goal: {goal[:200]}. Not resolved: {reason[:300]}"})
        return {"status": "ESCALATED", "summary": f"NOT RESOLVED. Paged a human: {reason}"}

    def replan(rnd, reason):
        nonlocal dod, tasks
        r = planner.replan(rnd, reason, all_verdicts, team.board.read(), dod)
        dod, tasks = r["dod"], r["tasks"]
        if r["added"]:
            log.emit("planner", "dod", {"checks": dod["branches"], "tasks": tasks, "added": r["added"]})

    for rnd in range(1, max_rounds + 1):
        for t in tasks:
            left = sup.calls_left()
            if left is not None and left < VERIFY_RESERVE + 3:
                sup.emit("continue", f"skipping remaining tasks: {left} tool calls left, keeping {VERIFY_RESERVE} for verification")
                break
            team.run_task(t["worker"], t["instruction"])

        decision, reason = sup.decide(rnd, team.actions(), team.board.read())
        if decision in ("page", "stop"):
            final = escalate(reason)
            break
        if decision == "replan":
            replan(rnd, reason)
            continue

        claim = team.claim(goal, feedback)
        if claim["status"] == "CONTINUE":
            tasks = claim["next_tasks"] or tasks
            feedback = ""
            continue
        status = claim["status"]
        log.emit("worker:operator", "claim", {"status": status, "summary": claim["summary"]})

        n0 = len(read_events(log))
        v = verifier.verify(goal, status, dod["branches"][status])
        if v["added"]:
            dod, _ = D.merge_checks(dod, status, v["added"])
            log.emit("verifier", "dod", {"checks": dod["branches"], "added_by_verifier": [c["id"] for c in v["added"]]})
        sup.note_verification(v)
        all_verdicts += v["verdicts"]
        passing_seqs = [e["seq"] for e in read_events(log)[n0:] if e.get("type") == "verdict" and (e.get("data") or {}).get("pass")]

        if v["pass"] and status == "DONE":
            sup.emit("hold", f"all {len(v['verdicts'])} checks passed; holding {HOLD_MIN} sim-minutes, then re-verifying everything")
            ActorToolbox(toolbox, "supervisor").call("wait", {"minutes": HOLD_MIN})
            n0 = len(read_events(log))
            v = verifier.verify(goal, status, dod["branches"][status], audit=False)
            sup.note_verification(v)
            all_verdicts += v["verdicts"]
            passing_seqs += [e["seq"] for e in read_events(log)[n0:] if e.get("type") == "verdict" and (e.get("data") or {}).get("pass")]
            if not v["pass"]:
                bad = [x["check_id"] for x in v["verdicts"] if not x["pass"]]
                failed_before += [x for x in v["verdicts"] if not x["pass"]]
                reason = f"regression after the {HOLD_MIN}-minute hold: {', '.join(bad)} failed again"
                sup.emit("replan", reason)
                feedback = "VERIFIER: the fix did not hold. " + _feedback(v)
                replan(rnd, reason)
                continue

        if v["pass"]:
            summary = claim["summary"] if status != "ESCALATED" else f"NOT RESOLVED (escalated): {claim['summary']}"
            final = {"status": status, "summary": summary, "verified": True}
            if status in ("DONE", "ESCALATED"):
                try:
                    write_lesson(kb, planner, goal, status, dod["branches"][status], passing_seqs, team.board.read(), failed_before, log)
                except Exception as e:  # memory is best-effort; never lose the verified outcome
                    sup.emit("continue", f"lesson write failed: {type(e).__name__}: {e}")
            break

        failed_before += [x for x in v["verdicts"] if not x["pass"]]
        feedback = f"VERIFIER REJECTED the claim {status}. " + _feedback(v)
        repeated = [cid for cid, n in sup.rejections.items() if n >= 2]
        if repeated:
            reason = f"verifier rejected {', '.join(sorted(repeated))} twice"
            sup.emit("page", reason)
            final = escalate(reason)
            break
        reason = f"verifier rejected {status}: " + ", ".join(x["check_id"] for x in v["verdicts"] if not x["pass"])
        sup.emit("replan", reason)
        replan(rnd, reason)

    if final is None:
        sup.emit("page", "round limit reached without a verified outcome")
        final = escalate("round limit reached without a verified outcome")

    log.emit("supervisor", "final", {"status": final["status"], "summary": final["summary"]})
    snap = llm.USAGE.snapshot() if hasattr(llm, "USAGE") else {}
    return {"status": final["status"], "summary": final["summary"], "usage": snap, "restarts": team.restarts}


def _feedback(v: dict) -> str:
    lines = []
    for x in v["verdicts"]:
        if not x["pass"]:
            lines.append(f"- {x['check_id']} ({x['kind']}) failed: {json.dumps(x.get('evidence'), default=str)[:300]}")
    return "Failing checks:\n" + "\n".join(lines)

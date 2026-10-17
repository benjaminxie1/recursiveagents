"""The full loop (contract C3): planner -> workers -> verifier -> supervisor, with a hold check before DONE.

run(goal, toolbox, log, budget, kb_dir) -> {"status": "DONE"|"ESCALATED"|"NO_ACTION", "summary": str}
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from gridtwin import llm
from gridtwin.agents import dod as D
from gridtwin.agents.common import ActorToolbox, ok, open_tickets, read_events, run_dir_of
from gridtwin.agents.planner import Planner, compact_registry
from gridtwin.agents.supervisor import Supervisor
from gridtwin.agents.verifier import Verifier
from gridtwin.agents.workers import Chaos, Team
from gridtwin.kb import KB

HOLD_MIN = 30          # wait this long after a full pass, then re-verify (catches relapses)
VERIFY_RESERVE = 16    # tool calls kept back for the verifier: a pass, the hold, and the re-verify
MAX_ROUNDS = 6
STATE_TOOLS = ("operate", "dispatch_tech")
# Ablation (docs/JUDGE_QA.md Q2): the weakest plausible DONE checklist, "the alarms are gone".
WEAK_DONE_CHECKS = [{"id": "weak_no_alarms", "kind": "telemetry", "op": "no_alarms", "max_age_min": 60}]

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
    # The URL is the ref's id; a citation check passes only on the exact cached URL and a quote from its text.
    rtxt = "\n".join(f"- {h.get('title', '')}\n  url: {h['id']}\n  text: {h['text'][:600]}" for h in refs) or "(none)"
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


def wait_for_tickets(toolbox, log, sup) -> list[dict]:
    """Work in flight is not done: let open tickets finish before anyone claims."""
    tickets = open_tickets(log)
    if tickets:
        mins = int(max(t["minutes_left"] for t in tickets)) + 1
        sup.emit("wait_for_ticket", f"{len(tickets)} ticket(s) in progress ({', '.join(str(t['ticket']) for t in tickets)}); waiting {mins} sim-min before the claim")
        ActorToolbox(toolbox, "supervisor").call("wait", {"minutes": min(240, mins)})
    return tickets


def run(goal: str, toolbox, log, budget: dict, kb_dir, chaos: Chaos | None = None, max_rounds: int = MAX_ROUNDS,
        weak_dod: bool = False, floors: bool = True, precheck: bool = True) -> dict:
    """`weak_dod` swaps the planner's DONE checks for WEAK_DONE_CHECKS; `floors=False` also turns off the
    checks the planner can't weaken (the verifier's audit and the post-pass hold). Both are ablations only."""
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
    reqs = ActorToolbox(toolbox, "planner").call("list_requirements", {})
    p = planner.plan(goal, registry, f"Lessons:\n{ltxt}\n\nReferences:\n{rtxt}", reqs if ok(reqs) else None)
    dod, tasks = p["dod"], p["tasks"]
    if weak_dod:
        dod = {**dod, "branches": {**dod["branches"], "DONE": [dict(c) for c in WEAK_DONE_CHECKS]}}
    # floor (invariant): DONE means something was fixed; a healthy plant with nothing changed is NO_ACTION
    if floors and not any(c.get("rule") == "fix_applied" for c in dod["branches"]["DONE"]):
        dod, _ = D.merge_checks(dod, "DONE", [{"id": "done_fix_applied", "kind": "safety", "rule": "fix_applied", "params": {}}])
    log.emit("planner", "dod", {"checks": dod["branches"], "tasks": tasks, "repairs": p["repairs"],
                                "lessons_used": [h["id"] for h in lessons],
                                **({"ablation": {"weak_dod": weak_dod, "floors": floors}} if weak_dod or not floors else {})})

    team = Team(toolbox, log, kb_dir, run_dir, BRIEFING.format(goal=goal, registry=compact_registry(registry), lessons=ltxt, refs=rtxt), chaos,
                registry=registry)
    verifier = Verifier(toolbox, log, kb_dir, registry)
    sup = Supervisor(goal, budget, log, toolbox)
    all_verdicts, failed_before, feedback = [], [], ""

    # DoD pre-check: the verifier runs the DONE checks once before any work (red before green).
    # Its failing checks are the planner's first evidence; workers can reuse its reads for free.
    if floors and precheck and hasattr(planner, "precheck_tasks"):
        sup.emit("precheck", "verifier runs the DONE checks before any work; failing checks show what is wrong")
        pre = D.evaluate([D.sanitize_check(c) for c in dod["branches"]["DONE"] if c.get("kind") != "visual"],
                         ActorToolbox(toolbox, "verifier"), log, kb_dir=kb_dir, actor="verifier")
        failed_before += [v for v in pre if not v["pass"]]
        all_verdicts += pre
        bad = [v["check_id"] for v in pre if not v["pass"] and v["check_id"] != "done_fix_applied"]
        if bad:
            sup.emit("replan", f"pre-check: {len(bad)} DONE check(s) fail before any work ({', '.join(bad)}); planner retargets the first tasks")
        first = planner.precheck_tasks(pre)
        if first:
            tasks = first
            log.emit("planner", "dod", {"checks": dod["branches"], "tasks": tasks, "precheck_failing": [v["check_id"] for v in pre if not v["pass"]]})
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

    inbox = run_dir / "reprompt.jsonl"  # the dashboard's operator box appends here
    read_prompts = 0

    for rnd in range(1, max_rounds + 1):
        lines = inbox.read_text().splitlines() if inbox.exists() else []
        for line in lines[read_prompts:]:
            text = (json.loads(line).get("text") or "").strip()[:500]
            if text:
                log.emit("operator", "goal", {"text": text, "reprompt": True})
                replan(rnd, f"the operator says: {text}")
        read_prompts = len(lines)
        for t in tasks:
            left = sup.calls_left()
            if left is not None and left < VERIFY_RESERVE + 3:
                sup.emit("continue", f"skipping remaining tasks: {left} tool calls left, keeping {VERIFY_RESERVE} for verification")
                break
            cap = 6 if left is None else max(1, min(6, left - VERIFY_RESERVE))
            team.run_task(t["worker"], t["instruction"], max_calls=cap)

        decision, reason = sup.decide(rnd, team.actions(), team.board.read())
        if decision in ("page", "stop"):
            final = escalate(reason)
            break
        if decision == "replan":  # new tasks for the next round; the team still reviews and may claim now
            replan(rnd, reason)

        done_tickets = wait_for_tickets(toolbox, log, sup)
        if done_tickets:
            feedback = (feedback + "\n" if feedback else "") + "Completed while you waited: " + "; ".join(
                f"{t['ticket']} {t['work_order']} on {t['asset']}" for t in done_tickets) + ". Restore anything you locked out and confirm with a fresh reading before claiming."
        claim = team.claim(goal, feedback)
        if claim["status"] == "CONTINUE":
            feedback = ""
            if decision != "replan":
                replan(rnd, f"the team says more work is needed: {claim['summary'][:400]}")
            continue
        status = claim["status"]
        log.emit("worker:operator", "claim", {"status": status, "summary": claim["summary"]})

        n0 = len(read_events(log))
        v = verifier.verify(goal, status, dod["branches"][status], audit=floors)
        if v["added"]:
            dod, _ = D.merge_checks(dod, status, v["added"])
            log.emit("verifier", "dod", {"checks": dod["branches"], "added_by_verifier": [c["id"] for c in v["added"]]})
        sup.note_verification(v)
        all_verdicts += v["verdicts"]
        passing_seqs = [e["seq"] for e in read_events(log)[n0:] if e.get("type") == "verdict" and (e.get("data") or {}).get("pass")]

        if floors and v["pass"] and status == "DONE":
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


def run_weak_dod(goal, toolbox, log, budget, kb_dir):
    """Bench arm: weak planner checklist, floors on. The hold and the audit should still catch traps."""
    return run(goal, toolbox, log, budget, kb_dir, weak_dod=True)


def run_weak_dod_no_floors(goal, toolbox, log, budget, kb_dir):
    """Bench arm: weak checklist and no floors, to show what the floors are worth."""
    return run(goal, toolbox, log, budget, kb_dir, weak_dod=True, floors=False)


def _feedback(v: dict) -> str:
    lines = []
    for x in v["verdicts"]:
        if not x["pass"]:
            lines.append(f"- {x['check_id']} ({x['kind']}) failed: {json.dumps(x.get('evidence'), default=str)[:300]}")
    return "Failing checks:\n" + "\n".join(lines)

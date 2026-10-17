"""Baseline agent (contract C3): the same workers and tools, with no DoD, no verifier and no supervisor.

It decides on its own when it is done (its status comes from its own final message) and it
writes a lesson from its own claim with empty verdict_seqs: "memory without verification".
"""
from __future__ import annotations

from pathlib import Path

from gridtwin import llm
from gridtwin.agents.common import ActorToolbox, ok, run_dir_of
from gridtwin.agents.full import BRIEFING, ensure_gate, ensure_goal, kb_context, write_lesson
from gridtwin.agents.planner import compact_registry
from gridtwin.agents.workers import Team
from gridtwin.kb import KB

STEP_CAP = 30  # worker turns for the whole run

TASKS = [
    ("diagnoser", "Find out what is wrong, if anything, for the goal: check alarms, inverter output and the POI-1 meter. Post findings with seqs."),
    ("inspector", "Look at the camera views of any asset the board flags and post what you see."),
    ("researcher", "Look up references for any alarm or anomaly on the board and post what applies."),
    ("operator", "Fix what the board shows is wrong, safely, and confirm the effect. If nothing is wrong, change nothing."),
]

FORCE = "You must now choose DONE, ESCALATED or NO_ACTION (not CONTINUE)."


def run(goal: str, toolbox, log, budget: dict, kb_dir, step_cap: int = STEP_CAP) -> dict:
    kb_dir = Path(kb_dir)
    ensure_gate(toolbox, log)
    ensure_goal(goal, log)
    run_dir = run_dir_of(log) or Path("runs") / "adhoc"
    kb = KB(kb_dir, log)
    registry = ActorToolbox(toolbox, "worker:diagnoser").call("list_assets", {})
    registry = registry if ok(registry) else {}
    ltxt, rtxt, lessons = kb_context(kb, goal, "worker:operator")
    team = Team(toolbox, log, kb_dir, run_dir, BRIEFING.format(goal=goal, registry=compact_registry(registry), lessons=ltxt, refs=rtxt),
                registry=registry)

    turns = 0
    tasks = list(TASKS)
    claim = None
    for _ in range(3):
        for worker, instruction in tasks:
            if turns >= step_cap:
                break
            steps = min(8, step_cap - turns)
            team.run_task(worker, instruction, max_steps=steps)
            turns += steps
        claim = team.claim(goal, FORCE if turns >= step_cap else "")
        if claim["status"] != "CONTINUE":
            break
        tasks = [("diagnoser", f"The team said more work is needed: {claim['summary'][:300]}. Do it."), TASKS[-1]]
    if claim is None or claim["status"] == "CONTINUE":
        claim = team.claim(goal, FORCE, max_steps=2)
        if claim["status"] == "CONTINUE":
            claim["status"] = "ESCALATED"  # conservative: an undecided baseline is never counted as claiming done

    status, summary = claim["status"], claim["summary"]
    log.emit("worker:operator", "claim", {"status": status, "summary": summary})
    if status in ("DONE", "ESCALATED"):
        try:
            write_lesson(kb, None, goal, status, [], [], team.board.read(), [], log, verified=False, actor="worker:operator")
        except Exception:
            pass
    log.emit("worker:operator", "final", {"status": status, "summary": summary})
    return {"status": status, "summary": summary, "usage": llm.USAGE.snapshot() if hasattr(llm, "USAGE") else {}}

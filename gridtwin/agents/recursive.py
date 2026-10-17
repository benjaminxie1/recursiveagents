"""The recursive loop: one sub-agent per suspected cause, each with its own checks and its own verifier.

run(goal, toolbox, log, budget, kb_dir) -> {"status": "DONE"|"ESCALATED"|"NO_ACTION", "summary": str}, the same
contract as `full.run`, so the bench runs it with `--agent gridtwin.agents.recursive:run`.

The root starts like `full`: the planner writes the whole-plant DoD and the survey tasks. After the survey the
planner triages the evidence into suspected causes. With one cause (or none) the root carries on as the flat loop,
so a single-fault incident costs one extra planner call and nothing else. With two or more independent causes the
root spawns a sub-agent per cause: a fresh worker team, scoped DONE checks, its own verifier and a share of the tool
budget. Sub-agents work side by side in sim time: each does its tasks, the root lets every open work order finish
once, then each sub-agent claims for its own cause and its verifier re-runs its checks. A sub-agent whose fix fails
its verifier may split its cause again, down to MAX_DEPTH.

Sub-agents never claim on the run. Each reports to the root's board, and the checks a sub-agent's verifier passed
join the root's DONE branch. The root then claims once, and the root verifier re-runs the whole-plant DoD with the
hold, exactly as in `full`. One `claim` and one `final` per run, so the judge and the verifier scores see the root.
"""
from __future__ import annotations

from pathlib import Path

from gridtwin import llm
from gridtwin.agents import dod as D
from gridtwin.agents.common import ActorToolbox, extract_json, ok, read_events, run_dir_of
from gridtwin.agents.full import (BRIEFING, HOLD_MIN, MAX_ROUNDS, VERIFY_RESERVE, _feedback, ensure_gate, ensure_goal,
                                  kb_context, wait_for_tickets, write_lesson)
from gridtwin.agents.planner import Planner, _clean_tasks, compact_registry
from gridtwin.agents.supervisor import Supervisor
from gridtwin.agents.verifier import Verifier
from gridtwin.agents.workers import Chaos, Team
from gridtwin.kb import KB

MAX_DEPTH = 2         # the root is depth 0; a sub-agent at MAX_DEPTH never splits
MAX_CAUSES = 3
MAX_SUB_CHECKS = 4
MERGE_PER_SUB = 2     # checks from each verified sub-agent that join the root's DONE branch
ROOT_RESERVE = 20     # tool calls kept for the root's claim, verification and post-hold re-verification
SUB_MIN_CALLS = 8     # a sub-agent needs about this many tool calls to confirm, fix and verify
SUB_MAX_CALLS = 18
SUB_ROUNDS = 2

TRIAGE_REQUEST = """TRIAGE. {scope}Team board:
{board}

Before anyone fixes anything, decide how many independent causes the evidence shows. Causes are independent when they sit on different equipment or need different fixes. An alarm and the output drop it explains are one cause, not two.
Each cause gets its own sub-agent: a fresh team that confirms the cause, fixes it and proves the fix with its own checks, while other sub-agents handle the other causes.
For each cause give a short label, the asset ids involved, a brief for the sub-agent (the evidence so far and what a fix must achieve), 1 to 3 tasks in evidence-before-action order, and 1 to {max_checks} DONE checks in the DoD language. The checks must pass only once this cause is fixed, and a healthy plant must always pass them. Use only asset ids, points and viewpoints from the registry.
If the evidence shows one cause or none, return one entry or an empty list. Never split one cause into parts.
Reply with only JSON: {{"causes": [{{"label": "...", "assets": ["..."], "brief": "...", "tasks": [{{"worker": "...", "instruction": "..."}}], "checks": [...]}}]}}"""

SPLIT_SCOPE = """This is sub-agent {agent}'s scope only: {label} (assets: {assets}). Its fix failed its own verifier: {failing}. Split it only if that failure shows a second, independent cause inside this scope; otherwise return one entry.
"""

SUB_GOAL = """SUB-AGENT {agent} of the run for: {goal}
Your scope is one suspected cause: {label} (assets: {assets}).
{brief}
Other sub-agents handle the other causes, so judge only your scope. DONE = this cause is confirmed and fixed. NO_ACTION = the evidence does not hold up and nothing in your scope needs fixing. ESCALATED = no available command or work order can safely fix it; page a human and curtail first. Leave equipment outside your scope alone."""


def parse_causes(reply, start: int = 1) -> list[dict]:
    """Triage output -> sub-agent specs with validated, renumbered checks. Never raises."""
    d = extract_json(reply) if isinstance(reply, str) else reply
    raw = d.get("causes") if isinstance(d, dict) else d if isinstance(d, list) else []
    out = []
    for c in raw if isinstance(raw, list) else []:
        if len(out) >= MAX_CAUSES:
            break
        if not isinstance(c, dict):
            continue
        assets = [str(a) for a in (c.get("assets") or []) if isinstance(a, str) and a.strip()][:6]
        label = str(c.get("label") or c.get("cause") or ", ".join(assets)).strip()[:160]
        if not label:
            continue
        n = start + len(out)
        checks = []
        for i, ch in enumerate(c.get("checks") or []):
            if not isinstance(ch, dict):
                continue
            ch = dict(ch, id=f"sub{n}_{i + 1}")
            if not D.validate_check(ch):
                checks.append(D.sanitize_check(ch))
            if len(checks) >= MAX_SUB_CHECKS:
                break
        if not checks and assets:  # the minimum a sub-agent's fix must show: its assets report fresh data
            fb = {"id": f"sub{n}_fresh", "kind": "telemetry", "op": "fresh", "assets": assets, "max_age_min": 15}
            checks = [] if D.validate_check(fb) else [fb]
        out.append({"id": f"sub-{n}", "n": n, "label": label, "assets": assets, "brief": str(c.get("brief") or "")[:1200],
                    "tasks": _clean_tasks(c.get("tasks"))[:3], "checks": checks})
    return out


def combine(results: list[dict]) -> dict:
    """One status for a set of sibling sub-agents: any ESCALATED wins, then UNRESOLVED, then DONE, then NO_ACTION."""
    statuses = [r["status"] for r in results]
    for s in ("ESCALATED", "UNRESOLVED", "DONE"):
        if s in statuses:
            status = s
            break
    else:
        status = "NO_ACTION"
    done = [r for r in results if r["status"] == "DONE"]
    return {"status": status, "verified": bool(done) and all(r["verified"] for r in done),
            "checks": [c for r in done if r["verified"] for c in r["checks"]],
            "summary": " | ".join(f"{r['agent']} {r['status']}: {r['summary'][:200]}" for r in results)}


def leaves(result: dict) -> list[dict]:
    kids = result.get("children") or []
    return [x for k in kids for x in leaves(k)] if kids else [result]


class Ctx:
    """What every agent in the tree shares: the plant tools, the log, the root planner and the root supervisor."""

    def __init__(self, goal, toolbox, log, kb_dir, run_dir, registry, planner, sup, ltxt, rtxt):
        self.goal, self.toolbox, self.log, self.kb_dir, self.run_dir = goal, toolbox, log, kb_dir, run_dir
        self.registry, self.planner, self.sup, self.ltxt, self.rtxt = registry, planner, sup, ltxt, rtxt
        self.next_n = 1

    def calls(self) -> int:
        n = getattr(self.toolbox, "calls", None)
        return n if isinstance(n, int) else sum(1 for e in read_events(self.log) if e.get("type") == "tool_call")

    def emit(self, decision: str, reason: str, **data):
        self.log.emit("supervisor", "supervisor", {"decision": decision, "reason": reason, **data})

    def triage(self, board_posts: list[dict], scope: str = "") -> list[dict]:
        btxt = "\n".join(f"- [{p['worker']}] {p['text'][:300]}" for p in board_posts[-15:]) or "(no posts)"
        try:
            reply = self.planner._ask(TRIAGE_REQUEST.format(scope=scope, board=btxt, max_checks=MAX_SUB_CHECKS),
                                      max_tokens=8192, low_effort=True)
        except Exception as e:  # triage is an optimisation: without it the root runs as the flat loop
            self.emit("continue", f"triage failed ({type(e).__name__}); no split")
            return []
        causes = parse_causes(reply, start=self.next_n)
        self.next_n += len(causes)
        return causes


class SubAgent:
    def __init__(self, ctx: Ctx, cause: dict, depth: int, share: int):
        self.ctx, self.cause, self.depth, self.share = ctx, cause, depth, share
        self.agent, self.checks = cause["id"], cause["checks"]
        self.goal = SUB_GOAL.format(agent=self.agent, goal=ctx.goal[:300], label=cause["label"],
                                    assets=", ".join(cause["assets"]) or "see brief", brief=cause["brief"])
        self.team = Team(ctx.toolbox, ctx.log, ctx.kb_dir, ctx.run_dir,
                         BRIEFING.format(goal=self.goal, registry=compact_registry(ctx.registry), lessons=ctx.ltxt, refs=ctx.rtxt),
                         registry=ctx.registry)
        self.verifier = Verifier(ctx.toolbox, ctx.log, ctx.kb_dir, ctx.registry)
        self.tasks = cause["tasks"] or [
            {"worker": "diagnoser", "instruction": f"Confirm the suspected cause in your scope with fresh readings and post what you find: {cause['label']}."},
            {"worker": "operator", "instruction": "If the board confirms the cause, apply the least invasive safe fix in your scope (lockout first where needed). If it does not, change nothing."},
        ]
        self.used, self.feedback, self.result, self.claimed = 0, "", None, None
        ctx.emit("subagent_start", f"{self.agent} (depth {depth}): {cause['label']}", agent=self.agent, depth=depth,
                 assets=cause["assets"], checks=[c["id"] for c in self.checks], tool_calls=share)

    def left(self) -> int:
        return self.share - self.used

    def _spend(self, fn, *a, **kw):
        before = self.ctx.calls()
        try:
            return fn(*a, **kw)
        finally:
            self.used += self.ctx.calls() - before

    def work(self):
        """Run this round's tasks, keeping enough calls back to claim and verify."""
        reserve = len(self.checks) + 2
        for t in self.tasks:
            cap = min(6, self.left() - reserve)
            if cap < 1:
                break
            self._spend(self.team.run_task, t["worker"], t["instruction"], max_calls=cap)

    def review(self, note: str, last_round: bool):
        """Claim for this scope and have this sub-agent's own verifier check it."""
        fb = "\n".join(x for x in (self.feedback, note) if x)
        claim = self._spend(self.team.claim, self.goal, fb, max_calls=max(1, min(2, self.left() - len(self.checks))))
        status, summary = claim["status"], claim["summary"]
        self.feedback = ""
        if status == "CONTINUE":
            self.tasks = claim["next_tasks"] and _clean_tasks(claim["next_tasks"])[:3] or self.tasks
            if last_round:
                self.result = self._result("UNRESOLVED", f"still working: {summary}")
            return
        if status != "DONE":
            self.result = self._result(status, summary)
            return
        if self.left() < 1:
            self.result = self._result("DONE", summary + " (not verified: out of tool budget)", verified=False)
            return
        v = self._spend(self.verifier.verify, self.goal, "DONE", self.checks, audit=False)
        if v["pass"]:
            self.result = self._result("DONE", summary, verified=True)
            return
        bad = [x["check_id"] for x in v["verdicts"] if not x["pass"]]
        self.feedback = "YOUR VERIFIER REJECTED the claim for your scope. " + _feedback(v)
        if self.depth < MAX_DEPTH and self.left() >= 2 * SUB_MIN_CALLS:
            scope = SPLIT_SCOPE.format(agent=self.agent, label=self.cause["label"], assets=", ".join(self.cause["assets"]), failing=", ".join(bad))
            kids = self.ctx.triage(self.team.board.read(), scope=scope)
            if len(kids) >= 2:
                share = min(SUB_MAX_CALLS, self.left() // len(kids))
                self.ctx.emit("split", f"{self.agent} splits into {len(kids)} sub-agents after its verifier rejected {', '.join(bad)}",
                              agent=self.agent, depth=self.depth, children=[k["id"] for k in kids], tool_calls=share)
                before = self.ctx.calls()
                children = fan_out(self.ctx, [SubAgent(self.ctx, k, self.depth + 1, share) for k in kids])
                self.used += self.ctx.calls() - before
                c = combine(children)
                self.result = self._result(c["status"], c["summary"], verified=c["verified"], checks=c["checks"], children=children)
                return
        if last_round:
            self.result = self._result("UNRESOLVED", f"verifier rejected {', '.join(bad)}: {summary}")
            return
        self.tasks = [
            {"worker": "diagnoser", "instruction": f"Your verifier rejected the fix ({', '.join(bad)}). Re-check that evidence with fresh readings and post what is still wrong."},
            {"worker": "operator", "instruction": "Fix what your verifier rejected, within your scope only, then confirm with a fresh reading."},
        ]

    def _result(self, status, summary, verified=False, checks=None, children=None) -> dict:
        return {"agent": self.agent, "label": self.cause["label"], "depth": self.depth, "status": status,
                "summary": str(summary)[:600], "verified": verified,
                "checks": (self.checks if checks is None else checks) if verified else [], "children": children or []}


def fan_out(ctx: Ctx, subs: list[SubAgent]) -> list[dict]:
    """Sub-agents work side by side: everyone's tasks, one wait for all open work orders, then everyone's review."""
    for rnd in range(1, SUB_ROUNDS + 1):
        active = [s for s in subs if s.result is None]
        if not active:
            break
        for s in active:
            s.work()
        tickets = wait_for_tickets(ctx.toolbox, ctx.log, ctx.sup)
        note = ("Completed while the team waited: " + "; ".join(f"{t['ticket']} {t['work_order']} on {t['asset']}" for t in tickets) +
                ". Restore anything you locked out and confirm with a fresh reading before claiming.") if tickets else ""
        for s in active:
            s.review(note, last_round=rnd == SUB_ROUNDS)
    out = []
    for s in subs:
        r = s.result or s._result("UNRESOLVED", "no verified outcome")
        r["tool_calls"] = s.used
        ctx.emit("subagent_result", f"{s.agent}: {r['status']}{' (verified)' if r['verified'] else ''}", agent=s.agent, depth=s.depth,
                 status=r["status"], verified=r["verified"], checks=[c["id"] for c in r["checks"]], tool_calls=s.used)
        out.append(r)
    return out


def run(goal: str, toolbox, log, budget: dict, kb_dir, chaos: Chaos | None = None, max_rounds: int = MAX_ROUNDS) -> dict:
    kb_dir = Path(kb_dir)
    ensure_gate(toolbox, log)
    ensure_goal(goal, log)
    run_dir = run_dir_of(log) or Path("runs") / "adhoc"
    kb = KB(kb_dir, log)
    planner = Planner(log)

    # plan (as in full)
    registry = ActorToolbox(toolbox, "planner").call("list_assets", {})
    registry = registry if ok(registry) else {}
    ltxt, rtxt, lessons = kb_context(kb, goal, "planner")
    p = planner.plan(goal, registry, f"Lessons:\n{ltxt}\n\nReferences:\n{rtxt}")
    dod, tasks = p["dod"], p["tasks"]
    if not any(c.get("rule") == "fix_applied" for c in dod["branches"]["DONE"]):
        dod, _ = D.merge_checks(dod, "DONE", [{"id": "done_fix_applied", "kind": "safety", "rule": "fix_applied", "params": {}}])
    log.emit("planner", "dod", {"checks": dod["branches"], "tasks": tasks, "repairs": p["repairs"],
                                "lessons_used": [h["id"] for h in lessons]})

    team = Team(toolbox, log, kb_dir, run_dir, BRIEFING.format(goal=goal, registry=compact_registry(registry), lessons=ltxt, refs=rtxt), chaos,
                registry=registry)
    verifier = Verifier(toolbox, log, kb_dir, registry)
    sup = Supervisor(goal, budget, log, toolbox)
    ctx = Ctx(goal, toolbox, log, kb_dir, run_dir, registry, planner, sup, ltxt, rtxt)
    all_verdicts, failed_before, feedback = [], [], ""
    final, triaged, tree = None, False, []

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
            cap = 6 if left is None else max(1, min(6, left - VERIFY_RESERVE))
            team.run_task(t["worker"], t["instruction"], max_calls=cap)

        if not triaged:  # after the survey: one cause stays flat, several get a sub-agent each
            triaged = True
            causes = ctx.triage(team.board.read())
            left = sup.calls_left()
            avail = (60 if left is None else left) - ROOT_RESERVE
            if len(causes) >= 2 and avail >= 2 * SUB_MIN_CALLS:
                causes = causes[:max(2, avail // SUB_MIN_CALLS)]
                share = min(SUB_MAX_CALLS, avail // len(causes))
                ctx.emit("split", f"{len(causes)} independent causes; one sub-agent each with {share} tool calls",
                         depth=0, children=[c["id"] for c in causes], tool_calls=share,
                         causes=[{"agent": c["id"], "label": c["label"], "assets": c["assets"]} for c in causes])
                tree = fan_out(ctx, [SubAgent(ctx, c, 1, share) for c in causes])
                merged = []
                for r in (x for t in tree for x in leaves(t)):
                    team.board.post(r["agent"], f"{r['label']}: {r['status']}" + (" (proved by its own verifier)" if r["verified"] else "") +
                                    f". {r['summary'][:400]}", [])
                    if r["verified"] and r["status"] == "DONE":
                        merged += r["checks"][:MERGE_PER_SUB]
                if merged:
                    dod, ids = D.merge_checks(dod, "DONE", merged)
                    log.emit("planner", "dod", {"checks": dod["branches"], "tasks": tasks, "added_by_subagents": ids})
                feedback = "SUB-AGENT RESULTS (each worked one cause): " + "; ".join(
                    f"{r['agent']} {r['label']}: {r['status']}{' verified' if r['verified'] else ''}" for t in tree for r in leaves(t)) + \
                    ". Claim for the whole plant: DONE only if every cause is fixed now; ESCALATED if any cause needs a human."
            else:
                why = "one cause" if len(causes) == 1 else "no clear cause" if not causes else f"not enough budget for {len(causes)} sub-agents"
                ctx.emit("continue", f"no split ({why}); the root works it as one team", depth=0,
                         causes=[{"label": c["label"], "assets": c["assets"]} for c in causes])

        decision, reason = sup.decide(rnd, team.actions(), team.board.read())
        if decision in ("page", "stop"):
            final = escalate(reason)
            break
        if decision == "replan":
            replan(rnd, reason)
            continue

        done_tickets = wait_for_tickets(toolbox, log, sup)
        if done_tickets:
            feedback = (feedback + "\n" if feedback else "") + "Completed while you waited: " + "; ".join(
                f"{t['ticket']} {t['work_order']} on {t['asset']}" for t in done_tickets) + ". Restore anything you locked out and confirm with a fresh reading before claiming."
        claim = team.claim(goal, feedback)
        if claim["status"] == "CONTINUE":
            feedback = ""
            replan(rnd, f"the team says more work is needed: {claim['summary'][:400]}")
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
    subagents = [{k: r[k] for k in ("agent", "label", "depth", "status", "verified", "tool_calls") if k in r}
                 for t in tree for r in [t] + [x for x in leaves(t) if x is not t]]
    return {"status": final["status"], "summary": final["summary"], "usage": snap, "restarts": team.restarts,
            "subagents": subagents}

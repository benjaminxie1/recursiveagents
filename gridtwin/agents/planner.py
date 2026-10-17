"""Planner: compiles a vague goal into a definition of done (DoD) and an ordered task list.

The system prompt states principles only. It never names a fault or a fix.
The planner keeps one append-only conversation per run so replans hit the prefix cache.
"""
from __future__ import annotations

import json

from gridtwin import llm
from gridtwin.agents import dod as D
from gridtwin.agents.common import extract_json

WORKER_NAMES = ("inspector", "diagnoser", "researcher", "operator")

SYSTEM = """You are the planner of GridTwin's autonomous operations team for a solar plant. An operator gives a vague goal and walks away. You turn it into:
1. a definition of done (DoD): machine-checkable checks that an independent verifier will run itself against the live plant, with one branch per possible outcome;
2. an ordered task list for the workers (inspector: cameras and photos; diagnoser: telemetry, alarms, diagnostics; researcher: cited OEM and standards references; operator: the only one who changes plant state).

Principles:
- Tie business goals to the revenue meter (POI-1), not to what devices report about themselves. Device telemetry can be frozen or wrong, so every telemetry check needs max_age_min, and the DoD should cross-check independent sources (device sums against the meter, peers against each other, cameras against SCADA).
- Write all three branches.
  DONE: the problem is fixed and the plant is healthy: fresh telemetry, output consistent across sources and peers, no active alarms, and safety invariants held.
  ESCALATED: when no safe fix exists, done means the plant is curtailed to a safe limit, a human was paged, and the outcome is reported as not resolved. Use page_sent, an action_taken check for the curtailment, and safety invariants.
  NO_ACTION: "nothing wrong" is a valid outcome with its own checks: the same health checks as DONE plus no_dispatch and no_state_change.
- Ratings live on the inverters: for plant-relative values use {{"rated": "INV-*", "factor": f}}.
- Keep each branch to 4 to 8 checks. Prefer the cross-checking ops (fresh, balance, peer, no_alarms) over per-asset thresholds, and never compare a state string unless you know its exact value from the registry. Every check must pass on a healthy plant; a check a correct outcome could fail is a bug.
- Prefer checks that a lazy or mistaken fix would fail. Put hold_min on the key output checks so a fix that only works for a few minutes fails.
- Tag every check with "requirements": the ids from the requirements catalog it gives evidence for (a check on "INV-*" evidences the INV requirements for whichever inverter is at fault). Tag generously; across the DoD, try to cover every requirement on the assets the goal could involve.
- Use only asset ids, points and viewpoints from the plant registry you are given. Only add a citation check for a URL and quote that appear verbatim in the references you are given: copy the url exactly as written, including any #fragment, and copy the quote word for word from that reference's text.
- Lessons from verified past incidents are strong hints, not proof: have the team confirm the symptoms first, then apply what worked.
- Tasks: 2 to 4, ordered so evidence comes before action. The whole run, verification included, has about 60 tool calls, so be economical. The first round establishes facts: one diagnoser survey (active alarms, then the meter and each inverter in a single read each, checking freshness) and camera looks only at assets that the data or alarms point to; never photograph everything. Give the operator a task only for an action you can already justify; you will see the team's findings and assign the fix next.
- When you assign a fix, give the operator one precise instruction: which asset, which command or work order, the lockout and restore steps, how long to wait, and the reading that confirms it.

The DoD language:
{schema}

Reply with only JSON, no prose."""

PLAN_REQUEST = """GOAL: {goal}

PLANT REGISTRY (from list_assets):
{registry}

REQUIREMENTS CATALOG (from list_requirements; codes and standards x components, each must be proven with evidence):
{requirements}

TOOLS THE WORKERS HAVE:
{tools}

KNOWLEDGE BASE (retrieved for this goal):
{kb}

Write the plan. Reply with only JSON:
{{"dod": {{"version": 1, "branches": {{"DONE": [...], "ESCALATED": [...], "NO_ACTION": [...]}}}}, "tasks": [{{"worker": "...", "instruction": "..."}}]}}"""

PRECHECK_REQUEST = """PRE-CHECK. Before any work, the independent verifier ran the DONE branch against the live plant. Failing checks show what is wrong right now (a healthy plant passes them all):
{verdicts}

Write the first tasks (2 to 4) aimed at the failing checks: confirm the cause with the cheapest evidence, then give the operator the precise fix if the evidence will already be on the board. If every check passes, have the team confirm nothing is wrong and change nothing. Reply with only JSON:
{{"tasks": [{{"worker": "...", "instruction": "..."}}]}}"""

REPLAN_REQUEST = """REPLAN (round {round}). Supervisor: {reason}

Verifier results so far (check id -> pass, evidence):
{verdicts}

Team board:
{board}

Current DoD check ids by branch: {ids}

Write the next tasks (2 to 3, precise; if the cause is confirmed, the operator's fix comes first). You may add checks to any branch (existing checks can never be removed). Reply with only JSON:
{{"tasks": [{{"worker": "...", "instruction": "..."}}], "add_checks": {{"DONE": [], "ESCALATED": [], "NO_ACTION": []}}}}"""


def system_prompt() -> str:
    return SYSTEM.format(schema=D.schema_text())


def tool_summary() -> str:
    """One line per worker tool, with its enum values: the tool surface, as the workers see it."""
    from gridtwin.agents.workers import TOOL_SCHEMAS

    lines = []
    for t in TOOL_SCHEMAS:
        f = t["function"]
        params = []
        for k, v in (f.get("parameters") or {}).get("properties", {}).items():
            params.append(f"{k}={'|'.join(map(str, v['enum']))}" if v.get("enum") else k)
        lines.append(f"- {f['name']}({', '.join(params)}): {f.get('description', '')[:140]}")
    return "\n".join(lines)


def compact_registry(registry: dict) -> str:
    """Asset ids, types, points, viewpoints and ratings only."""
    lines = []
    for a in (registry or {}).get("assets", []):
        aid = a.get("asset") or a.get("id")
        bits = [f"{aid} ({a.get('type') or a.get('component') or '?'})"]
        for k in ("rating_kwac", "rated_kw", "breaker", "feeds", "inverter"):
            if a.get(k) is not None:
                bits.append(f"{k}={a[k]}")
        if a.get("points"):
            bits.append("points=" + ",".join(a["points"]))
        if a.get("viewpoints"):
            bits.append("viewpoints=" + ",".join(a["viewpoints"]))
        lines.append(" ".join(bits))
    if registry and registry.get("rating_kwac"):
        lines.insert(0, f"plant rating_kwac={registry['rating_kwac']}")
    return "\n".join(lines) or "(registry unavailable)"


def compact_requirements(result: dict) -> str:
    """One line per requirement: id (asset prefix): title [standards; how it is verified]."""
    lines = []
    for r in (result or {}).get("requirements") or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        targets = r.get("applies_to")
        scope = ",".join(targets) if isinstance(targets, list) else str(targets or "")
        vb = r.get("verify_by") or {}
        lines.append(f"{r['id']} ({scope}): {r.get('title', '')} [{','.join(r.get('standards') or [])}; "
                     f"{vb.get('kind', '?')}: {vb.get('how', '')}]")
    return "\n".join(lines) or "(catalog unavailable)"


def _ids(registry, kind_prefix=None, type_=None):
    out = []
    for a in (registry or {}).get("assets", []):
        aid = a.get("asset") or a.get("id")
        if type_ and a.get("type") == type_:
            out.append(aid)
        elif kind_prefix and aid and aid.startswith(kind_prefix) and not type_:
            out.append(aid)
    return out


def default_dod(registry: dict) -> dict:
    """Principle-based fallback DoD, used only when the planner's output can't be validated."""
    rating = float((registry or {}).get("rating_kwac") or 5000)
    health = [
        {"id": "fresh_inv", "kind": "telemetry", "op": "fresh", "assets": "INV-*", "max_age_min": 5, "requirements": ["REQ-INV-04"]},
        {"id": "balance_poi", "kind": "telemetry", "op": "balance", "lhs": {"assets": "INV-*", "point": "ac_kw"},
         "rhs": {"asset": "POI-1", "point": "export_kw"}, "tol_pct": 6, "max_age_min": 5, "requirements": ["REQ-INV-04"]},
        {"id": "peer_inv", "kind": "telemetry", "op": "peer", "assets": "INV-*", "point": "ac_kw", "min_ratio": 0.8, "max_age_min": 5,
         "requirements": ["REQ-INV-01"]},
        {"id": "alarms_clear", "kind": "telemetry", "op": "no_alarms", "max_age_min": 5, "requirements": ["REQ-SW-01"]},
        {"id": "safe_gate", "kind": "safety", "rule": "no_action_after_reject", "params": {}},
        {"id": "safe_close", "kind": "safety", "rule": "no_close_on_fault", "params": {}, "requirements": ["REQ-SW-01", "REQ-INV-05"]},
    ]
    done = [dict(c, id="d_" + c["id"]) for c in health]
    noact = [dict(c, id="n_" + c["id"]) for c in health] + [
        {"id": "n_no_dispatch", "kind": "safety", "rule": "no_dispatch", "params": {}},
        {"id": "n_no_change", "kind": "safety", "rule": "no_state_change", "params": {}},
    ]
    esc = [
        {"id": "e_paged", "kind": "safety", "rule": "page_sent", "params": {}},
        {"id": "e_curtailed", "kind": "safety", "rule": "action_taken",
         "params": {"tool": "operate", "asset": "POI-1", "action": "set_plant_limit_kw", "value_max": 0.5 * rating}},
        {"id": "e_safe_gate", "kind": "safety", "rule": "no_action_after_reject", "params": {}},
        {"id": "e_safe_close", "kind": "safety", "rule": "no_close_on_fault", "params": {}},
    ]
    return {"version": 1, "branches": {"DONE": done, "ESCALATED": esc, "NO_ACTION": noact}}


DEFAULT_TASKS = [
    {"worker": "diagnoser", "instruction": "Survey the plant: active alarms, then each inverter's ac_kw and status with last_update_ts, and POI-1 export_kw. Compare devices with the meter and with each other. Post findings with seqs."},
    {"worker": "inspector", "instruction": "Look at the camera view of any asset the board flags as suspicious and post what you see."},
    {"worker": "researcher", "instruction": "Look up references for any alarm or anomaly on the board and post what applies with exact URLs."},
    {"worker": "operator", "instruction": "If the board shows a confirmed problem, take the least invasive safe action that fixes it (lockout first where needed) and confirm the effect with a fresh reading. If nothing is wrong, change nothing."},
]


MAX_BRANCH_CHECKS = 10


def _plan_errors(dod) -> list[str]:
    if not isinstance(dod, dict):
        return ["reply was not a JSON object with a 'dod' key"]
    errors = D.validate(dod)
    for b, checks in (dod.get("branches") or {}).items():
        if isinstance(checks, list) and len(checks) > MAX_BRANCH_CHECKS:
            errors.append(f"branch {b} has {len(checks)} checks; keep it to at most {MAX_BRANCH_CHECKS}")
        for c in checks if isinstance(checks, list) else []:
            if (isinstance(c, dict) and c.get("op") == "threshold" and c.get("point") in POWER_POINTS
                    and isinstance(c.get("value"), dict) and c.get("cmp") in (">=", ">")):
                errors.append(f"check {c.get('id')}: output against a rating depends on sun and weather, so a healthy "
                              "plant can fail it; use peer or balance for output instead")
    return errors


POWER_POINTS = ("ac_kw", "dc_kw", "export_kw", "p_kw")


def _clean_tasks(tasks) -> list[dict]:
    out = []
    for t in tasks or []:
        if isinstance(t, dict) and t.get("instruction"):
            w = str(t.get("worker", "diagnoser")).lower()
            out.append({"worker": w if w in WORKER_NAMES else "diagnoser", "instruction": str(t["instruction"])[:1200]})
    return out[:6]


def _repair_dod(dod: dict, registry: dict) -> tuple[dict, list[str]]:
    """Keep the planner's valid checks; fill a missing or empty branch from the default."""
    notes = []
    fallback = default_dod(registry)
    branches = (dod or {}).get("branches") or {}
    seen, fixed = set(), {}
    for b in D.OUTCOMES:
        keep = []
        for c in branches.get(b) or []:
            if not isinstance(c, dict):
                continue
            if D.validate_check(c) or c.get("id") in seen:
                notes.append(f"dropped invalid check {c.get('id')} in {b}")
                continue
            seen.add(c["id"])
            keep.append(c)
        if len(keep) > MAX_BRANCH_CHECKS:
            notes.append(f"branch {b}: kept the first {MAX_BRANCH_CHECKS} of {len(keep)} checks")
            keep = keep[:MAX_BRANCH_CHECKS]
        if not keep:
            notes.append(f"branch {b} empty: used the default checks")
            keep = [c for c in fallback["branches"][b] if c["id"] not in seen]
            seen.update(c["id"] for c in keep)
        fixed[b] = keep
    return {"version": 1, "branches": fixed}, notes


class Planner:
    def __init__(self, log=None):
        self.log = log
        self.messages = [{"role": "system", "content": system_prompt()}]

    def _ask(self, content: str, max_tokens: int = 8192, low_effort: bool = False) -> str:
        """low_effort: Nemotron Super's short-reasoning mode (about 5x faster), used for replans and lessons."""
        self.messages.append({"role": "user", "content": content})
        kw = {"extra_body": {"chat_template_kwargs": {"enable_thinking": True, "low_effort": True}}} if low_effort else {}
        resp = llm.chat("planner", self.messages, max_tokens=max_tokens, log=self.log, actor="planner", **kw)
        out = llm.text(resp)
        self.messages.append({"role": "assistant", "content": out})
        return out

    def plan(self, goal: str, registry: dict, kb_text: str, requirements: dict | None = None) -> dict:
        reply = self._ask(PLAN_REQUEST.format(goal=goal, registry=compact_registry(registry), requirements=compact_requirements(requirements),
                                              tools=tool_summary(), kb=kb_text or "(empty)"),
                          max_tokens=12288)
        d = extract_json(reply)
        dod = d.get("dod") if isinstance(d, dict) else None
        errors = _plan_errors(dod)
        if errors:  # re-ask once with the validation errors
            reply = self._ask("The DoD failed validation:\n- " + "\n- ".join(errors[:25]) +
                              "\nFix every error and reply with the complete corrected JSON (dod and tasks).")
            d2 = extract_json(reply)
            if isinstance(d2, dict) and isinstance(d2.get("dod"), dict):
                d, dod = d2, d2["dod"]
                errors = _plan_errors(dod)
        notes = []
        if errors:
            dod, notes = _repair_dod(dod if isinstance(dod, dict) else {}, registry)
        tasks = _clean_tasks(d.get("tasks") if isinstance(d, dict) else None) or list(DEFAULT_TASKS)
        return {"dod": dod, "tasks": tasks, "validation_errors": errors, "repairs": notes}

    def precheck_tasks(self, verdicts: list[dict]) -> list[dict]:
        vtxt = "\n".join(f"- {v['check_id']} ({v['kind']}): {'pass' if v['pass'] else 'FAIL'} "
                         f"{json.dumps(v.get('evidence'), default=str)[:400]}" for v in verdicts) or "(no checks ran)"
        d = extract_json(self._ask(PRECHECK_REQUEST.format(verdicts=vtxt), max_tokens=12288))
        return _clean_tasks(d.get("tasks") if isinstance(d, dict) else None)

    def replan(self, round_no: int, reason: str, verdicts: list[dict], board_posts: list[dict], dod: dict) -> dict:
        vtxt = "\n".join(f"- {v['check_id']}: {'pass' if v['pass'] else 'FAIL'} {json.dumps(v.get('evidence'), default=str)[:300]}"
                         for v in verdicts[-20:]) or "(none yet)"
        btxt = "\n".join(f"- [{p['worker']}] {p['text'][:300]}" for p in board_posts[-15:]) or "(no posts)"
        ids = {b: [c["id"] for c in dod["branches"][b]] for b in D.OUTCOMES}
        reply = self._ask(REPLAN_REQUEST.format(round=round_no, reason=reason, verdicts=vtxt, board=btxt, ids=json.dumps(ids)),
                          max_tokens=12288, low_effort=not reason.startswith(("verifier", "regression", "the team")))
        d = extract_json(reply)
        d = d if isinstance(d, dict) else {}
        added = {}
        if not reason.startswith(("verifier", "regression")):
            d["add_checks"] = {}  # the DoD only grows in response to evidence from the verifier
        for b, checks in (d.get("add_checks") or {}).items():
            b = str(b).upper()
            if b in D.OUTCOMES and isinstance(checks, list) and checks:
                dod, ids_added = D.merge_checks(dod, b, checks)
                if ids_added:
                    added[b] = ids_added
        tasks = _clean_tasks(d.get("tasks")) or [
            {"worker": "diagnoser", "instruction": f"The verifier rejected the claim: {reason}. Re-check the failing evidence and post findings."},
            {"worker": "operator", "instruction": "Act on the board's confirmed findings to fix what the verifier rejected; confirm with a fresh reading."},
        ]
        return {"tasks": tasks, "dod": dod, "added": added}

    def lesson_symptoms(self, goal: str, board_posts: list[dict], failed: list[dict]) -> list[str]:
        """Short, generic, observable symptoms for a lesson (no asset-specific fix)."""
        btxt = "\n".join(f"- {p['text'][:300]}" for p in board_posts[-15:]) or "(none)"
        ftxt = "\n".join(f"- {v['check_id']}: {json.dumps(v.get('evidence'), default=str)[:200]}" for v in failed[-10:]) or "(none)"
        reply = self._ask(
            f"LESSON. The run for goal '{goal}' was verified. Board:\n{btxt}\nChecks that failed before the fix:\n{ftxt}\n"
            "List 2 to 5 short observable symptoms that identified the problem (what the data or cameras showed), "
            "phrased so they match a similar incident on a different asset. Reply with only JSON: {\"symptoms\": [\"...\"]}",
            max_tokens=2048, low_effort=True)
        d = extract_json(reply)
        syms = d.get("symptoms") if isinstance(d, dict) else None
        if isinstance(syms, list) and syms:
            return [str(s)[:200] for s in syms[:5]]
        return [p["text"][:200] for p in board_posts[:3]] or [goal]

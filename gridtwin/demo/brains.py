"""The model calls of the demo: diagnosis, the recursive planner and its sub-agents, the verifier's email.

Live: every call goes through gridtwin.llm on Crusoe (planner = Nemotron Super, sub-agents = Nemotron
Lightning, verifier = gpt-oss-120b, a different family). Stub: deterministic answers for tests and offline
rehearsal, selected with --stub. The recursion depth cap lives in Agent.spawn, not in a prompt.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from gridtwin import llm
from gridtwin.agents.common import extract_json
from gridtwin.config import ROLES
from gridtwin.demo.events import SUBAGENT_ROLES
from gridtwin.sim.tools import WORK_ORDERS

MAX_DEPTH = 1  # the planner is depth 0; its sub-agents are depth 1 and cannot spawn
WO_FIXES = {
    "inspect": "look only, repairs nothing",
    "replace_fan_filter": "failed or clogged inverter cooling fan/filter (overheating, derating)",
    "reseat_comms_card": "failed inverter comms card (SCADA frozen/stale, comms_ping timeout, dark display)",
    "replace_mc4_connector": "damaged DC connector (ground fault, low insulation on a combiner)",
    "clean_modules": "soiled PV modules (block output low on a clear day)",
    "mow_vegetation": "vegetation shading the array",
    "replace_module": "cracked or hot-spot PV module",
    "transformer_oil_service": "transformer oil problems (leak, dissolved gas)",
}
# Diagnosis keywords -> the work order that fixes it; the planner uses this to check its repair sub-agent.
FAULT_WORK_ORDER = [
    (("comm", "stale", "frozen", "scada", "ping", "display"), "reseat_comms_card"),
    (("fan", "overheat", "heatsink", "thermal derat"), "replace_fan_filter"),
    (("ground fault", "gfdi", "insulation", "mc4", "connector"), "replace_mc4_connector"),
    (("soil", "dust", "dirt"), "clean_modules"),
    (("vegetation", "weed", "shade", "shading"), "mow_vegetation"),
    (("hot spot", "hotspot", "crack"), "replace_module"),
    (("oil", "dga", "acetylene", "transformer"), "transformer_oil_service"),
]


def expected_work_order(text: str) -> str | None:
    t = (text or "").lower()
    for words, wo in FAULT_WORK_ORDER:
        if any(w in t for w in words):
            return wo
    return None
# Crusoe rate limits are per model, so the planner's three sub-agents run on three different Nemotron models.
SUB_MODEL_ROLE = {"safety": "gate", "repair": "worker", "acceptance": "supervisor"}


class DepthExceeded(RuntimeError):
    pass


class Agent:
    """One node of the agent tree. spawn() is the only way to create a child, and it enforces MAX_DEPTH."""

    def __init__(self, id: str, role: str, model_role: str, depth: int = 0, parent: "Agent | None" = None, task: str = "",
                 team: str = "plan", round: int = 0):
        self.id, self.role, self.model_role, self.depth, self.parent, self.task = id, role, model_role, depth, parent, task
        self.team, self.round = (parent.team, parent.round) if parent else (team, round)
        self.children: list[Agent] = []

    @property
    def model(self) -> str:
        return ROLES.get(self.model_role, self.model_role)

    def spawn(self, role: str, task: str, model_role: str = "worker") -> "Agent":
        if self.depth + 1 > MAX_DEPTH:
            raise DepthExceeded(f"{self.id} is at depth {self.depth}; sub-agents may not spawn (max depth {MAX_DEPTH})")
        child = Agent(f"{self.id}.{role}", role, model_role, self.depth + 1, self, task)
        self.children.append(child)
        return child

    def event(self, status: str, output=None) -> dict:
        return {"id": self.id, "parent": self.parent.id if self.parent else None, "depth": self.depth, "role": self.role,
                "model": self.model, "task": self.task, "status": status, "output": output, "team": self.team, "round": self.round}


DIAGNOSE_SYS = """You are the diagnosis lead of an agent team that runs a 20 MW solar plant (4 inverters INV-1..INV-4, feeder breakers 52-I1..52-I4).
You get the SCADA sensor feed (with data_age_min = minutes since SCADA last heard from the device), field diagnostics and camera descriptions.
Find the one faulty asset and the most likely fault. Telemetry that looks perfect but has not updated is a lie, not a healthy unit.
Reply with JSON only: {"asset": "INV-n", "fault": "<short name>", "confidence": 0..1, "summary": "<2 sentences>", "evidence": ["<fact with numbers>", ...]}"""

PLAN_SYS = """You are the planner (depth 0) of a recursive agent team. You do not write the work order yourself:
you brief exactly three sub-agents, one per role, and they cannot spawn further agents.
Roles: "safety" (lockout and re-energize steps for the asset, citing the plant's breaker), "repair" (choose the work order and the field steps),
"acceptance" (what proof, read from the plant itself, will show the fix is real).
Verified lessons from past incidents are strong hints: pass their do-not items to the sub-agents that need them.
Reply with JSON only: {"briefs": {"safety": "<task>", "repair": "<task>", "acceptance": "<task>"}, "rationale": "<1 sentence>"}"""

SUB_SYS = {
    "safety": """You are the safety sub-agent. Given a fault and asset, list the lockout/tagout steps before the work and the
re-energize steps after it (open the inverter's feeder breaker 52-In before work on INV-n, close it after the work is complete).
Reply with JSON only: {"before": ["<step>"], "after": ["<step>"], "cites": ["<standard, e.g. NFPA 70E 120>"]}""",
    "repair": """You are the repair sub-agent. Choose exactly one work order; each one fixes only its own fault:
%s
Then write the field steps a contractor follows. If a lesson says a cheap visit failed before, put it in do_not.
Reply with JSON only: {"work_order": "<one of the list>", "steps": ["<step>"], "do_not": ["<item>"]}""" % "\n".join(
        f"- {w}: {WO_FIXES.get(w, '')}" for w in WORK_ORDERS),
    "acceptance": """You are the acceptance sub-agent. Write 2-4 acceptance criteria that a verifier can check against the plant itself,
never against the contractor's word: SCADA freshness (data_age_min under 5, status running), the unit answering comms_ping,
and a camera view that shows the healthy state. No power, temperature or latency ranges: they move with sun, load and time.
Reply with JSON only: {"acceptance": ["<criterion>"]}""",
}

EMAIL_BACK_SYS = """You are the verifier of a plant agent team (a different model family from the planner). The contractor said the work is done,
but your checks against the plant failed. Write a short, firm, polite email back to the contractor (max 120 words): what you checked,
what failed (with the numbers), what they must do now, and what proof you will check next. No greeting fluff. Plain text only."""


LLM_TIMEOUT_S = 60.0  # per request; a stalled Crusoe call is retried instead of hanging the run (client default is 300 s)


def _ask(role: str, system: str, user: str, log=None, actor: str | None = None, thinking: bool = False,
         max_tokens: int = 2048, max_tries: int = 8, timeout: float = LLM_TIMEOUT_S) -> dict:
    resp = llm.chat(role, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    max_tokens=max_tokens, log=log, actor=actor, thinking=thinking, max_tries=max_tries, timeout=timeout)
    txt = llm.text(resp) or llm.reasoning(resp)
    out = extract_json(txt)
    if not isinstance(out, dict):
        raise ValueError(f"{role}: no JSON in reply: {txt[:200]!r}")
    return out


class LiveBrains:
    stub = False

    def __init__(self, log=None):
        self.log = log

    def diagnose(self, evidence: dict) -> dict:
        return _ask("planner", DIAGNOSE_SYS, json.dumps(evidence, default=str), self.log, "planner", max_tokens=4096)

    def plan(self, diagnosis: dict, lessons: list[dict]) -> dict:
        user = json.dumps({"diagnosis": diagnosis, "lessons": [_lesson_brief(l) for l in lessons]}, default=str)
        return _ask("planner", PLAN_SYS, user, self.log, "planner", max_tokens=4096)

    def subagent(self, role: str, task: str, diagnosis: dict, lessons: list[dict], model_role: str = "worker") -> dict:
        user = json.dumps({"task": task, "diagnosis": diagnosis, "lessons": [_lesson_brief(l) for l in lessons]}, default=str)
        return _ask(model_role, SUB_SYS[role], user, self.log, f"subagent:{role}", max_tokens=4096)

    def email_back(self, wo: dict, failed: list[dict]) -> str:
        user = json.dumps({"work_order": {k: wo[k] for k in ("wo_id", "asset", "work_order", "steps", "forbid", "acceptance")},
                           "failed_checks": failed}, default=str)
        resp = llm.chat("verifier", [{"role": "system", "content": EMAIL_BACK_SYS}, {"role": "user", "content": user}],
                        max_tokens=2048, log=self.log, actor="verifier", max_tries=8, timeout=LLM_TIMEOUT_S)
        return (llm.text(resp) or "").strip()


class StubBrains:
    """Deterministic stand-ins with the same shapes, for tests and offline rehearsal."""

    stub = True

    def __init__(self, log=None):
        self.log = log

    def diagnose(self, evidence: dict) -> dict:
        stale = [s for s in evidence.get("sensors", []) if s.get("stale")]
        asset = stale[0]["asset"] if stale else "INV-1"
        return {"asset": asset, "fault": "comms card failure (stale SCADA)", "confidence": 0.9,
                "summary": f"{asset} telemetry is frozen while its display is dark.",
                "evidence": [f"{asset} data_age_min {stale[0]['data_age_min'] if stale else '?'}"]}

    def plan(self, diagnosis: dict, lessons: list[dict]) -> dict:
        return {"briefs": {r: f"{r} for {diagnosis['asset']}" for r in SUBAGENT_ROLES}, "rationale": "stub"}

    def subagent(self, role: str, task: str, diagnosis: dict, lessons: list[dict], model_role: str = "worker") -> dict:
        a = diagnosis["asset"]
        n = a.split("-")[-1]
        if role == "safety":
            return {"before": [f"Open feeder breaker 52-I{n} and apply lockout/tagout"], "after": [f"Close 52-I{n} after the work is complete"],
                    "cites": ["NFPA 70E 120"]}
        if role == "repair":
            return {"work_order": "reseat_comms_card", "steps": [f"Reseat the comms card in {a}"],
                    "do_not": [d for l in lessons for d in (l.get("forbid") or [])]}
        return {"acceptance": [f"{a} SCADA data_age_min under 5", f"{a} display lit in a camera view"]}

    def email_back(self, wo: dict, failed: list[dict]) -> str:
        return "Checks failed: " + "; ".join(f"{f['name']}: {f['detail']}" for f in failed)


def _lesson_brief(l: dict) -> dict:
    return {k: l.get(k) for k in ("id", "symptoms", "fix", "steps", "forbid", "acceptance") if l.get(k)}


def run_subagents(planner: Agent, brains, briefs: dict, diagnosis: dict, lessons: list[dict], on_event) -> dict:
    """Spawn one sub-agent per role (depth 1) and run them in parallel. Returns {role: output or None}."""
    kids = [planner.spawn(role, str(briefs.get(role) or f"{role} for {diagnosis.get('asset')}"), SUB_MODEL_ROLE.get(role, "worker"))
            for role in SUBAGENT_ROLES]
    for k in kids:
        on_event(k, "spawned", None)

    def one(k: Agent):
        try:
            out = brains.subagent(k.role, k.task, diagnosis, lessons, k.model_role)
            on_event(k, "done", out)
            return k.role, out
        except Exception as e:  # noqa: BLE001 - a failed sub-agent is reported, the planner falls back
            on_event(k, "failed", {"error": f"{type(e).__name__}: {e}"[:300]})
            return k.role, None

    with ThreadPoolExecutor(max_workers=len(kids)) as ex:
        return dict(ex.map(one, kids))

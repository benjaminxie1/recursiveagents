"""Decision gate in front of every `operate` and `dispatch_tech` (docs/SIMULATOR.md section 3).

`make_gate(backend=None, log=None)` returns `gate(proposed, state) -> {"route", "unsafe", "reason"}`.
Backends: `local` (the "gate" LLM role, default), `jev` (TypeSafe Jev), `openai` (OpenAI Decisions).
HTTP backends fall back to local on any error; local fails closed to `confirm`. The gate never raises
and emits no events itself (the Toolbox logs `gate` events).
"""
from __future__ import annotations

import json
import re
import math
import os

import httpx

from gridtwin import llm
from gridtwin.agents.common import extract_json

ROUTES = ("proceed", "confirm", "reject")

POLICY = (
    "Plant safety policy. "
    "1. De-energizing is safe: opening a breaker, isolating a combiner, curtailment and plant-limit reductions "
    "only remove energy or stress, so proceed with them. "
    "2. Lockout before work: a technician dispatch whose work requires de-energized equipment (the state shows "
    "work_requires_deenergized true) must not proceed while that asset is energized or its feeding breaker is "
    "closed; reject it and say the feeding breaker must be opened first. Work that does not require "
    "de-energizing may proceed. "
    "3. Never energize, re-close or close onto equipment with a suspected fault, an active insulation or ground "
    "alarm, or an unexplained protection trip on that feeder or circuit. Closing a breaker after completed work "
    "is safe when no such alarm or low insulation reading is shown for that feeder. "
    "4. Restarts and alarm resets on equipment showing active protection alarms need a diagnostic first. "
    "5. When information that matters for the decision is missing, prefer confirm."
)

ROUTE_INSTRUCTIONS = "Which path applies to the proposed action given the current state and the policy?"
ROUTE_CRITERIA = {
    "proceed": "Safe to execute now",
    "confirm": "Needs a diagnostic or a human first",
    "reject": "Unsafe",
}
UNSAFE_INSTRUCTIONS = (
    "Probability (0 to 1) that the proposed action would energize equipment with a suspected fault "
    "or otherwise endanger people or equipment."
)

SYSTEM_PROMPT = (
    "You are the safety gate for a solar and battery plant. Every proposed control action or technician "
    "dispatch is shown to you with the current plant state before it is executed. You answer two typed "
    "questions and nothing else.\n\n"
    f"{POLICY}\n\n"
    "Question 1, route (choice, exactly one of):\n"
    f"- proceed: {ROUTE_CRITERIA['proceed']}.\n"
    f"- confirm: {ROUTE_CRITERIA['confirm']}.\n"
    f"- reject: {ROUTE_CRITERIA['reject']}.\n\n"
    f"Question 2, unsafe (probability): {UNSAFE_INSTRUCTIONS}\n\n"
    "The user message is JSON with keys `proposed` (the action) and `state` (alarms, telemetry, "
    "diagnostics and other context). Reply with only this JSON object, no prose and no code fences:\n"
    '{"route": "proceed" | "confirm" | "reject", "unsafe": <number between 0 and 1>, '
    '"reason": "<one sentence citing the state that drove the answer>"}'
)

DEFAULT_REASON = "no reason given"

_SYNONYMS = {
    "proceed": "proceed", "approve": "proceed", "approved": "proceed", "allow": "proceed",
    "allowed": "proceed", "ok": "proceed", "okay": "proceed", "safe": "proceed", "go": "proceed",
    "yes": "proceed", "execute": "proceed", "accept": "proceed", "pass": "proceed",
    "reject": "reject", "rejected": "reject", "deny": "reject", "denied": "reject", "block": "reject",
    "blocked": "reject", "refuse": "reject", "refused": "reject", "stop": "reject", "unsafe": "reject",
    "no": "reject", "forbid": "reject", "forbidden": "reject",
    "confirm": "confirm", "confirmation": "confirm", "ask": "confirm", "escalate": "confirm",
    "review": "confirm", "hold": "confirm", "human": "confirm", "diagnostic": "confirm",
    "diagnose": "confirm", "wait": "confirm", "defer": "confirm", "unknown": "confirm",
}

_VALUE_KEYS = ("choice", "value", "answer", "probability", "score", "result", "output", "label")
_CONTAINER_KEYS = ("answers", "results", "decisions", "outputs", "questions", "data", "output", "result")


def _fail_closed(why: str) -> dict:
    return {"route": "confirm", "unsafe": 0.5, "reason": f"gate unavailable: {why}"[:300]}


def normalize_route(v) -> str:
    if isinstance(v, (dict, list)):
        v = _scalar(v)
    if v is None:
        return "confirm"
    s = str(v).strip().lower().strip(" .\"'`")
    if s in _SYNONYMS:
        return _SYNONYMS[s]
    if "reject" in s:  # e.g. "route: reject"; never infer proceed from a substring ("do not proceed")
        return "reject"
    return "confirm"


def normalize_unsafe(v) -> float:
    if isinstance(v, (dict, list)):
        v = _scalar(v)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, str) and v.strip().lower() in ("yes", "true", "no", "false"):
        return 1.0 if v.strip().lower() in ("yes", "true") else 0.0
    try:
        pct = isinstance(v, str) and v.strip().endswith("%")
        x = float(str(v).strip().rstrip("%")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return 0.5
    if not math.isfinite(x):
        return 0.5
    if pct:
        x /= 100.0
    return min(1.0, max(0.0, x))  # out-of-range values clamp (fail toward unsafe)


def _scalar(d):
    if isinstance(d, list):
        return d[0] if len(d) == 1 and not isinstance(d[0], (dict, list)) else None
    if not isinstance(d, dict):
        return d
    for k in _VALUE_KEYS:
        if k in d:
            v = d[k]
            return _scalar(v) if isinstance(v, dict) else v
    return None


def _find(payload, name: str, depth: int = 0):
    """The answer for question `name` in a decision-API payload of unknown shape, or None."""
    if depth > 4 or payload is None:
        return None
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and (item.get("name") == name or item.get("id") == name
                                           or item.get("question") == name):
                return item
        for item in payload:
            hit = _find(item, name, depth + 1)
            if hit is not None:
                return hit
        return None
    if not isinstance(payload, dict):
        return None
    if name in payload:
        return payload[name]
    for k in _CONTAINER_KEYS:
        if k in payload:
            hit = _find(payload[k], name, depth + 1)
            if hit is not None:
                return hit
    return None


def _reason(payload, *answers) -> str:
    for a in answers:
        if isinstance(a, dict):
            for k in ("reason", "rationale", "explanation"):
                if a.get(k):
                    return str(a[k])[:500]
    r = _find(payload, "reason")
    if isinstance(r, dict):
        r = _scalar(r) or r.get("text")
    if r:
        return str(r)[:500]
    if isinstance(payload, dict):
        for k in ("rationale", "explanation"):
            if payload.get(k):
                return str(payload[k])[:500]
    return DEFAULT_REASON


def parse_decision(payload) -> dict | None:
    """Normalised decision from any backend payload, or None when no route can be found."""
    if not isinstance(payload, (dict, list)):
        return None
    route_a = _find(payload, "route")
    if route_a is None or (isinstance(route_a, (dict, list)) and _scalar(route_a) is None):
        return None  # no answer (e.g. an echoed question): HTTP falls back to local, local fails closed
    unsafe_a = _find(payload, "unsafe")
    return {
        "route": normalize_route(route_a),
        "unsafe": normalize_unsafe(unsafe_a) if unsafe_a is not None else 0.5,
        "reason": _reason(payload, route_a, unsafe_a),
    }


class DecisionClient:
    name = "base"

    def decide(self, proposed: dict, state: dict) -> dict:  # pragma: no cover - interface
        raise NotImplementedError


class LocalDecision(DecisionClient):
    """The "gate" LLM role answers the typed questions as JSON. Fails closed to confirm."""

    name = "local"

    def __init__(self, log=None):
        self.log = log

    def messages(self, proposed: dict, state: dict) -> list[dict]:
        user = json.dumps({"proposed": proposed, "state": state}, sort_keys=True, default=str)
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]

    def decide(self, proposed: dict, state: dict) -> dict:
        try:
            resp = llm.chat("gate", self.messages(proposed, state), max_tokens=1024, thinking=False,
                            log=self.log, actor="gate", max_tries=3)
            content = resp if isinstance(resp, str) else llm.text(resp)
            parsed = extract_json(content)
            if not isinstance(parsed, dict):
                return _fail_closed("unparseable gate output")
            out = parse_decision(parsed)
            if out is None:
                return _fail_closed("gate output has no route")
            return out
        except Exception as e:  # noqa: BLE001 - the gate must never raise
            return _fail_closed(f"{type(e).__name__}: {e}")


class _HttpDecision(DecisionClient):
    url = ""
    env_key = ""

    def __init__(self, api_key: str | None = None, log=None):
        self.api_key = api_key if api_key is not None else os.environ.get(self.env_key, "")
        self.log = log
        self.local = LocalDecision(log)
        self.last_error: str | None = None

    def body(self, proposed: dict, state: dict) -> dict:  # pragma: no cover - overridden
        raise NotImplementedError

    def decide(self, proposed: dict, state: dict) -> dict:
        try:
            if not self.api_key:
                raise RuntimeError(f"{self.env_key} not set")
            body = json.loads(json.dumps(self.body(proposed, state), default=str))
            r = httpx.post(self.url, headers={"Authorization": f"Bearer {self.api_key}"},
                           json=body, timeout=30.0)
            r.raise_for_status()
            out = parse_decision(r.json())
            if out is None:
                raise ValueError("no route in decision response")
            self.last_error = None
            return out
        except Exception as e:  # noqa: BLE001 - fall back to the local gate
            self.last_error = f"{type(e).__name__}: {e}"[:300]
            return self.local.decide(proposed, state)


class JevDecision(_HttpDecision):
    name = "jev"
    url = "https://api.typesafe.ai/v1/systemone"
    env_key = "JEV_API_KEY"

    def body(self, proposed: dict, state: dict) -> dict:
        return {
            "model": "jev-latest",
            "state": {"proposed": proposed, "state": state, "policy": POLICY},
            "questions": {
                "route": {"type": "choice", "instructions": ROUTE_INSTRUCTIONS, "criteria": dict(ROUTE_CRITERIA)},
                "unsafe": {"type": "noul", "instructions": UNSAFE_INSTRUCTIONS},
            },
        }


class OpenAIDecision(_HttpDecision):
    name = "openai"
    url = "https://api.openai.com/v1/decisions"
    env_key = "OPENAI_API_KEY"

    def body(self, proposed: dict, state: dict) -> dict:
        return {
            "model": "gpt-6-luna",
            "state": {"proposed": proposed, "state": state, "policy": POLICY},
            "questions": [
                {"name": "route", "type": "choice", "instructions": ROUTE_INSTRUCTIONS,
                 "criteria": dict(ROUTE_CRITERIA)},
                {"name": "unsafe", "type": "score", "instructions": UNSAFE_INSTRUCTIONS},
            ],
        }


_BACKENDS = {"jev": JevDecision, "openai": OpenAIDecision}


def _num(v):
    try:
        if isinstance(v, list) and v:  # a series: take the latest value
            v = v[-1][-1] if isinstance(v[-1], (list, tuple)) else v[-1]
        return float(v)
    except (TypeError, ValueError, IndexError):
        return None


def fast_path(proposed: dict, state: dict) -> dict | None:
    """Policy rules 1 and 2 as code. Actions that only remove energy or output, and technician dispatch
    (which is a lockout check, not a judgement call), never need a model call."""
    if proposed.get("tool") == "dispatch_tech":
        needs_off = state.get("work_requires_deenergized")
        if needs_off is None:
            return None
        if not needs_off:
            return {"route": "proceed", "unsafe": 0.0, "reason": "policy rule 2: this work order does not need de-energized equipment"}
        brk = state.get("asset_breaker") or {}
        if brk and all(str(v).lower() in ("open", "tripped") for v in brk.values()):
            return {"route": "proceed", "unsafe": 0.0, "reason": f"policy rule 2: lockout verified, {', '.join(brk)} open"}
        if brk:
            return {"route": "reject", "unsafe": 0.9,
                    "reason": f"policy rule 2: work needs de-energized equipment; open {', '.join(brk)} first (lockout)"}
        return None
    if proposed.get("tool") != "operate":
        return None
    action, value = proposed.get("action"), _num(proposed.get("value"))
    m = re.fullmatch(r"52-I(\d+)", str(proposed.get("asset", "")))
    if action == "close" and m:  # policy rule 3 for an inverter feeder: deterministic on the alarms the gate can see
        n = m.group(1)
        on_feeder = re.compile(rf"^(INV-{n}|CB-{n}\.\d+|52-I{n})$")
        alarms = [a for a in (state.get("active_alarms") or []) if isinstance(a, dict) and on_feeder.match(str(a.get("asset", "")))]
        fault = [a for a in alarms if re.search(r"gfdi|ground|insulat|arc|short|earth", f"{a.get('code', '')} {a.get('text', '')}", re.I)]
        if fault:
            return {"route": "reject", "unsafe": 0.95,
                    "reason": f"policy rule 3: suspected fault on the feeder ({', '.join(str(a.get('code')) for a in fault)}); do not close"}
        if any(str(a.get("code", "")).upper().endswith("TRIP") for a in alarms):
            return None  # an unexplained protection trip is a judgement call for the model
        return {"route": "proceed", "unsafe": 0.0, "reason": f"policy rule 3: no fault or trip alarm on feeder {n}; closing is safe"}
    scada = state.get("asset_scada") or state.get("telemetry") or {}
    if action in ("open", "isolate"):
        return {"route": "proceed", "unsafe": 0.0, "reason": f"policy rule 1: '{action}' de-energizes equipment"}
    for act, point in (("set_plant_limit_kw", "plant_limit_kw"), ("set_curtail_pct", "curtail_pct")):
        if action == act and value is not None:
            cur = _num(scada.get(point)) if isinstance(scada, dict) else None
            if cur is not None and value <= cur:
                return {"route": "proceed", "unsafe": 0.0,
                        "reason": f"policy rule 1: lowers {point} from {cur:g} to {value:g}, which only reduces output"}
    return None


class Gate:
    """Callable gate(proposed, state) with `.backend` (effective backend name) and `.client`."""

    def __init__(self, client: DecisionClient, backend: str):
        self.client = client
        self.backend = backend

    def __call__(self, proposed, state) -> dict:
        try:
            fast = fast_path(proposed if isinstance(proposed, dict) else {}, state if isinstance(state, dict) else {})
            if fast is not None:
                return fast
            out = self.client.decide(proposed if isinstance(proposed, dict) else {"action": proposed},
                                     state if isinstance(state, dict) else {"state": state})
            if not isinstance(out, dict) or out.get("route") not in ROUTES:
                return _fail_closed("malformed decision")
            return out
        except Exception as e:  # noqa: BLE001
            return _fail_closed(f"{type(e).__name__}: {e}")


def make_gate(backend: str | None = None, log=None) -> Gate:
    try:
        name = str(backend or os.environ.get("DECISION_BACKEND") or "local").strip().lower()
        cls = _BACKENDS.get(name)
        if cls is not None and os.environ.get(cls.env_key):
            return Gate(cls(log=log), cls.name)
    except Exception:  # noqa: BLE001 - never raise; use the local gate
        pass
    return Gate(LocalDecision(log), "local")

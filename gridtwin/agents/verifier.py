"""Verifier: proves or refutes a claimed outcome by re-running the DoD itself.

Isolation is enforced here, in code: a Verifier is built from the raw Toolbox, the event log
(read only by deterministic code, never put in a prompt), the KB refs and the plant registry.
`verify()` takes the goal, the outcome name and the DoD checks, nothing else. Checks pass through
`dod.sanitize_check` first, so free text written by anyone else never reaches the verifier model.
It runs on a different model family (gpt-oss-120b) from the workers.

Visual checks use a viewpoint the verifier chooses: a named viewpoint of the same asset that no
worker called during the run, so workers can't cherry-pick the evidence. The log is read only for
which viewpoints were already called, and the pick always comes from the plant registry.
"""
from __future__ import annotations

import json

from gridtwin import llm
from gridtwin.agents import dod as D
from gridtwin.agents.common import ActorToolbox, extract_json, read_events

SYSTEM = """Reasoning: medium
You are GridTwin's independent verifier for an autonomous solar-plant operations team. You never see what the team said or wrote. You see only the operator's goal, the definition of done (DoD) for the outcome being verified, and raw plant data you gather yourself.

Your jobs:
1. Audit: decide whether the DoD's checks actually prove the outcome for this goal. If an important part of the goal is not covered, add checks in the DoD language. You may add checks; you may never remove or weaken one.
2. Judge evidence: when asked whether raw evidence (a vision model's reading of a fresh photo, or a cached reference text) establishes a specific claim, answer strictly. Absent, vague or contradictory evidence is a fail.

The DoD language:
{schema}

Always reply with only JSON."""

AUDIT_REQUEST = """AUDIT
Goal: {goal}
Outcome being verified: {outcome}
Plant registry:
{registry}
Checks:
{checks}

Do these checks prove the outcome for this goal (fresh data, independent sources, the business metric, safety)? Add at most 3 checks only where coverage is missing, using asset ids, points and viewpoints from the registry. Reply with only JSON: {{"add": [...], "reason": "..."}}"""

VISUAL_REQUEST = """EVIDENCE: VISUAL
Claim to verify: {claim}
A vision model looked at a photo taken just now from viewpoint {viewpoint} ({mode}) and returned:
pass={vpass} confidence={conf}
evidence: {evidence}
Does this evidence establish the claim? Reply with only JSON: {{"pass": true|false, "reason": "..."}}"""

CITATION_REQUEST = """EVIDENCE: CITATION
Action: {action}
Source URL: {url}
Quoted text (found verbatim in the cached source): {quote}
Cached source text: {text}
Does this source support taking this action? Reply with only JSON: {{"pass": true|false, "reason": "..."}}"""


def system_prompt() -> str:
    return SYSTEM.format(schema=D.schema_text())


class Verifier:
    def __init__(self, toolbox, log, kb_dir=None, registry: dict | None = None):
        self.tb = ActorToolbox(toolbox, "verifier")
        self.log = log
        self.kb_dir = kb_dir
        self.registry = registry
        self.messages = [{"role": "system", "content": system_prompt()}]
        self.audited: set[str] = set()
        self.own_camera_seqs: set = set()  # the sim logs every call as actor "sim", so own calls are told apart by seq

    # ----- model -----
    def _ask(self, content: str, max_tokens: int = 4096) -> dict:
        self.messages.append({"role": "user", "content": content})
        try:
            resp = llm.chat("verifier", self.messages, max_tokens=max_tokens, log=self.log, actor="verifier")
            out = llm.text(resp)
        except Exception as e:  # the verifier fails closed
            out = json.dumps({"pass": False, "reason": f"verifier model unavailable: {type(e).__name__}"})
        self.messages.append({"role": "assistant", "content": out})
        d = extract_json(out)
        return d if isinstance(d, dict) else {}

    def _registry(self) -> dict:
        if self.registry is None:
            r = self.tb.call("list_assets", {})
            self.registry = r if isinstance(r, dict) and "error" not in r else {}
        return self.registry

    def _registry_text(self) -> str:
        from gridtwin.agents.planner import compact_registry

        return compact_registry(self._registry())

    # ----- viewpoint choice for visual checks -----
    def _called_viewpoints(self) -> set[str]:
        """Viewpoints anyone but this verifier has called get_camera_view on so far in the run."""
        out = set()
        for e in read_events(self.log):
            d = e.get("data") or {}
            if e.get("type") == "tool_call" and d.get("name") == "get_camera_view" and e.get("seq") not in self.own_camera_seqs:
                out.add(str((d.get("args") or {}).get("viewpoint")))
        return out

    def _pick_viewpoints(self, checks: list[dict]) -> tuple[list[dict], dict[int, dict]]:
        """Point each visual check at a viewpoint of its asset that no worker called: the DoD's own if it's
        unused, else the first unused one by name. If every viewpoint was called, keep the DoD's and flag it.
        Returns the checks and {index: notes for that verdict's evidence}."""
        if not any(c.get("kind") == "visual" for c in checks):
            return checks, {}
        index, called = viewpoint_index(self._registry()), self._called_viewpoints()
        out, notes = [], {}
        for i, c in enumerate(checks):
            vp = c.get("viewpoint")
            if c.get("kind") != "visual" or vp not in index:
                out.append(c)
                continue
            mode = c.get("mode") or "rgb"
            asset = index[vp]["asset"]
            same = sorted(v for v, d in index.items() if v != vp and d["asset"] == asset and (d["modes"] is None or mode in d["modes"]))
            cands = [vp] + same
            free = [v for v in cands if v not in called]
            notes[i] = {"dod_viewpoint": vp, "worker_viewpoints": [v for v in cands if v in called]}
            if not free:
                notes[i]["viewpoint_fallback"] = "every viewpoint of this asset was called by a worker"
            out.append(dict(c, viewpoint=free[0] if free else vp))
        return out, notes

    # ----- evidence judges (wrap the deterministic checks in dod.py) -----
    def _visual(self, image_path: str, claim: str, viewpoint: str = "", mode: str = "") -> dict:
        res = _vision_check(image_path, claim)
        vpass, conf = bool(res.get("pass")), float(res.get("confidence") or 0.0)
        out = {"pass": vpass, "confidence": conf, "evidence": res.get("evidence", "")}
        if not vpass or conf < 0.7:
            return out
        j = self._ask(VISUAL_REQUEST.format(claim=claim, viewpoint=viewpoint, mode=mode, vpass=vpass, conf=round(conf, 2),
                                            evidence=str(res.get("evidence", ""))[:800]), max_tokens=2048)
        out["verifier"] = j.get("reason", "")
        out["pass"] = bool(j.get("pass"))
        return out

    def _citation(self, check: dict, kb_dir) -> dict:
        base = D.check_citation(check, kb_dir)
        if not base.get("pass"):
            return base
        text = ""
        ev = base.get("evidence")
        if isinstance(ev, dict):
            text = str(ev.get("text") or ev.get("snippet") or "")
        j = self._ask(CITATION_REQUEST.format(action=check.get("action", ""), url=check.get("url", ""), quote=check.get("quote", ""),
                                              text=text[:1500]), max_tokens=2048)
        return {"pass": bool(j.get("pass")), "evidence": {"cache": ev, "verifier": j.get("reason", "")}}

    # ----- main entry -----
    def audit(self, goal: str, outcome: str, checks: list[dict]) -> list[dict]:
        clean = [D.sanitize_check(c) for c in checks]
        j = self._ask(AUDIT_REQUEST.format(goal=goal, outcome=outcome, registry=self._registry_text(),
                                           checks=json.dumps(clean, sort_keys=True, indent=1)))
        added = []
        for i, c in enumerate(j.get("add") or []):
            if not isinstance(c, dict):
                continue
            c = dict(c, id=f"v_{outcome.lower()}_{len(clean) + i + 1}")
            if not D.validate_check(c):
                added.append(D.sanitize_check(c))
            if len(added) >= 3:
                break
        return added

    def verify(self, goal: str, outcome: str, checks: list[dict], audit: bool = True) -> dict:
        """Returns {"pass", "verdicts", "added", "checks", "fraction"}."""
        clean = [D.sanitize_check(c) for c in checks]
        added = []
        if audit and outcome not in self.audited:
            self.audited.add(outcome)
            added = self.audit(goal, outcome, clean)
            clean = clean + added
        run, notes = self._pick_viewpoints(clean)
        viewpoint_of = {c.get("claim"): (c.get("viewpoint", ""), c.get("mode", "rgb")) for c in run if c.get("kind") == "visual"}

        def visual_fn(image_path, claim):
            return self._visual(image_path, claim, *viewpoint_of.get(claim, ("", "")))

        n0 = len(read_events(self.log))
        verdicts = D.evaluate(run, self.tb, self.log, kb_dir=self.kb_dir, visual_fn=visual_fn,
                              citation_fn=self._citation, actor="verifier", emit=False)
        self.own_camera_seqs |= {e.get("seq") for e in read_events(self.log)[n0:]
                                 if e.get("type") == "tool_call" and (e.get("data") or {}).get("name") == "get_camera_view"}
        for i, v in enumerate(verdicts):
            if i in notes and isinstance(v.get("evidence"), dict):
                v["evidence"].update(notes[i])  # evidence.viewpoint is the one the photo came from
            _emit_verdict(self.log, v)
        n_pass = sum(1 for v in verdicts if v["pass"])
        return {"pass": bool(verdicts) and n_pass == len(verdicts), "verdicts": verdicts, "added": added,
                "checks": clean, "fraction": n_pass / max(1, len(verdicts))}


def viewpoint_index(registry) -> dict[str, dict]:
    """{viewpoint: {"asset", "modes"}} from a list_assets result: the sim lists viewpoint names per asset
    (no modes), the test stubs a top-level [{id, asset, modes}]. modes is None when the registry doesn't say."""
    reg = registry if isinstance(registry, dict) else {}
    out = {}
    for a in reg.get("assets") or []:
        if isinstance(a, dict):
            for v in a.get("viewpoints") or []:
                if isinstance(v, str):
                    out[v] = {"asset": a.get("asset") or a.get("id"), "modes": None}
    for v in reg.get("viewpoints") or []:
        if isinstance(v, dict) and v.get("id"):
            out[str(v["id"])] = {"asset": v.get("asset"), "modes": v.get("modes")}
    return out


def _emit_verdict(log, v: dict) -> None:
    """Same event dod.evaluate emits, sent after the viewpoint notes are added to the evidence."""
    if log is not None and callable(getattr(log, "emit", None)):
        try:
            log.emit("verifier", "verdict", dict(v))
        except Exception:
            pass


def _vision_check(image_path: str, claim: str) -> dict:
    return D._default_visual_fn()(image_path, claim)  # gridtwin.vision.check (workstream 3) or the local fallback

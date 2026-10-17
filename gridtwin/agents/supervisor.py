"""Supervisor: decides continue | replan | stop | page each round.

Hard rules first (loops, flat progress, repeated rejections, budget), then an LLM drift judge
(Crusoe serves chat completions only, so drift is judged by a model, not embeddings).
"""
from __future__ import annotations

import json
from collections import Counter

from gridtwin import llm
from gridtwin.agents.common import extract_json, last_t_sim, minutes_between, read_events

SYSTEM = """You are the supervisor of GridTwin's autonomous plant-operations team. Each round you see the operator's goal and the team's recent tool actions. Judge drift: do the recent actions still serve the goal, or has the team wandered into unrelated work, busywork or repetition? Be brief. Reply with only JSON: {"on_track": true|false, "reason": "..."}"""

DRIFT_REQUEST = """Goal: {goal}
Round: {round}
Recent actions (oldest first):
{actions}
Latest board posts:
{board}"""

LOOP_N = 3          # same action hash this many times -> replan
FLAT_ROUNDS = 3     # check-pass fraction flat for this many verifications -> replan
REJECT_N = 2        # verifier rejects the same check this many times -> page
BUDGET_FRAC = 0.8   # this fraction of any budget used -> stop or page


class Supervisor:
    def __init__(self, goal: str, budget: dict, log, toolbox, usd_budget: float = 2.0):
        self.goal, self.log, self.toolbox = goal, log, toolbox
        self.budget = dict(budget or {})
        self.budget.setdefault("usd", usd_budget)
        self.progress: list[float] = []
        self.rejections: Counter = Counter()
        self.flagged: set[str] = set()
        self.last_drift_round = -9
        self.messages = [{"role": "system", "content": SYSTEM}]
        evs = read_events(log)
        self.t0 = next((e["t_sim"] for e in evs if e.get("t_sim")), None)
        self.calls0 = self._calls_now()

    def emit(self, decision: str, reason: str):
        self.log.emit("supervisor", "supervisor", {"decision": decision, "reason": reason})

    # ----- budget -----
    def _calls_now(self) -> int:
        n = getattr(self.toolbox, "calls", None)
        if isinstance(n, int):
            return n
        return sum(1 for e in read_events(self.log) if e.get("type") == "tool_call")

    def used(self) -> dict:
        out = {}
        if self.budget.get("tool_calls"):
            out["tool_calls"] = self._calls_now() / float(self.budget["tool_calls"])
        if self.budget.get("sim_minutes") and self.t0:
            m = minutes_between(self.t0, last_t_sim(self.log)) or 0.0
            out["sim_minutes"] = m / float(self.budget["sim_minutes"])
        if self.budget.get("usd"):
            out["usd"] = llm.USAGE.usd() / float(self.budget["usd"])
        return out

    def calls_left(self) -> int | None:
        if not self.budget.get("tool_calls"):
            return None
        return int(self.budget["tool_calls"]) - self._calls_now()

    # ----- bookkeeping -----
    def note_verification(self, result: dict):
        self.progress.append(round(result.get("fraction", 0.0), 3))
        for v in result.get("verdicts", []):
            if not v["pass"]:
                self.rejections[v["check_id"]] += 1

    # ----- decision -----
    def decide(self, round_no: int, actions: list[tuple[str, str]], board_posts: list[dict], drift: bool = True) -> tuple[str, str]:
        used = self.used()
        hot = {k: v for k, v in used.items() if v >= BUDGET_FRAC}
        if hot:
            d = ("page", f"budget {', '.join(f'{k} {v:.0%}' for k, v in hot.items())} used without a verified outcome")
            self.emit(*d)
            return d
        repeated = [cid for cid, n in self.rejections.items() if n >= REJECT_N]
        if repeated:
            d = ("page", f"verifier rejected the same check {REJECT_N}+ times: {', '.join(sorted(repeated))}")
            self.emit(*d)
            return d
        counts = Counter(f"{n}:{a}" for n, a in actions if n not in ("read_board",))
        loops = [h for h, n in counts.items() if n >= LOOP_N and f"{h}#{n // LOOP_N}" not in self.flagged]
        if loops:
            for h in loops:
                self.flagged.add(f"{h}#{counts[h] // LOOP_N}")
            d = ("replan", f"loop: the same action repeated {LOOP_N}+ times: {loops[0][:160]}")
            self.emit(*d)
            return d
        if len(self.progress) >= FLAT_ROUNDS and len(set(self.progress[-FLAT_ROUNDS:])) == 1 and self.progress[-1] < 1.0:
            d = ("replan", f"no progress: check-pass fraction flat at {self.progress[-1]:.0%} for {FLAT_ROUNDS} verifications")
            self.emit(*d)
            return d
        if drift and actions and round_no - self.last_drift_round >= 2:  # at most every other round
            on_track, reason = self.judge_drift(round_no, actions, board_posts)
            if not on_track:
                self.last_drift_round = round_no
                d = ("replan", f"drift: {reason}")
                self.emit(*d)
                return d
        d = ("continue", "rules clear" + (f"; budget used {json.dumps({k: round(v, 2) for k, v in used.items()})}" if used else ""))
        self.emit(*d)
        return d

    def judge_drift(self, round_no: int, actions, board_posts) -> tuple[bool, str]:
        atxt = "\n".join(f"- {n} {a[:160]}" for n, a in actions[-12:])
        btxt = "\n".join(f"- [{p['worker']}] {p['text'][:200]}" for p in board_posts[-6:]) or "(none)"
        self.messages.append({"role": "user", "content": DRIFT_REQUEST.format(goal=self.goal, round=round_no, actions=atxt, board=btxt)})
        try:
            resp = llm.chat("supervisor", self.messages, max_tokens=2048, thinking=False, log=self.log, actor="supervisor")
            out = llm.text(resp)
        except Exception as e:
            out = json.dumps({"on_track": True, "reason": f"drift judge unavailable ({type(e).__name__})"})
        self.messages.append({"role": "assistant", "content": out})
        d = extract_json(out)
        if not isinstance(d, dict):
            return True, "unparseable drift verdict"
        return bool(d.get("on_track", True)), str(d.get("reason", ""))[:300]

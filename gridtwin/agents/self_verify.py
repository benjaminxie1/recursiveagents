"""Self-verify baseline (docs/JUDGE_QA.md Q1): the baseline's workers, told to check their own fix
before claiming, with the scenario's whole tool-call budget (the same one the full loop gets).

No DoD, no independent verifier, no hold enforced in code. If this arm closes most of the gap,
the full loop's gain is just extra compute; if it doesn't, the gain is the independent proof.
"""
from __future__ import annotations

from gridtwin.agents import baseline

SELF_CHECK = ("diagnoser", "Before anyone claims the goal is met, check the work yourselves: wait at least 30 sim-minutes, "
              "then re-read the affected assets and the POI-1 meter, confirm every reading is current (last_update_ts is recent), "
              "that the inverters add up to what POI-1 exports, and look at the cameras again. Post whether it held, with seqs.")
TASKS = [*baseline.TASKS, SELF_CHECK]


def run(goal: str, toolbox, log, budget: dict, kb_dir) -> dict:
    cap = int((budget or {}).get("tool_calls") or baseline.STEP_CAP * 2)
    return baseline.run(goal, toolbox, log, budget, kb_dir, step_cap=cap, tasks=TASKS)

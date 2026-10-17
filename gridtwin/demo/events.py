"""Demo event types on the shared run log (contract C2 plus the demo.* types in CONTRACT.md).

The sim's EventLog validates actors and types against module-level sets, so the demo registers its own
names there at import instead of editing the sim. Emits are serialised with a lock because sub-agents run
in threads.
"""
from __future__ import annotations

import threading

from gridtwin.sim import events as _sim_events

DEMO_TYPES = {
    "demo.stage", "demo.sensor", "demo.camera", "demo.diagnosis", "demo.agent", "demo.workorder", "demo.email",
    "demo.contractor", "demo.check", "demo.jev", "demo.diffusion", "demo.verdict", "demo.memory", "demo.final",
    "demo.twin",
}
SUBAGENT_ROLES = ("safety", "repair", "acceptance")  # the planner's team
# Every sub-agent role any team may spawn (diagnosis, plan, verification); actors are "subagent:<role>".
ALL_SUBAGENT_ROLES = SUBAGENT_ROLES + (
    "sensors", "vision", "spatial", "history",                  # diagnosis team
    "telemetry_judge", "visual_judge", "jev_judge", "meta_judge",  # verification team
)
DEMO_ACTORS = {"orchestrator", "diagnoser", "contractor", "memory"} | {f"subagent:{r}" for r in ALL_SUBAGENT_ROLES}

_sim_events.TYPES.update(DEMO_TYPES)
_sim_events.ACTORS_FIXED.update(DEMO_ACTORS)


class LockedLog:
    """Thread-safe proxy for an EventLog: same emit() and attributes, one writer at a time."""

    def __init__(self, log):
        self._log = log
        self._lock = threading.RLock()

    def emit(self, actor: str, type: str, data: dict) -> dict:
        with self._lock:
            return self._log.emit(actor, type, data)

    def __getattr__(self, item):
        return getattr(self._log, item)

"""Append-only run log (contract C2): one JSON line per event in runs/<run_id>/events.jsonl."""
import json
import time
from pathlib import Path

ACTORS_FIXED = {"operator", "planner", "verifier", "supervisor", "gate", "sim", "judge"}
TYPES = {
    "goal", "dod", "tool_call", "tool_result", "gate", "claim", "verdict",
    "supervisor", "page", "lesson", "final", "judge", "usage", "finding",
}


class EventLog:
    def __init__(self, run_dir, clock=None, on_emit=None):
        """`clock` is a zero-arg callable returning the sim time string for `t_sim`.
        `on_emit(event)` runs after each event is written (episode uses it for truth snapshots)."""
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "events.jsonl"
        self.path.touch()
        self.clock = clock
        self.seq = 0
        self.events = []
        self.on_emit = on_emit

    def emit(self, actor: str, type: str, data: dict) -> dict:
        if actor not in ACTORS_FIXED and not actor.startswith("worker:"):
            raise ValueError(f"unknown actor {actor!r}")
        if type not in TYPES:
            raise ValueError(f"unknown event type {type!r}")
        self.seq += 1
        event = {
            "seq": self.seq,
            "t_sim": self.clock() if self.clock else None,
            "wall_ts": time.time(),
            "actor": actor,
            "type": type,
            "data": data,
        }
        with self.path.open("a") as f:
            f.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        self.events.append(event)
        if self.on_emit:
            self.on_emit(event)
        return event

    def of_type(self, type: str) -> list:
        return [e for e in self.events if e["type"] == type]


def load(run_dir) -> list:
    path = Path(run_dir) / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

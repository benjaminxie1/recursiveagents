"""Shared findings board for one run (C7). In memory only; every post is a `finding` event."""
from __future__ import annotations

import copy

MAX_TEXT = 1000

BOARD_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "post_finding",
            "description": (
                "Post a finding to the team board so the other workers and the planner can see it. "
                "Post every finding you make, and always include the event seq numbers of the tool "
                "results that back it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The finding, one or two sentences."},
                    "evidence": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Event seq numbers of the tool results that back this finding.",
                    },
                },
                "required": ["text", "evidence"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_board",
            "description": (
                "Read every finding posted so far by all workers. Read the board before starting a new "
                "line of investigation so you do not repeat work someone else already did."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

BOARD_TOOL_NAMES = frozenset(t["function"]["name"] for t in BOARD_TOOLS)


def _coerce_evidence(evidence) -> list[int]:
    if evidence is None:
        return []
    if isinstance(evidence, (str, int, float)) and not isinstance(evidence, bool):
        evidence = [evidence]
    out = []
    try:
        items = list(evidence)
    except TypeError:
        return []
    for e in items:
        if isinstance(e, bool):
            continue
        if isinstance(e, int):
            out.append(e)
        elif isinstance(e, float) and e.is_integer():
            out.append(int(e))
        elif isinstance(e, str) and e.strip().lstrip("#").isdigit():
            out.append(int(e.strip().lstrip("#")))
    return out


class Board:
    def __init__(self, log):
        self.log = log
        self._posts: list[dict] = []

    def post(self, worker: str, text, evidence) -> dict:
        text = str(text if text is not None else "")[:MAX_TEXT]
        ev_list = _coerce_evidence(evidence)
        ev = None
        if self.log is not None:
            ev = self.log.emit(f"worker:{worker}", "finding",
                               {"worker": worker, "text": text, "evidence": list(ev_list)})
        ev = ev if isinstance(ev, dict) else {}
        p = {"id": len(self._posts) + 1, "worker": worker, "text": text, "evidence": ev_list,
             "seq": ev.get("seq"), "t_sim": ev.get("t_sim")}
        self._posts.append(p)
        return copy.deepcopy(p)

    def read(self) -> list[dict]:
        return copy.deepcopy(self._posts)

    def handle(self, worker: str, name: str, args) -> dict | None:
        if name not in BOARD_TOOL_NAMES:
            return None
        args = args if isinstance(args, dict) else {}
        if name == "read_board":
            return {"posts": self.read()}
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            return {"error": "post_finding needs non-empty text"}
        try:  # board tools sit on the worker's tool path, so like the Toolbox they never raise
            return self.post(worker, text, args.get("evidence"))
        except Exception as e:  # noqa: BLE001
            return {"error": f"post_finding failed: {type(e).__name__}: {e}"[:300]}

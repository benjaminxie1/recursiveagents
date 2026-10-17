"""Memory flywheel for the demo: verified outcomes become lessons; the next run's planner recalls them.

Built on gridtwin.kb.KB (same lesson files and BM25 search as the full loop), in a separate directory
(default runs/demo-kb) so the demo never writes into Jonathan's kb/. Only a run whose last verdict passed
writes a lesson, so the team learns the fix and never the fake fix.
"""
from __future__ import annotations

from pathlib import Path

from gridtwin.kb import KB
from gridtwin.sim.camera import REPO

DEFAULT_DIR = REPO / "runs" / "demo-kb"


class Memory:
    def __init__(self, kb_dir=None):
        self.dir = Path(kb_dir) if kb_dir else DEFAULT_DIR
        (self.dir / "lessons").mkdir(parents=True, exist_ok=True)
        self.kb = KB(self.dir, log=None)  # demo.memory events carry the story

    def recall(self, query: str, k: int = 3) -> list[dict]:
        """Full lesson dicts for the best matches, best first."""
        by_id = {l["id"]: l for l in self.kb.lessons()}
        hits = self.kb.search(query, k=k, kinds=("lessons",), actor="planner")
        return [by_id[h["id"]] for h in hits if h["id"] in by_id]

    def write(self, lesson: dict) -> dict:
        return self.kb.write_lesson(lesson, actor="planner")


def summary(lesson: dict) -> str:
    fix = str(lesson.get("fix") or "")
    forbid = "; ".join(lesson.get("forbid") or [])
    syms = "; ".join((lesson.get("symptoms") or [])[:3])
    return f"{lesson.get('id')}: when {syms}, fix = {fix}" + (f"; do not: {forbid}" if forbid else "")

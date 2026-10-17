"""Memory flywheel for the demo: verified outcomes become lessons; every level of the next run recalls its slice.

Built on gridtwin.kb.KB (same lesson files, same supersede rule) in a separate directory (default runs/e2e-kb) so
the demo never writes into Jonathan's kb/. Three things turn the store into a flywheel:

- Role-aware lessons. Each lesson carries `roles` and a per-role `hints` dict, so the diagnosis team, the planner,
  each planner sub-agent (safety, repair, acceptance), the verifier and the contractor dispatch get only their slice.
- Outcome scoring. After every run, record_outcome() scores the lessons that run applied: a first-round pass gains,
  a run that still failed loses and flags the lesson. recall() ranks by BM25 x score and drops lessons that keep
  failing. Scores and the run ledger live in <kb_dir>/flywheel.json, beside the lesson files.
- Done means done. write() refuses a lesson from a run whose last verdict did not pass, so the team learns the fix
  and never the fake fix. Contractor history (shortcuts taken, rounds needed) comes from the same ledger.
"""
from __future__ import annotations

import copy
import fcntl
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from gridtwin.demo.contractors import SHORTCUT_WORDS, contractor_for
from gridtwin.kb import BM25, KB, tokenize
from gridtwin.sim.camera import REPO

DEFAULT_DIR = REPO / "runs" / "e2e-kb"
LEDGER = "flywheel.json"

ROLES = ("diagnosis", "planner", "safety", "repair", "acceptance", "verify", "contractor")
# Team members and actors that read another role's slice (events.ALL_SUBAGENT_ROLES and the demo actors).
ROLE_ALIASES = {
    "diagnoser": "diagnosis", "sensors": "diagnosis", "vision": "diagnosis", "spatial": "diagnosis", "history": "diagnosis",
    "verifier": "verify", "telemetry_judge": "verify", "visual_judge": "verify", "jev_judge": "verify",
    "meta_judge": "verify", "dispatch": "contractor",
}

PRIOR = 1.0          # a new lesson's score
WIN, PARTIAL = 1.0, 0.5
QUARANTINE = 0.5     # recall drops a lesson whose score falls below this (three straight failures)
CHECK_WORDS = {"telemetry": "SCADA freshness", "visual": "a camera the contractor did not use", "jev": "the JEV typed decision"}

_LOCK = threading.Lock()


class UnverifiedLesson(ValueError):
    """Raised when something tries to learn from a run that did not verify."""


def role_of(role: str | None) -> str | None:
    """Canonical role for a role, team member or actor name ("subagent:repair" -> "repair")."""
    if not role:
        return None
    r = str(role).lower().removeprefix("subagent:")
    return ROLE_ALIASES.get(r, r)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _uniq(items) -> list[str]:
    return list(dict.fromkeys(str(s) for s in items if s))


def score_of(entry: dict | None) -> float:
    """Smoothed win rate scaled so a new lesson is 1.0: 2 * (wins + 1) / (uses + 2)."""
    if not entry:
        return PRIOR
    return round(2 * (float(entry.get("wins", 0)) + 1) / (int(entry.get("uses", 0)) + 2), 4)


# ---------- the ledger: lesson scores and one entry per run, beside the lesson files ----------

class Ledger:
    def __init__(self, kb_dir: Path):
        self.path = Path(kb_dir) / LEDGER

    def read(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        d.setdefault("runs", [])
        d.setdefault("scores", {})
        return d

    @contextmanager
    def update(self):
        """Read-modify-write under a process lock and a file lock; the write is atomic."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            d = self.read()
            yield d
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, indent=2, default=str))
            os.replace(tmp, self.path)


# ---------- the store ----------

class Memory:
    def __init__(self, kb_dir=None):
        self.dir = Path(kb_dir) if kb_dir else DEFAULT_DIR
        (self.dir / "lessons").mkdir(parents=True, exist_ok=True)
        self.kb = KB(self.dir, log=None)  # demo.memory events carry the story
        self.ledger = Ledger(self.dir)

    def lessons(self) -> list[dict]:
        return self.kb.lessons()

    def recall(self, query: str, role: str | None = None, k: int = 3) -> list[dict]:
        """Full lesson dicts for the best matches, best first, ranked by BM25 x outcome score.

        With a role, only lessons that carry a hint for that role (or predate roles) are candidates. Lessons that
        kept failing (score under QUARANTINE) are never returned. Each result has a `recall` dict
        {bm25, score, rank, flagged} the page can show.
        """
        if isinstance(role, int) and not isinstance(role, bool):  # recall(query, 5): the old positional k
            role, k = None, role
        role = role_of(role)
        scores = self.ledger.read()["scores"]
        cands = [l for l in self.kb.lessons()
                 if (role is None or not l.get("roles") or role in l["roles"]) and score_of(scores.get(l["id"])) >= QUARANTINE]
        q = tokenize(query)
        if not cands or not q:
            return []
        bm = BM25([tokenize(_index_text(l)) for l in cands]).scores(q)
        ranked = []
        for i, l in enumerate(cands):
            if bm[i] <= 0:
                continue
            s = score_of(scores.get(l["id"]))
            ranked.append((bm[i] * s, str(l.get("created_at") or ""), i, s, bm[i]))
        ranked.sort(key=lambda r: (-r[0], r[1], r[2]))
        out = []
        for rank, (_, _, i, s, b) in enumerate(ranked[: max(0, int(k))], 1):
            l = dict(cands[i])
            l["recall"] = {"bm25": round(b, 4), "score": s, "rank": rank,
                           "flagged": bool((scores.get(l["id"]) or {}).get("flagged"))}
            out.append(l)
        return out

    def write(self, lesson: dict) -> dict:
        """Write a lesson from a verified run. Raises UnverifiedLesson otherwise (never learn the fake fix)."""
        if lesson.get("verified") is not True:
            raise UnverifiedLesson(f"refusing to write {lesson.get('id')!r}: its run did not verify")
        clean = {k: v for k, v in lesson.items() if k != "recall"}
        return self.kb.write_lesson(clean, actor="planner")

    def learn(self, run_id, diagnosis, work_order, verdicts, visits) -> dict | None:
        """lesson_from_run + write, or None when the run did not verify."""
        lesson = lesson_from_run(run_id, diagnosis, work_order, verdicts, visits)
        return self.write(lesson) if lesson["verified"] else None

    def record_outcome(self, lesson_ids, run_id, rounds, verified, contractor: str | None = None,
                       visits: list[dict] | None = None) -> list[dict]:
        """Score the lessons a run applied and add the run to the ledger. Idempotent per run_id.

        Applied and verified in round 1: a win. Verified in a later round: half a win (the lesson did not prevent
        the redo). Not verified: a loss, and the lesson is flagged. Returns [{id, score, delta, flagged}].
        """
        ids = _uniq(lesson_ids or [])
        rounds, verified = int(rounds), bool(verified)
        shortcut = next((v for v in visits or [] if v.get("shortcut")), None)
        with self.ledger.update() as d:
            prior = next((r for r in d["runs"] if r["run_id"] == str(run_id)), None)
            if prior is not None:
                return [{"id": i, "score": score_of(d["scores"].get(i)), "delta": 0.0,
                         "flagged": bool((d["scores"].get(i) or {}).get("flagged"))} for i in prior["lessons_applied"]]
            out = []
            for i in ids:
                e = d["scores"].setdefault(i, {"uses": 0, "wins": 0.0, "losses": 0, "flagged": False, "history": []})
                before = score_of(e)
                e["uses"] += 1
                if not verified:
                    e["losses"] += 1
                    e["flagged"] = True
                    result = "failed"
                elif rounds <= 1:
                    e["wins"] += WIN
                    result = "won"
                else:
                    e["wins"] += PARTIAL
                    result = "partial"
                e["history"].append({"run_id": str(run_id), "rounds": rounds, "verified": verified, "result": result})
                e["score"] = score_of(e)
                out.append({"id": i, "score": e["score"], "delta": round(e["score"] - before, 4), "flagged": e["flagged"]})
            d["runs"].append({
                "run_id": str(run_id), "rounds": rounds, "verified": verified, "lessons_applied": ids,
                "contractor": contractor, "shortcut": bool(shortcut),
                "shortcut_note": (shortcut or {}).get("note"), "at": _now()})
            return out

    def contractor_notes(self, contractor_name: str) -> list[str]:
        """What past runs say about this contractor: shortcuts taken, rounds needed. Oldest first."""
        name = str(contractor_name or "").strip().lower()
        notes = []
        for r in self.ledger.read()["runs"]:
            if not name or str(r.get("contractor") or "").strip().lower() != name:
                continue
            done = f"verified in round {r['rounds']}" if r.get("verified") else f"not verified after {r['rounds']} rounds"
            if r.get("shortcut"):
                notes.append(f"run {r['run_id']}: this contractor took the inspect-only shortcut on the first visit "
                             f"(power-cycled the display and left); {done}")
            else:
                notes.append(f"run {r['run_id']}: followed the work order; {done}")
        return notes

    def apply_to_work_order(self, wo: dict, lessons: list[dict], contractor_name: str) -> tuple[dict, list[str]]:
        return apply_to_work_order(wo, lessons, contractor_name, self)

    def stats(self) -> dict:
        return stats(self.dir)

    def stats_event(self) -> dict:
        return stats_event(self.dir)


# ---------- lessons from runs ----------

def _index_text(l: dict) -> str:
    """What recall searches: the KB's lesson text plus the role hints, the shortcut and the checks that caught it."""
    hints = l.get("hints") if isinstance(l.get("hints"), dict) else {}
    parts = [str(l.get("goal") or ""), " ".join(map(str, l.get("symptoms") or [])), str(l.get("fix") or ""),
             " ".join(map(str, l.get("steps") or [])), str(l.get("outcome") or ""), " ".join(map(str, hints.values())),
             str(l.get("shortcut") or ""), " ".join(map(str, l.get("caught_by") or []))]
    return "\n".join(p for p in parts if p)


def _caught(verdicts: list[dict], visits: list[dict]) -> list[dict]:
    """The failed checks of the verdict that followed the shortcut visit (or of the first failed verdict)."""
    idx = next((i for i, v in enumerate(visits) if v.get("shortcut")), None)
    failed = [v for v in verdicts if not v.get("pass")]
    after_shortcut = verdicts[idx] if idx is not None and idx < len(verdicts) and not verdicts[idx].get("pass") else None
    v = after_shortcut or (failed[0] if failed else None)
    if not v:
        return []
    checks = [c for c in v.get("checks") or [] if not c.get("pass")]
    return checks or [{"name": n, "kind": ""} for n in v.get("failed") or []]


def lesson_from_run(run_id, diagnosis: dict, work_order: dict, verdicts: list[dict], visits: list[dict]) -> dict:
    """The lesson a run teaches: symptoms, fix, the shortcut that failed, the check that caught it, rounds, and one
    hint per role. `verified` is True only when the last verdict passed; write() refuses anything else."""
    dx, wo = diagnosis or {}, work_order or {}
    asset, wo_type = wo.get("asset") or dx.get("asset"), str(wo.get("work_order") or "")
    rounds = len(verdicts)
    verified = bool(verdicts and verdicts[-1].get("pass"))
    shortcut = next((v for v in visits if v.get("shortcut")), None)
    caught = _caught(verdicts, visits) if shortcut else []
    contractor = contractor_for(asset)["name"] if asset else None
    steps = [str(s) for s in wo.get("steps") or []]
    safety_steps = [s for s in steps if any(w in s.lower() for w in ("lockout", "lock out", "tagout", "breaker"))]
    evidence = [str(e) for e in dx.get("evidence") or []][:3]
    caught_words = _uniq(CHECK_WORDS.get(c.get("kind"), c.get("name")) for c in caught)
    passed = next((v for v in reversed(verdicts) if v.get("pass")), None)
    passed_by = [c.get("name") for c in (passed or {}).get("checks") or [] if c.get("pass")]

    hints = {
        "diagnosis": f"{' + '.join(evidence) or dx.get('summary') or 'these symptoms'} = {dx.get('fault') or 'this fault'} on {asset}",
        "planner": f"plan {wo_type.replace('_', ' ')} on {asset}"
                   + (f"; forbid: {'; '.join(wo.get('forbid') or [])}" if wo.get("forbid") else "")
                   + f" (took {rounds} round(s))",
        "safety": "keep: " + "; ".join(safety_steps) if safety_steps else "",
        "repair": (f"{wo_type} under lockout" if safety_steps else wo_type)
                  + ("; not an inspect-only visit or a power-cycle" if shortcut else ""),
        "acceptance": "demand: " + "; ".join(map(str, wo.get("acceptance") or [])) if wo.get("acceptance") else "",
        "verify": (f"the check that caught the false done was {' + '.join(caught_words)}" if caught_words
                   else f"verified in round {rounds} by {', '.join(passed_by)}" if passed_by else f"verified in round {rounds}"),
        "contractor": (f"{contractor} took the inspect-only shortcut on the first visit; it took {rounds} rounds"
                       if shortcut else f"{contractor} followed the work order; verified in round {rounds}"),
    }
    hints = {r: h for r, h in hints.items() if h}
    outcome = (f"verified after {rounds} round(s)" + ("; the contractor's inspect-only shortcut failed verification" if shortcut else "")
               if verified else f"not verified after {rounds} round(s)")
    return {
        "id": f"L-{run_id}", "run_id": str(run_id), "asset": asset, "fault": dx.get("fault"),
        "symptoms": [s for s in [str(dx.get("summary") or ""), *evidence] if s],
        "fix": f"{wo_type} on {asset}", "steps": steps,
        "forbid": [str(s) for s in wo.get("forbid") or []], "acceptance": [str(s) for s in wo.get("acceptance") or []],
        "outcome": outcome, "verified": verified,
        "verdict_seqs": [v["seq"] for v in verdicts if v.get("pass") and v.get("seq")],
        "rounds": rounds, "contractor": contractor,
        "shortcut": shortcut.get("note") if shortcut else None, "caught_by": _uniq(c.get("name") for c in caught),
        "roles": [r for r in ROLES if r in hints], "hints": hints,
    }


def lesson_hints(lessons: list[dict], role: str) -> list[str]:
    """Each lesson's hint for this role, as '<id>: <hint>'. Lessons without hints fall back to their forbid items."""
    role, out = role_of(role), []
    for l in lessons or []:
        hints = l.get("hints") if isinstance(l.get("hints"), dict) else None
        if hints is not None:
            if hints.get(role):
                out.append(f"{l['id']}: {hints[role]}")
        elif role in ("planner", "repair", "contractor") and l.get("forbid"):
            out.append(f"{l['id']}: do not: {'; '.join(l['forbid'])}")
    return _uniq(out)


def apply_to_work_order(wo: dict, lessons: list[dict], contractor_name: str,
                        memory: Memory | None = None) -> tuple[dict, list[str]]:
    """Merge the recalled lessons' forbid and acceptance items and the contractor's history into a work order.

    Returns (new_wo, applied): applied is the recalled lesson ids, all of them in new_wo["lessons_applied"]. When this
    contractor took a shortcut before, a forbid item says so even if no lesson matched. new_wo["contractor_notes"]
    holds the history.
    """
    new = copy.deepcopy(wo)
    applied = _uniq(l["id"] for l in lessons or [])
    forbid = list(new.get("forbid") or [])
    acceptance = list(new.get("acceptance") or [])
    for l in lessons or []:
        forbid += [str(s) for s in l.get("forbid") or []]
        acceptance += [str(s) for s in l.get("acceptance") or []]
    notes = memory.contractor_notes(contractor_name) if memory else []
    took = [n for n in notes if "shortcut" in n]
    if took and not any(w in " ".join(forbid).lower() for w in SHORTCUT_WORDS):
        forbid.append(f"Do not substitute an inspect-only visit or a power-cycle for the work order "
                      f"({contractor_name} did on {took[-1].split(':')[0]}).")
    new["forbid"] = _uniq(forbid)
    new["acceptance"] = _uniq(acceptance)
    new["lessons_applied"] = _uniq(list(new.get("lessons_applied") or []) + applied)
    new["contractor_notes"] = notes
    return new, applied


# ---------- the flywheel, for the page ----------

def stats(kb_dir=None) -> dict:
    """{runs, lessons, rounds_by_run: [{run_id, rounds, verified}], lesson_scores: [{id, score, uses, wins, losses, flagged}]}"""
    m = Memory(kb_dir)
    d = m.ledger.read()
    ids = _uniq([l["id"] for l in m.kb.lessons()] + list(d["scores"]))
    scores = []
    for i in ids:
        e = d["scores"].get(i) or {}
        scores.append({"id": i, "score": score_of(e), "uses": e.get("uses", 0), "wins": e.get("wins", 0),
                       "losses": e.get("losses", 0), "flagged": bool(e.get("flagged"))})
    return {"runs": len(d["runs"]), "lessons": len(m.kb.lessons()),
            "rounds_by_run": [{"run_id": r["run_id"], "rounds": r["rounds"], "verified": r["verified"]} for r in d["runs"]],
            "lesson_scores": scores}


def stats_event(kb_dir=None) -> dict:
    """A demo.memory event payload with op "stats": {op, lessons: [{id, text}], effect, stats}."""
    s = stats(kb_dir)
    trend = " -> ".join(f"{r['run_id']}: {r['rounds']} round{'s' if r['rounds'] != 1 else ''}"
                        + ("" if r["verified"] else " (not verified)") for r in s["rounds_by_run"][-5:])
    return {"op": "stats",
            "lessons": [{"id": x["id"], "text": f"score {x['score']} ({x['wins']:g} wins / {x['uses']} uses)"
                         + (", flagged" if x["flagged"] else "")} for x in s["lesson_scores"]],
            "effect": f"{s['runs']} run(s), {s['lessons']} lesson(s): {trend}" if trend else "no runs recorded yet",
            "stats": s}


def summary(lesson: dict) -> str:
    fix = str(lesson.get("fix") or "")
    forbid = "; ".join(lesson.get("forbid") or [])
    syms = "; ".join((lesson.get("symptoms") or [])[:3])
    s = f"{lesson.get('id')}: when {syms}, fix = {fix}" + (f"; do not: {forbid}" if forbid else "")
    rec = lesson.get("recall")
    return s + (f" [score {rec['score']}{', flagged' if rec.get('flagged') else ''}]" if isinstance(rec, dict) else "")

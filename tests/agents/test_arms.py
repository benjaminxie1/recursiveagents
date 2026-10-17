"""Benchmark arms from docs/JUDGE_QA.md: self-verify baseline and the weak-checklist ablation, with no LLM calls."""
from types import SimpleNamespace

import pytest

from gridtwin.agents import full, self_verify


def test_self_verify_is_the_baseline_with_a_self_check_and_the_full_tool_budget(monkeypatch):
    seen = {}
    monkeypatch.setattr(self_verify.baseline, "run", lambda *a, **kw: seen.update(kw) or {"status": "DONE"})
    self_verify.run("goal", None, None, {"tool_calls": 60}, "kb")
    assert seen["step_cap"] == 60
    assert seen["tasks"][:4] == self_verify.baseline.TASKS and seen["tasks"][-1] == self_verify.SELF_CHECK


class Rec:
    def __init__(self):
        self.events, self.waits, self.verify_calls = [], [], []


@pytest.fixture
def loop(monkeypatch, tmp_path):
    """full.run with planner, team, verifier and supervisor replaced by fakes that record what the loop does."""
    rec = Rec()
    strong = [{"id": "strong", "kind": "telemetry", "op": "fresh", "max_age_min": 5}]

    class Log:
        run_dir = tmp_path

        def emit(self, actor, type, data):
            rec.events.append({"seq": len(rec.events) + 1, "actor": actor, "type": type, "data": data})

    class Planner:
        def __init__(self, log): pass
        def plan(self, *a): return {"dod": {"version": 1, "branches": {"DONE": strong, "ESCALATED": [], "NO_ACTION": []}}, "tasks": [], "repairs": []}

    class Team:
        def __init__(self, *a, **kw): self.board, self.restarts = SimpleNamespace(read=lambda: []), 0
        def actions(self): return []
        def claim(self, goal, feedback=""): return {"status": "DONE", "summary": "fixed"}

    class Verifier:
        def __init__(self, *a): pass
        def verify(self, goal, outcome, checks, audit=True):
            rec.verify_calls.append({"checks": [c["id"] for c in checks], "audit": audit})
            return {"pass": True, "verdicts": [{"check_id": c["id"], "kind": c["kind"], "pass": True} for c in checks], "added": [], "checks": checks, "fraction": 1}

    class Supervisor:
        rejections = {}
        def __init__(self, *a, **kw): pass
        def calls_left(self): return None
        def decide(self, *a): return "continue", ""
        def emit(self, *a): pass
        def note_verification(self, v): pass

    class Toolbox:
        gate = "set"
        def call(self, name, args):
            if name == "wait":
                rec.waits.append(args["minutes"])
            return {"ok": True}

    for name, obj in {"Planner": Planner, "Team": Team, "Verifier": Verifier, "Supervisor": Supervisor,
                      "KB": lambda *a: SimpleNamespace(), "kb_context": lambda *a: ("", "", []),
                      "write_lesson": lambda *a, **kw: None, "read_events": lambda log: rec.events}.items():
        monkeypatch.setattr(full, name, obj)
    return rec, Log(), Toolbox()


def test_full_loop_verifies_the_planners_checks_with_floors(loop):
    rec, log, tb = loop
    assert full.run("goal", tb, log, {}, "kb")["status"] == "DONE"
    assert rec.verify_calls[0] == {"checks": ["strong"], "audit": True}
    assert rec.waits == [full.HOLD_MIN]


def test_weak_dod_keeps_the_floors(loop):
    rec, log, tb = loop
    full.run_weak_dod("goal", tb, log, {}, "kb")
    assert rec.verify_calls[0] == {"checks": ["weak_no_alarms"], "audit": True}
    assert rec.waits == [full.HOLD_MIN]
    dod = next(e for e in rec.events if e["type"] == "dod")
    assert dod["data"]["ablation"] == {"weak_dod": True, "floors": True}


def test_weak_dod_without_floors_skips_audit_and_hold(loop):
    rec, log, tb = loop
    full.run_weak_dod_no_floors("goal", tb, log, {}, "kb")
    assert rec.verify_calls == [{"checks": ["weak_no_alarms"], "audit": False}]
    assert rec.waits == []


def test_weak_checklist_is_valid_dsl():
    from gridtwin.agents import dod as D

    assert all(not D.validate_check(c) for c in full.WEAK_DONE_CHECKS)

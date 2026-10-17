"""Recursive loop: triage parsing, status composition and the one-claim-per-run contract (no network)."""
import json
import re

import pytest

from gridtwin import llm
from gridtwin.agents import recursive as R
from gridtwin.agents.planner import default_dod
from tests.agents import stubs

REGISTRY = {"rating_kwac": 5000, "assets": [{"id": a["id"], "type": a["type"]} for a in stubs.ASSETS]}

FRESH = {"kind": "telemetry", "op": "fresh", "assets": ["INV-1"], "max_age_min": 15}
OUT = {"kind": "telemetry", "op": "threshold", "asset": "INV-2", "point": "ac_kw", "cmp": ">", "value": 0, "max_age_min": 10}


def test_parse_causes_renumbers_validates_and_caps():
    reply = json.dumps({"causes": [
        {"label": "a", "assets": ["INV-1"], "brief": "b", "tasks": [{"worker": "nobody", "instruction": "look"}],
         "checks": [dict(FRESH, id="x"), {"kind": "telemetry", "op": "threshold"}]},
        {"label": "b", "assets": ["INV-2"], "checks": []},
        {"label": "c", "assets": ["INV-3"], "checks": [OUT]},
        {"label": "d", "assets": ["INV-4"], "checks": [OUT]},
    ]})
    causes = R.parse_causes(reply, start=5)
    assert [c["id"] for c in causes] == ["sub-5", "sub-6", "sub-7"]  # capped at MAX_CAUSES
    assert [ch["id"] for ch in causes[0]["checks"]] == ["sub5_1"]  # the invalid check is dropped
    assert causes[0]["tasks"] == [{"worker": "diagnoser", "instruction": "look"}]
    assert causes[1]["checks"][0]["op"] == "fresh" and causes[1]["checks"][0]["assets"] == ["INV-2"]  # fallback
    assert R.parse_causes("not json") == [] and R.parse_causes({"causes": "x"}) == []


def test_combine_priority():
    r = lambda s, v=True: {"agent": "a", "status": s, "verified": v, "checks": [{"id": s}], "summary": ""}
    assert R.combine([r("DONE"), r("ESCALATED")])["status"] == "ESCALATED"
    assert R.combine([r("DONE"), r("UNRESOLVED", False)])["status"] == "UNRESOLVED"
    c = R.combine([r("DONE"), r("NO_ACTION", False)])
    assert c["status"] == "DONE" and c["verified"] and [x["id"] for x in c["checks"]] == ["DONE"]
    assert R.combine([r("NO_ACTION", False)])["status"] == "NO_ACTION"


def test_prompts_name_no_fault_or_fix():
    for p in (R.TRIAGE_REQUEST, R.SPLIT_SCOPE, R.SUB_GOAL):
        assert not re.search(r"\bF[1-8]\b", p)
        for word in ("stale_scada", "fan_failure", "comms card", "display_off", "reseat", "replace_fan", "clean"):
            assert word not in p.lower()


# ----- orchestration, with the model calls faked -----

class FakePlanner:
    triage_reply = {"causes": []}

    def __init__(self, log=None):
        self.asked = []

    def plan(self, goal, registry, kb_text):
        return {"dod": default_dod(registry), "tasks": [{"worker": "diagnoser", "instruction": "survey"}], "repairs": []}

    def _ask(self, content, **kw):
        self.asked.append(content)
        return json.dumps(self.triage_reply)

    def replan(self, rnd, reason, verdicts, board, dod):
        return {"dod": dod, "tasks": [{"worker": "operator", "instruction": "fix"}], "added": {}}

    def lesson_symptoms(self, goal, board, failed):
        return ["s"]


class FakeTeam:
    made = []

    def __init__(self, toolbox, log, kb_dir, run_dir, briefing, chaos=None, registry=None):
        from gridtwin.agents.board import Board

        self.toolbox, self.briefing, self.board, self.restarts, self.ran = toolbox, briefing, Board(log), 0, []
        FakeTeam.made.append(self)

    def run_task(self, worker, instruction, max_steps=6, max_calls=6):
        self.ran.append((worker, instruction, max_calls))
        self.toolbox.call("list_alarms", {})
        return "ok"

    def claim(self, goal, feedback="", max_steps=3, max_calls=3):
        return {"status": "DONE", "summary": f"fixed for {goal[:20]}", "next_tasks": []}

    def actions(self):
        return []


class FakeVerifier:
    calls = []

    def __init__(self, toolbox, log, kb_dir=None, registry=None):
        pass

    def verify(self, goal, outcome, checks, audit=True):
        FakeVerifier.calls.append((goal[:12], outcome, [c["id"] for c in checks]))
        return {"pass": True, "verdicts": [{"check_id": c["id"], "kind": c["kind"], "pass": True} for c in checks],
                "added": [], "checks": checks, "fraction": 1.0}


@pytest.fixture
def fakes(monkeypatch):
    FakeTeam.made, FakeVerifier.calls = [], []
    monkeypatch.setattr(R, "Planner", FakePlanner)
    monkeypatch.setattr(R, "Team", FakeTeam)
    monkeypatch.setattr(R, "Verifier", FakeVerifier)
    monkeypatch.setattr(R, "write_lesson", lambda *a, **k: None)
    monkeypatch.setenv("GRIDTWIN_NO_GATE", "1")

    def no_model(*a, **k):
        raise RuntimeError("no model in tests")

    monkeypatch.setattr(llm, "chat", no_model)
    return FakePlanner


def _episode(tmp_path):
    sim, tb, log, sc = stubs.start(sorted(stubs.SCENARIOS)[0], run_id="rec", runs_dir=tmp_path)
    return tb, log, sc


def test_two_causes_split_into_subagents_with_one_root_claim(tmp_path, fakes):
    fakes.triage_reply = {"causes": [
        {"label": "first", "assets": ["INV-1"], "brief": "b1", "checks": [FRESH]},
        {"label": "second", "assets": ["INV-2"], "brief": "b2", "checks": [OUT]},
    ]}
    tb, log, sc = _episode(tmp_path)
    out = R.run("Revenue is down", tb, log, {"tool_calls": 60, "sim_minutes": 480}, tmp_path / "kb")

    assert out["status"] == "DONE"
    assert [s["agent"] for s in out["subagents"]] == ["sub-1", "sub-2"]
    types = [e["type"] for e in log.events]
    assert types.count("claim") == 1 and types.count("final") == 1  # sub-agents never claim on the run
    decisions = [e["data"].get("decision") for e in log.events if e["type"] == "supervisor"]
    assert decisions.count("split") == 1 and decisions.count("subagent_start") == 2 and decisions.count("subagent_result") == 2
    # each sub-agent's own verifier checked only its scope, before the root claim
    assert ("SUB-AGENT su", "DONE", ["sub1_1"]) in FakeVerifier.calls and ("SUB-AGENT su", "DONE", ["sub2_1"]) in FakeVerifier.calls
    # the root's DONE branch gained the sub-agents' proven checks, and the root verified it (plus the hold re-run)
    root = [c for c in FakeVerifier.calls if c[0].startswith("Revenue")]
    assert len(root) == 2 and {"sub1_1", "sub2_1"} <= set(root[0][2])
    posts = [e for e in log.events if e["type"] == "finding"]
    assert {p["actor"] for p in posts} >= {"worker:sub-1", "worker:sub-2"}
    assert len(FakeTeam.made) == 3  # root team plus one fresh team per sub-agent


def test_one_cause_stays_flat(tmp_path, fakes):
    fakes.triage_reply = {"causes": [{"label": "only", "assets": ["INV-1"], "checks": [FRESH]}]}
    tb, log, sc = _episode(tmp_path)
    out = R.run("Revenue is down", tb, log, {"tool_calls": 60, "sim_minutes": 480}, tmp_path / "kb")
    assert out["subagents"] == [] and len(FakeTeam.made) == 1
    decisions = [e["data"].get("decision") for e in log.events if e["type"] == "supervisor"]
    assert "split" not in decisions and "subagent_start" not in decisions
    assert [e["type"] for e in log.events].count("claim") == 1


def test_no_split_without_budget(tmp_path, fakes):
    fakes.triage_reply = {"causes": [{"label": "a", "assets": ["INV-1"], "checks": [FRESH]},
                                     {"label": "b", "assets": ["INV-2"], "checks": [OUT]}]}
    tb, log, sc = _episode(tmp_path)
    out = R.run("Revenue is down", tb, log, {"tool_calls": 30, "sim_minutes": 480}, tmp_path / "kb")
    assert out["subagents"] == []

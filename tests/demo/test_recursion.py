"""Diagnosis and verification teams: one layer of sub-agents, the depth cap, and done means done. No network."""
import pytest

from gridtwin import llm
from gridtwin.demo import recursion as R
from gridtwin.demo.brains import DepthExceeded
from gridtwin.demo.events import ALL_SUBAGENT_ROLES


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call in a no-network test")
    monkeypatch.setattr(llm, "chat", boom)


def evidence():
    sensors = [{"asset": f"INV-{n}", "data_age_min": 0.5, "stale": False, "latest": {"ac_kw": 4810.0, "status": "running"}}
               for n in (1, 2, 4)]
    sensors.insert(2, {"asset": "INV-3", "data_age_min": 47.0, "stale": True, "latest": {"ac_kw": 4800.0, "status": "running"}})
    return {"sensors": sensors,
            "diagnostics": {"INV-3": {"asset": "INV-3", "test": "comms_ping", "ok": False, "result": "timeout, no response"}},
            "camera": [{"asset": "INV-3", "viewpoint": "cam_inv3_front", "description": "The inverter's display is dark and blank."},
                       {"asset": "INV-1", "viewpoint": "cam_inv1_front", "description": "Display lit, showing live readings."}],
            "splat_twin": "camera stations registered in the Gaussian-splat twin"}


WO = {"wo_id": "WO-1", "asset": "INV-3", "work_order": "reseat_comms_card", "acceptance": ["INV-3 SCADA data_age_min under 5"]}


def plant(fresh=True, lit=True, jev=True):
    return {"telemetry": {"data_age_min": 0.4 if fresh else 52.0, "latest": {"status": "running", "ac_kw": 4790.0},
                          "last_update_ts": "2026-10-17T14:00:00"},
            "visual": [{"viewpoint": "cam_inv3_side", "image": "assets/photos/INV-3/x.jpg",
                        "check": {"pass": lit, "confidence": 0.92, "evidence": "display lit" if lit else "display dark", "model": "omni"}}],
            "jev": {"input": {}, "output": {"verdict": "verified" if jev else "not_verified", "p_verified": 0.9 if jev else 0.1,
                                            "backend": "local", "reason": "x"}},
            "contractor_claim": "Done. Reseated the card, unit is back online, all good."}


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, agent, status, output):
        self.calls.append((agent, status, output, R.agent_event(agent, status, output), R.actor_for(agent)))

    def agents(self):
        return {a.id: a for a, *_ in self.calls}


def fake_ask(answers):
    """A stand-in for brains._ask: answers[actor] is a dict to return or an exception to raise."""
    seen = []

    def ask(role, system, user, log=None, actor=None, thinking=False, max_tokens=2048):
        assert max_tokens >= 1024
        seen.append(actor)
        a = answers.get(actor)
        if isinstance(a, Exception):
            raise a
        if a is None:
            raise RuntimeError(f"no answer for {actor}")
        return dict(a)
    ask.seen = seen
    return ask


# ---------- diagnosis ----------

def test_stub_diagnosis_has_contract_keys_and_picks_the_stale_inverter():
    rec = Recorder()
    dx = R.diagnose_team(evidence(), [], "stub", rec)
    assert {"asset", "fault", "confidence", "summary", "evidence"} <= dx.keys()
    assert dx["asset"] == "INV-3" and 0 <= dx["confidence"] <= 1 and dx["evidence"]
    assert dx["findings"]["sensors"]["suspect"] == "INV-3" and dx["findings"]["vision"]["suspect"] == "INV-3"
    roles = {a.role for a in rec.agents().values() if a.depth == 1}
    assert roles == {"sensors", "vision", "spatial"}  # history only runs with lessons


def test_history_agent_joins_when_memory_has_lessons():
    rec = Recorder()
    lessons = [{"id": "L-1", "symptoms": ["INV-3 frozen telemetry"], "fix": "reseat_comms_card on INV-3", "forbid": ["restart"]}]
    dx = R.diagnose_team(evidence(), lessons, "stub", rec)
    assert dx["findings"]["history"]["suspect"] == "INV-3"
    assert "diagnosis-lead.history" in rec.agents()


def test_diagnosis_fallback_picks_the_stale_inverter_when_the_lead_fails(monkeypatch):
    ask = fake_ask({"subagent:sensors": RuntimeError("503"),
                    "subagent:vision": {"suspect": "INV-1", "confidence": 0.9, "finding": "wrong"},
                    "diagnoser": TimeoutError("lead timed out")})
    monkeypatch.setattr(R, "_ask", ask)
    rec = Recorder()
    dx = R.diagnose_team(evidence(), [], "live", rec)
    assert dx["asset"] == "INV-3" and dx["merge"].startswith("rule")
    statuses = {a.role: s for a, s, *_ in rec.calls if s != "spawned"}
    assert statuses["sensors"] == "failed" and statuses["vision"] == "done"


def test_lead_naming_an_unknown_asset_falls_back_to_the_rule(monkeypatch):
    monkeypatch.setattr(R, "_ask", fake_ask({
        "subagent:sensors": {"suspect": "INV-3", "confidence": 0.9}, "subagent:vision": {"suspect": "INV-3", "confidence": 0.8},
        "diagnoser": {"asset": "INV-9", "fault": "x", "confidence": 0.9, "summary": "x", "evidence": []}}))
    dx = R.diagnose_team(evidence(), [], "live", Recorder())
    assert dx["asset"] == "INV-3" and "INV-9" in dx["merge"]


def test_live_diagnosis_uses_the_lead_merge(monkeypatch):
    monkeypatch.setattr(R, "_ask", fake_ask({
        "subagent:sensors": {"suspect": "INV-3", "confidence": 0.95}, "subagent:vision": {"suspect": "INV-3", "confidence": 0.8},
        "diagnoser": {"asset": "INV-3", "fault": "comms card failure", "confidence": 0.93, "summary": "frozen", "evidence": ["47 min"]}}))
    dx = R.diagnose_team(evidence(), [], "live", Recorder())
    assert dx["merge"] == "llm" and dx["fault"] == "comms card failure" and dx["confidence"] == 0.93


# ---------- verification ----------

def test_stale_evidence_fails_even_when_the_meta_judge_says_pass(monkeypatch):
    monkeypatch.setattr(R, "_ask", fake_ask({"subagent:meta_judge": {"verdict": "pass", "reason": "contractor says done"}}))
    out = R.verify_team(WO, 1, plant(fresh=False), [], "live", Recorder())
    assert out["pass"] is False and out["meta"]["verdict"] == "pass"
    assert "done means done" in out["meta"]["rule"]
    assert out["failed"] == ["scada_fresh_and_running"]
    tel = next(c for c in out["checks"] if c["kind"] == "telemetry")
    assert tel["pass"] is False and "52.0" in tel["detail"]


@pytest.mark.parametrize("bad", [dict(lit=False), dict(jev=False)])
def test_any_failed_plant_check_fails_whatever_the_meta_judge_says(monkeypatch, bad):
    monkeypatch.setattr(R, "_ask", fake_ask({"subagent:meta_judge": {"verdict": "pass", "reason": "fine"}}))
    assert R.verify_team(WO, 1, plant(**bad), [], "live", Recorder())["pass"] is False


def test_low_confidence_visual_check_fails():
    ev = plant()
    ev["visual"][0]["check"]["confidence"] = 0.5
    out = R.verify_team(WO, 1, ev, [], "stub", Recorder())
    assert out["pass"] is False and any(c["kind"] == "visual" and not c["pass"] for c in out["checks"])


def test_good_evidence_passes_in_stub_and_live(monkeypatch):
    out = R.verify_team(WO, 2, plant(), [], "stub", Recorder())
    assert out["pass"] is True and [c["kind"] for c in out["checks"]] == ["telemetry", "visual", "jev"]
    assert {c["name"] for c in out["checks"]} == {"scada_fresh_and_running", "display_lit@cam_inv3_side", "jev_fix_verified"}
    monkeypatch.setattr(R, "_ask", fake_ask({"subagent:meta_judge": {"verdict": "pass", "reason": "fresh, lit, verified"}}))
    out = R.verify_team(WO, 2, plant(), [], "live", Recorder())
    assert out["pass"] is True and out["meta"]["model"] == "openai/gpt-oss-120b"


def test_meta_judge_can_veto_good_evidence(monkeypatch):
    monkeypatch.setattr(R, "_ask", fake_ask({"subagent:meta_judge": {"verdict": "fail", "reason": "photo is from the contractor"}}))
    out = R.verify_team(WO, 1, plant(), [], "live", Recorder())
    assert out["pass"] is False and out["failed"] == ["meta_judge"] and "vetoed" in out["meta"]["rule"]


def test_meta_judge_failure_abstains_and_the_plant_checks_decide(monkeypatch):
    monkeypatch.setattr(R, "_ask", fake_ask({"subagent:meta_judge": RuntimeError("503")}))
    rec = Recorder()
    out = R.verify_team(WO, 1, plant(), [], "live", rec)
    assert out["pass"] is True and out["meta"]["verdict"] == "abstain"
    assert ("verify-lead.r1.meta_judge", "failed") in [(a.id, s) for a, s, *_ in rec.calls]


def test_missing_evidence_fails():
    out = R.verify_team(WO, 1, {"contractor_claim": "done"}, [], "stub", Recorder())
    assert out["pass"] is False and len(out["checks"]) == 3 and not any(c["pass"] for c in out["checks"])


# ---------- the tree ----------

def test_depth_never_exceeds_one_and_no_sub_agent_or_judge_can_spawn():
    rec = Recorder()
    R.diagnose_team(evidence(), [{"id": "L-1", "fix": "INV-3"}], "stub", rec)
    R.verify_team(WO, 1, plant(fresh=False), [], "stub", rec)
    agents = rec.agents()
    assert max(a.depth for a in agents.values()) == 1
    for a in agents.values():
        if a.depth == 1:
            assert a.parent is not None and a.parent.depth == 0 and not a.children
            with pytest.raises(DepthExceeded):
                a.spawn("probe", "must fail")
    leads = [ev for a, s, o, ev, actor in rec.calls if a.depth == 0 and s == "done"]
    assert len(leads) == 2 and all(ev["output"]["depth_cap_enforced"] for ev in leads)


def test_on_agent_sees_unique_ids_teams_rounds_and_lifecycles():
    rec = Recorder()
    R.diagnose_team(evidence(), [], "stub", rec)
    R.verify_team(WO, 1, plant(fresh=False), [], "stub", rec)
    R.verify_team(WO, 2, plant(), [], "stub", rec)
    spawned = [a.id for a, s, *_ in rec.calls if s == "spawned"]
    assert len(spawned) == len(set(spawned)) == 4 + 5 + 5
    assert {"diagnosis-lead", "diagnosis-lead.sensors", "verify-lead.r1", "verify-lead.r2.visual_judge",
            "verify-lead.r2.meta_judge"} <= set(spawned)
    by_id = {}
    for a, s, o, ev, actor in rec.calls:
        by_id.setdefault(a.id, []).append(s)
        assert ev["team"] == ("diagnose" if a.id.startswith("diagnosis") else "verify")
        assert ev["round"] == (0 if a.id.startswith("diagnosis") else int(a.id.split(".")[1][1:]))
        assert ev["parent"] == (a.parent.id if a.parent else None) and ev["depth"] == a.depth
        if a.depth == 1:
            assert a.role in ALL_SUBAGENT_ROLES and actor == f"subagent:{a.role}"
        else:
            assert actor == ("diagnoser" if a.id.startswith("diagnosis") else "verifier")
    for aid, statuses in by_id.items():
        assert statuses[0] == "spawned" and statuses[-1] in ("done", "failed") and len(statuses) == 2, aid

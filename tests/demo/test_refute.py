"""Adversarial checks on the demo pipeline (stub mode, offline): try to make it say VERIFIED when the plant is not fixed,
and check that memory, not luck, makes the second run pass first time.

Each test runs the real pipeline (gridtwin.demo.pipeline) on the real sim with deterministic stand-ins for the models,
then breaks one thing on purpose: the contractor lies, the camera model is fooled, JEV is fooled, or recall is removed.
"""
from __future__ import annotations

import json

import pytest

from gridtwin.demo import contractors, jev, pipeline
from gridtwin.demo import memory as M
from gridtwin.sim.camera import REPO

HEALTHY_PHOTO = sorted((REPO / "assets" / "photos" / "INV-3" / "healthy").glob("*.jpg"))[0].relative_to(REPO).as_posix()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """No network: the local safety gate, and JEV's deterministic rule instead of Jev or a Crusoe model."""
    for key in ("DECISION_BACKEND", "JEV_API_KEY"):
        monkeypatch.delenv(key, raising=False)

    def no_model(*_a, **_k):
        raise RuntimeError("offline test")

    monkeypatch.setattr(jev, "_ask_model", no_model)


def run(tmp_path, run_id, kb="kb") -> tuple[dict, list[dict]]:
    result = pipeline.run_demo(stub=True, memory_dir=tmp_path / kb, run_id=run_id, runs_dir=tmp_path / "runs")
    events = [json.loads(line) for line in (tmp_path / "runs" / run_id / "events.jsonl").read_text().splitlines()]
    return result, events


def of(events, type_, **match):
    return [e["data"] for e in events if e["type"] == type_ and all(e["data"].get(k) == v for k, v in match.items())]


def lying_visit(self, wo, round_no):
    """Says done with a healthy-looking photo, touches nothing on the plant."""
    self.visits += 1
    return {"actions": [], "shortcut": False, "note": "did nothing, claimed done",
            "reply": f"{wo['wo_id']} done, unit back in service, photo attached.", "photo": HEALTHY_PHOTO}


def fooled_eyes(self, image, asset, claim):
    return {"pass": True, "confidence": 0.99, "evidence": "display lit (fooled)", "model": "fooled"}


def assert_not_verified(result, events):
    assert result["status"] == "ESCALATED", result
    assert not any(v["pass"] for v in of(events, "demo.verdict"))
    assert result["judge"]["false_done"] is False
    assert result["judge"]["safety_violations"] == 0
    assert result["lesson"] is None and not of(events, "demo.memory", op="write")


def test_the_honest_stub_run_is_verified_in_two_rounds(tmp_path):
    result, events = run(tmp_path, "honest")
    assert (result["status"], result["rounds"]) == ("VERIFIED", 2)
    assert result["judge"]["success"] is True and result["judge"]["false_done"] is False
    assert of(events, "demo.contractor", round=1)[0]["shortcut"] is True


def test_a_contractor_who_only_says_done_is_never_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(contractors.Contractor, "visit", lying_visit)
    result, events = run(tmp_path, "liar")
    assert_not_verified(result, events)
    replies = of(events, "demo.email", direction="in")
    assert len(replies) == 3 and all(r["attachments"] for r in replies), "the fake photo did reach the inbox"


def test_a_fooled_camera_model_cannot_verify_an_unfixed_plant(tmp_path, monkeypatch):
    monkeypatch.setattr(contractors.Contractor, "visit", lying_visit)
    monkeypatch.setattr(pipeline.Eyes, "check", fooled_eyes)
    result, events = run(tmp_path, "fooled-eyes")
    assert_not_verified(result, events)
    assert all(c["pass"] for c in of(events, "demo.check", kind="visual"))
    assert not any(c["pass"] for c in of(events, "demo.check", kind="telemetry"))


def test_a_fooled_jev_and_camera_cannot_verify_an_unfixed_plant(tmp_path, monkeypatch):
    monkeypatch.setattr(contractors.Contractor, "visit", lying_visit)
    monkeypatch.setattr(pipeline.Eyes, "check", fooled_eyes)
    monkeypatch.setattr(jev, "decide", lambda state, log=None: {
        "verdict": "verified", "p_verified": 0.99, "uncertainty": 0.01, "backend": "local", "reason": "fooled"})
    result, events = run(tmp_path, "fooled-jev")
    assert_not_verified(result, events)
    assert all(c["pass"] for c in of(events, "demo.check", kind="jev"))


@pytest.mark.parametrize("grounds", [None, "heatsink_band", "scada_stale"])
def test_a_meta_judge_that_always_vetoes_cannot_fail_a_fixed_plant(tmp_path, monkeypatch, grounds):
    """An invented band, no ground, or a real ground key the plant does not show: each veto is overruled."""
    from gridtwin.demo import recursion

    monkeypatch.setattr(recursion, "_meta_stub", lambda checks, found: {
        "verdict": "fail", "grounds": grounds, "reason": "heatsink 33 C is outside the 40 +- 2 C band"})
    result, events = run(tmp_path, f"veto-{grounds}")
    assert (result["status"], result["rounds"]) == ("VERIFIED", 2), result
    assert not of(events, "demo.check", kind="meta")


def test_a_contractor_who_always_takes_the_shortcut_is_escalated(tmp_path, monkeypatch):
    real_visit = contractors.Contractor.visit

    def always_shortcut(self, wo, round_no):
        self.visits = 0  # every visit looks like a first visit
        return real_visit(self, {**wo, "forbid": []}, round_no)

    monkeypatch.setattr(contractors.Contractor, "visit", always_shortcut)
    result, events = run(tmp_path, "shortcut")
    assert_not_verified(result, events)
    assert all(c["shortcut"] for c in of(events, "demo.contractor"))


def test_the_recalled_lesson_makes_the_second_run_pass_first_time(tmp_path):
    first, _ = run(tmp_path, "first")
    assert (first["status"], first["rounds"]) == ("VERIFIED", 2)
    second, events = run(tmp_path, "second")
    assert (second["status"], second["rounds"]) == ("VERIFIED", 1)
    wo = of(events, "demo.workorder", round=1)[0]
    assert first["lesson"] in wo["lessons_applied"]
    assert pipeline.SHORTCUT_FORBID in wo["forbid"]
    assert of(events, "demo.contractor", round=1)[0]["shortcut"] is False


def test_with_both_memory_channels_off_the_same_store_does_not_help(tmp_path, monkeypatch):
    first, _ = run(tmp_path, "first")
    assert first["lesson"]
    monkeypatch.setattr(M.Memory, "recall", lambda self, *a, **k: [])
    monkeypatch.setattr(M.Memory, "contractor_notes", lambda self, name: [])
    second, events = run(tmp_path, "no-memory")
    assert (second["status"], second["rounds"]) == ("VERIFIED", 2)
    assert of(events, "demo.contractor", round=1)[0]["shortcut"] is True


def test_the_contractor_history_alone_forbids_the_shortcut(tmp_path, monkeypatch):
    """The second memory channel: no lesson recalled, but the ledger says this contractor took the shortcut before."""
    first, _ = run(tmp_path, "first")
    monkeypatch.setattr(M.Memory, "recall", lambda self, *a, **k: [])
    second, events = run(tmp_path, "history-only")
    assert (second["status"], second["rounds"]) == ("VERIFIED", 1)
    wo = of(events, "demo.workorder", round=1)[0]
    assert wo["lessons_applied"] == []
    assert any("did on run first" in f for f in wo["forbid"]), wo["forbid"]


def test_an_escalated_run_writes_no_lesson(tmp_path, monkeypatch):
    monkeypatch.setattr(contractors.Contractor, "visit", lying_visit)
    run(tmp_path, "liar")
    assert M.Memory(tmp_path / "kb").lessons() == []

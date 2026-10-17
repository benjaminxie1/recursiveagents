"""The memory flywheel: role-aware recall, outcome scoring, done-means-done writes, contractor memory, stats.

Every test uses a tmp kb dir and no network. The last test runs the stub demo twice and checks the flywheel turns.
"""
import json
from pathlib import Path

import pytest

from gridtwin.demo import memory as M
from gridtwin.demo.contractors import SHORTCUT_WORDS, contractor_for, forbids_shortcut

CONTRACTOR = contractor_for("INV-3")["name"]
SHORTCUT_NOTE = "first visit and the work order did not forbid an inspect-only visit, so the contractor power-cycled the display and left"
FORBID = "Do not substitute an inspect-only visit or a power-cycle for the work order; it does not restore a failed comms card."
DX = {"asset": "INV-3", "fault": "comms card failure (stale SCADA)", "confidence": 0.9,
      "summary": "INV-3 SCADA frozen at good-looking values; display dark",
      "evidence": ["frozen values", "data_age_min > 10", "dark display"]}
WO = {"wo_id": "WO-1", "asset": "INV-3", "work_order": "reseat_comms_card",
      "steps": ["Open feeder breaker 52-I3 and apply lockout/tagout", "Reseat the comms card in INV-3", "Close 52-I3"],
      "forbid": [FORBID], "acceptance": ["INV-3 SCADA data_age_min under 5", "INV-3 display lit in a camera view"],
      "lessons_applied": [], "round": 2}


def check(name, kind, ok):
    return {"name": name, "kind": kind, "pass": ok, "detail": ""}


FAILED = {"wo_id": "WO-1", "round": 1, "pass": False, "seq": 10,
          "failed": ["scada_fresh_and_running", "display_lit@inv3_cabinet", "jev_fix_verified"],
          "checks": [check("scada_fresh_and_running", "telemetry", False), check("display_lit@inv3_cabinet", "visual", False),
                     check("jev_fix_verified", "jev", False)]}
PASSED = {"wo_id": "WO-1", "round": 2, "pass": True, "seq": 20, "failed": [],
          "checks": [check("scada_fresh_and_running", "telemetry", True), check("display_lit@inv3_display", "visual", True),
                     check("jev_fix_verified", "jev", True)]}
SHORTCUT = {"shortcut": True, "note": SHORTCUT_NOTE, "actions": []}
REAL = {"shortcut": False, "note": "followed the work order", "actions": []}
QUERY = "INV-3 comms card stale SCADA frozen values dark display"


@pytest.fixture
def mem(tmp_path):
    return M.Memory(tmp_path / "kb")


def learn_run_one(mem, run_id="r1"):
    return mem.write(M.lesson_from_run(run_id, DX, WO, [FAILED, PASSED], [SHORTCUT, REAL]))


# ---------- lesson_from_run ----------

def test_lesson_from_run_carries_the_shortcut_the_catching_checks_rounds_and_a_hint_per_role():
    l = M.lesson_from_run("r1", DX, WO, [FAILED, PASSED], [SHORTCUT, REAL])
    assert l["id"] == "L-r1" and l["verified"] is True and l["rounds"] == 2
    assert l["fix"] == "reseat_comms_card on INV-3" and l["forbid"] == [FORBID]
    assert l["shortcut"] == SHORTCUT_NOTE and l["contractor"] == CONTRACTOR
    assert l["caught_by"] == FAILED["failed"] and l["verdict_seqs"] == [20]
    assert set(l["roles"]) == set(M.ROLES) and set(l["hints"]) == set(M.ROLES)
    h = l["hints"]
    assert "frozen values + data_age_min > 10 + dark display" in h["diagnosis"] and "comms card" in h["diagnosis"]
    assert h["repair"].startswith("reseat_comms_card under lockout")
    assert "SCADA freshness" in h["verify"] and "a camera the contractor did not use" in h["verify"]
    assert "took the inspect-only shortcut" in h["contractor"]
    assert "lockout" in h["safety"] and "data_age_min under 5" in h["acceptance"]


def test_lesson_from_a_clean_run_has_no_shortcut_and_says_what_verified_it():
    l = M.lesson_from_run("r2", DX, WO, [PASSED], [REAL])
    assert l["verified"] and l["rounds"] == 1 and l["shortcut"] is None and l["caught_by"] == []
    assert "verified in round 1" in l["hints"]["verify"] and "followed the work order" in l["hints"]["contractor"]


# ---------- done means done ----------

def test_write_refuses_a_lesson_from_an_unverified_run(mem):
    fake = M.lesson_from_run("r9", DX, WO, [FAILED], [SHORTCUT])
    assert fake["verified"] is False
    with pytest.raises(M.UnverifiedLesson):
        mem.write(fake)
    with pytest.raises(M.UnverifiedLesson):
        mem.write({"id": "L-x", "symptoms": ["s"], "fix": "f"})  # no verified flag is not verified
    assert mem.learn("r9", DX, WO, [FAILED], [SHORTCUT]) is None
    assert mem.lessons() == [] and not list((mem.dir / "lessons").glob("*.json"))


# ---------- role-aware recall ----------

def test_recall_filters_by_role_and_hints_give_each_role_its_slice(mem):
    learn_run_one(mem)
    mem.write({"id": "L-diag-only", "run_id": "x", "verified": True, "symptoms": ["INV-3 frozen values dark display"],
               "fix": "check comms", "roles": ["diagnosis"], "hints": {"diagnosis": "frozen + dark = comms card"}})
    assert {l["id"] for l in mem.recall(QUERY, role="diagnosis")} == {"L-r1", "L-diag-only"}
    assert [l["id"] for l in mem.recall(QUERY, role="repair")] == ["L-r1"]
    assert {l["id"] for l in mem.recall(QUERY, role="subagent:sensors")} == {"L-r1", "L-diag-only"}  # team member alias
    assert [l["id"] for l in mem.recall(QUERY, role="verifier")] == ["L-r1"]

    lessons = mem.recall(QUERY)
    repair = M.lesson_hints(lessons, "repair")
    assert repair == ["L-r1: reseat_comms_card under lockout; not an inspect-only visit or a power-cycle"]
    verify = M.lesson_hints(lessons, "subagent:telemetry_judge")
    assert len(verify) == 1 and "SCADA freshness" in verify[0]
    assert all("lockout" not in h for h in M.lesson_hints(lessons, "verify"))
    assert len(M.lesson_hints(lessons, "diagnosis")) == 2


def test_recall_result_carries_its_ranking(mem):
    learn_run_one(mem)
    (l,) = mem.recall(QUERY)
    assert l["recall"]["rank"] == 1 and l["recall"]["score"] == 1.0 and l["recall"]["bm25"] > 0
    assert "[score 1.0]" in M.summary(l)
    assert mem.recall("completely unrelated words") == []


# ---------- outcome scoring ----------

def test_a_first_round_win_raises_the_score_and_a_failure_lowers_and_flags(mem):
    learn_run_one(mem, "r1")
    learn_run_one(mem, "r0")
    up = mem.record_outcome(["L-r1"], "r2", rounds=1, verified=True)
    assert up == [{"id": "L-r1", "score": pytest.approx(4 / 3, abs=1e-3), "delta": pytest.approx(1 / 3, abs=1e-3), "flagged": False}]
    down = mem.record_outcome(["L-r0"], "r3", rounds=3, verified=False)
    assert down[0]["score"] < 1.0 and down[0]["delta"] < 0 and down[0]["flagged"] is True
    partial = mem.record_outcome(["L-r1"], "r4", rounds=2, verified=True)
    assert partial[0]["delta"] < 0 < partial[0]["score"] - 1.0  # a later-round pass is half a win
    assert [l["id"] for l in mem.recall(QUERY, k=2)] == ["L-r1", "L-r0"]  # BM25 ties, the score decides
    flagged = mem.recall(QUERY, k=2)[1]
    assert flagged["recall"]["flagged"] is True and "flagged" in M.summary(flagged)


def test_score_reorders_recall_and_a_lesson_that_keeps_failing_is_quarantined(mem):
    learn_run_one(mem, "ra")
    learn_run_one(mem, "rb")
    first = mem.recall(QUERY, k=2)[0]["id"]
    loser, winner = first, ({"L-ra", "L-rb"} - {first}).pop()
    for i in range(3):
        mem.record_outcome([loser], f"fail-{i}", rounds=3, verified=False)
    mem.record_outcome([winner], "win", rounds=1, verified=True)
    assert [l["id"] for l in mem.recall(QUERY, k=2)] == [winner]


def test_record_outcome_is_idempotent_per_run(mem):
    learn_run_one(mem)
    a = mem.record_outcome(["L-r1"], "r2", 1, True)
    b = mem.record_outcome(["L-r1"], "r2", 1, True)
    assert b[0]["score"] == a[0]["score"] and b[0]["delta"] == 0.0
    assert M.stats(mem.dir)["runs"] == 1


# ---------- contractor memory and the work order ----------

def test_contractor_notes_come_from_past_runs(mem):
    mem.record_outcome([], "r1", 2, True, contractor=CONTRACTOR, visits=[SHORTCUT, REAL])
    mem.record_outcome(["L-r1"], "r2", 1, True, contractor=CONTRACTOR, visits=[REAL])
    mem.record_outcome([], "x", 1, True, contractor="Someone Else", visits=[REAL])
    notes = mem.contractor_notes(CONTRACTOR)
    assert len(notes) == 2
    assert "r1" in notes[0] and "inspect-only shortcut" in notes[0] and "verified in round 2" in notes[0]
    assert "r2" in notes[1] and "followed the work order" in notes[1] and "verified in round 1" in notes[1]
    assert mem.contractor_notes("nobody") == [] and mem.contractor_notes("") == []


def test_apply_to_work_order_merges_lessons_and_contractor_history(mem):
    learn_run_one(mem)
    mem.record_outcome([], "r1", 2, True, contractor=CONTRACTOR, visits=[SHORTCUT, REAL])
    lessons = mem.recall(QUERY)
    base = {**WO, "forbid": ["Do not energize with the cabinet open"], "acceptance": ["INV-3 SCADA data_age_min under 5"],
            "lessons_applied": [], "round": 1}
    new, applied = mem.apply_to_work_order(base, lessons, CONTRACTOR)
    assert applied == ["L-r1"] and new["lessons_applied"] == ["L-r1"]
    assert new["forbid"] == ["Do not energize with the cabinet open", FORBID] and forbids_shortcut(new)
    assert new["acceptance"] == ["INV-3 SCADA data_age_min under 5", "INV-3 display lit in a camera view"]  # deduped
    assert len(new["contractor_notes"]) == 1 and "shortcut" in new["contractor_notes"][0]
    assert base["forbid"] == ["Do not energize with the cabinet open"] and "contractor_notes" not in base  # input untouched


def test_contractor_history_alone_forbids_the_shortcut(mem):
    mem.record_outcome([], "r1", 2, True, contractor=CONTRACTOR, visits=[SHORTCUT, REAL])
    new, applied = mem.apply_to_work_order({**WO, "forbid": [], "lessons_applied": []}, [], CONTRACTOR)
    assert applied == [] and forbids_shortcut(new)
    assert "r1" in new["forbid"][0] and CONTRACTOR in new["forbid"][0]
    clean, _ = M.Memory(mem.dir.parent / "other").apply_to_work_order({**WO, "forbid": []}, [], CONTRACTOR)
    assert clean["forbid"] == [] and clean["contractor_notes"] == []


# ---------- stats ----------

def test_stats_show_the_flywheel_turning(mem):
    assert M.stats(mem.dir) == {"runs": 0, "lessons": 0, "rounds_by_run": [], "lesson_scores": []}
    mem.record_outcome([], "r1", 2, True, contractor=CONTRACTOR, visits=[SHORTCUT, REAL])
    learn_run_one(mem)
    mem.record_outcome(["L-r1"], "r2", 1, True, contractor=CONTRACTOR, visits=[REAL])
    s = mem.stats()
    assert s["runs"] == 2 and s["lessons"] == 1
    assert s["rounds_by_run"] == [{"run_id": "r1", "rounds": 2, "verified": True}, {"run_id": "r2", "rounds": 1, "verified": True}]
    (sc,) = s["lesson_scores"]
    assert sc["id"] == "L-r1" and sc["uses"] == 1 and sc["wins"] == 1 and sc["score"] > 1.0 and not sc["flagged"]
    ev = M.stats_event(mem.dir)
    assert ev["op"] == "stats" and ev["stats"] == s
    assert ev["lessons"][0]["id"] == "L-r1" and isinstance(ev["lessons"][0]["text"], str)
    assert "r1: 2 rounds -> r2: 1 round" in ev["effect"]
    json.dumps(ev)


# ---------- backward compatibility ----------

def test_old_api_and_old_lessons_still_work(tmp_path):
    mem = M.Memory(tmp_path / "kb")
    old = {"id": "L-old", "run_id": "old", "symptoms": ["INV-3 SCADA frozen", "display dark"], "fix": "reseat_comms_card on INV-3",
           "steps": ["reseat"], "forbid": [FORBID], "acceptance": ["fresh SCADA"], "outcome": "verified after 2 round(s)",
           "verified": True, "verdict_seqs": [5]}
    saved = mem.write(old)
    assert saved["id"] == "L-old"
    assert [l["id"] for l in mem.recall(QUERY)] == ["L-old"]
    assert [l["id"] for l in mem.recall(QUERY, 1)] == ["L-old"]  # old positional k
    assert [l["id"] for l in mem.recall(QUERY, k=3)] == ["L-old"]
    assert [l["id"] for l in mem.recall(QUERY, role="acceptance")] == ["L-old"]  # no roles: every role sees it
    assert M.summary(old) == f"L-old: when INV-3 SCADA frozen; display dark, fix = reseat_comms_card on INV-3; do not: {FORBID}"
    assert M.lesson_hints([old], "repair") == [f"L-old: do not: {FORBID}"] and M.lesson_hints([old], "verify") == []
    assert (tmp_path / "kb" / "lessons" / "L-old.json").exists()
    assert M.DEFAULT_DIR.name == "e2e-kb"


def test_written_lessons_do_not_keep_the_recall_ranking(mem):
    learn_run_one(mem)
    (l,) = mem.recall(QUERY)
    l["id"] = "L-copy"
    mem.write(l)
    assert "recall" not in json.loads((mem.dir / "lessons" / "L-copy.json").read_text())


# ---------- end to end: the stub demo twice ----------

def _events(run_dir: Path) -> list[dict]:
    return [json.loads(x) for x in (run_dir / "events.jsonl").read_text().splitlines() if x.strip()]


def test_stub_demo_twice_the_flywheel_turns_two_rounds_then_one(tmp_path, monkeypatch):
    from gridtwin.demo import jev
    from gridtwin.demo.pipeline import run_demo

    # Stub brains still reach Jev or a Crusoe model in the verify stage and load diffusion on redispatch:
    # keep this test offline and deterministic with the JEV rule and the photo bank.
    monkeypatch.setattr(jev, "decide", lambda state, log=None: jev.rule(state))
    monkeypatch.setenv("GRIDTWIN_NO_DIFFUSION", "1")
    kb, runs = tmp_path / "kb", tmp_path / "runs"
    first = run_demo(stub=True, memory_dir=kb, run_id="r1", runs_dir=runs)
    second = run_demo(stub=True, memory_dir=kb, run_id="r2", runs_dir=runs)
    assert (first["status"], first["rounds"]) == ("VERIFIED", 2)
    assert (second["status"], second["rounds"]) == ("VERIFIED", 1)

    mem = M.Memory(kb)
    if not M.stats(kb)["runs"]:  # until pipeline.py calls record_outcome itself, record what the runs did
        for r in (first, second):
            evs = _events(runs / r["run_id"])
            wo = [e["data"] for e in evs if e["type"] == "demo.workorder"][0]
            visits = [e["data"] for e in evs if e["type"] == "demo.contractor"]
            mem.record_outcome(wo["lessons_applied"], r["run_id"], r["rounds"], r["status"] == "VERIFIED",
                               contractor=CONTRACTOR, visits=visits)
    s = M.stats(kb)
    assert [(r["run_id"], r["rounds"], r["verified"]) for r in s["rounds_by_run"]] == [("r1", 2, True), ("r2", 1, True)]
    scores = {x["id"]: x for x in s["lesson_scores"]}
    assert scores["L-r1"]["score"] > 1.0 and scores["L-r1"]["wins"] == 1
    assert any("shortcut" in n for n in mem.contractor_notes(CONTRACTOR))
    top = mem.recall(QUERY + " reseat", role="verify")[0]
    assert top["id"] == "L-r1" and any(w in " ".join(top["forbid"]).lower() for w in SHORTCUT_WORDS)

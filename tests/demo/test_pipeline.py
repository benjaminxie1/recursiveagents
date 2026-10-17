"""End-to-end demo pipeline in stub mode: real sim, real gate rules, real mail files, no model calls."""
import email
import email.policy
import json
from pathlib import Path

import pytest

from gridtwin.demo import brains as B
from gridtwin.demo.contractors import SHORTCUT_FORBID, SHORTCUT_FORBID_PREFIX, SHORTCUT_WORDS, forbids_shortcut
from gridtwin.demo.pipeline import run_demo


def events(run_dir: Path) -> list[dict]:
    return [json.loads(l) for l in (run_dir / "events.jsonl").read_text().splitlines() if l.strip()]


def of(evs, type_):
    return [e["data"] for e in evs if e["type"] == type_]


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    mem = root / "kb"
    first = run_demo(stub=True, memory_dir=mem, run_id="r1", runs_dir=root / "runs")
    second = run_demo(stub=True, memory_dir=mem, run_id="r2", runs_dir=root / "runs")
    return root / "runs", first, second


def test_first_run_catches_the_shortcut_and_verifies_in_round_two(two_runs):
    runs, first, _ = two_runs
    assert first["status"] == "VERIFIED" and first["rounds"] == 2
    evs = events(runs / "r1")
    verdicts = of(evs, "demo.verdict")
    assert [v["pass"] for v in verdicts] == [False, True]
    assert set(verdicts[0]["failed"]) >= {"scada_fresh_and_running", "jev_fix_verified"}
    visits = of(evs, "demo.contractor")
    assert visits[0]["shortcut"] is True and visits[1]["shortcut"] is False


def test_judge_agrees_no_false_done_no_safety_violation(two_runs):
    runs, first, second = two_runs
    for r in (first, second):
        assert r["judge"]["success"] is True
        assert r["judge"]["false_done"] is False
        assert r["judge"]["safety_violations"] == 0


def test_second_run_recalls_the_lesson_and_passes_first_visit(two_runs):
    runs, _, second = two_runs
    assert second["status"] == "VERIFIED" and second["rounds"] == 1
    evs = events(runs / "r2")
    recall = [m for m in of(evs, "demo.memory") if m["op"] == "recall"][0]
    assert recall["lessons"] and recall["lessons"][0]["id"] == "L-r1"
    wo = of(evs, "demo.workorder")[0]
    assert wo["lessons_applied"] == ["L-r1"] and forbids_shortcut(wo)


def test_recursion_is_one_level_in_every_team(two_runs):
    runs, _, _ = two_runs
    agents = of(events(runs / "r1"), "demo.agent")
    assert max(a["depth"] for a in agents) == 1
    ids = {a["id"]: a for a in agents}
    for a in agents:
        if a["depth"] == 1:
            assert ids[a["parent"]]["depth"] == 0 and ids[a["parent"]]["team"] == a["team"]
    teams = {(a["team"], a["round"]) for a in agents if a["depth"] == 0}
    assert teams == {("diagnose", 0), ("plan", 0), ("verify", 1), ("verify", 2)}
    plan_subs = {a["id"] for a in agents if a["depth"] == 1 and a["team"] == "plan"}
    assert plan_subs == {"planner.safety", "planner.repair", "planner.acceptance"}
    leads_done = [a for a in agents if a["depth"] == 0 and a["status"] == "done"]
    assert len(leads_done) == 4 and all(a["output"]["depth_cap_enforced"] is True for a in leads_done)


def test_depth_cap_is_code_not_prompt():
    root = B.Agent("p", "planner", "planner", 0)
    kid = root.spawn("repair", "x")
    with pytest.raises(B.DepthExceeded):
        kid.spawn("deeper", "y")


def test_every_email_is_a_parseable_eml_file_never_sent(two_runs):
    runs, _, _ = two_runs
    mails = of(events(runs / "r1"), "demo.email")
    assert [m["direction"] for m in mails] == ["out", "in", "out", "in"]
    for m in mails:
        p = Path(m["eml"])
        p = p if p.is_absolute() else Path(__file__).resolve().parents[2] / p
        msg = email.message_from_bytes(p.read_bytes(), policy=email.policy.default)
        assert msg["X-GridTwin-Simulated"].startswith("true")
        assert msg["Subject"] == m["subject"]
    back = mails[2]
    assert back["round"] == 2 and any(a["name"].startswith("expected-state") for a in back["attachments"])


def test_verification_reads_the_plant_not_the_email(two_runs):
    """Round 1's contractor said done; every check still failed because the plant was not fixed."""
    runs, _, _ = two_runs
    evs = events(runs / "r1")
    reply = [m for m in of(evs, "demo.email") if m["direction"] == "in"][0]
    assert "done" in reply["body"].lower()
    r1 = [c for c in of(evs, "demo.check") if c["round"] == 1]
    assert r1 and not any(c["pass"] for c in r1)


def test_verifier_picks_a_camera_the_contractor_did_not_use(two_runs):
    runs, _, _ = two_runs
    evs = events(runs / "r1")
    contractor_vps = {a["args"].get("viewpoint") for v in of(evs, "demo.contractor") for a in v["actions"] if a["tool"] == "get_camera_view"}
    verifier_vps = {c["viewpoint"] for c in of(evs, "demo.camera") if c.get("round") == 2}
    assert verifier_vps and not (verifier_vps & contractor_vps)


def test_shortcut_rule():
    assert not forbids_shortcut({"forbid": ["Do not work energized"]})
    for w in SHORTCUT_WORDS:  # the planner's free text never bans the shortcut, whatever words it uses
        assert not forbids_shortcut({"forbid": [f"no {w} visits"]})
    assert not forbids_shortcut({"forbid": ["Do not skip the power cycle; comms will not restore until the module reinitializes."]})
    assert forbids_shortcut({"forbid": [SHORTCUT_FORBID]})
    assert forbids_shortcut({"forbid": [f"{SHORTCUT_FORBID_PREFIX} (Mesa Electrical Field Services did on run r1)."]})


def test_server_rejects_bad_scenario():
    from gridtwin.demo.serve import start_run

    with pytest.raises(ValueError):
        start_run({"scenario": "../../etc"})


def test_planner_overrides_a_repair_subagent_that_picks_the_wrong_fix(tmp_path, monkeypatch):
    """Live run e2e-live-3: Lightning chose replace_module for a comms fault; the planner must catch it."""
    real = B.StubBrains.subagent

    def wrong(self, role, task, diagnosis, lessons, model_role="worker"):
        out = real(self, role, task, diagnosis, lessons, model_role)
        return {**out, "work_order": "replace_module"} if role == "repair" else out

    monkeypatch.setattr(B.StubBrains, "subagent", wrong)
    res = run_demo(stub=True, memory_dir=tmp_path / "kb", run_id="wrong-fix", runs_dir=tmp_path / "runs")
    evs = events(tmp_path / "runs" / "wrong-fix")
    assert of(evs, "demo.workorder")[0]["work_order"] == "reseat_comms_card"
    planner_done = [a for a in of(evs, "demo.agent") if a["id"] == "planner" and a["status"] == "done"][0]
    assert "replace_module" in planner_done["output"]["override"]
    assert res["status"] == "VERIFIED" and res["judge"]["success"] is True


# ---------- hardening from the 15:00 live sweep and review ----------

def test_work_order_acceptance_demands_only_what_the_verifier_proves():
    from gridtwin.demo.pipeline import ground_acceptance, verifier_acceptance
    invented = ["Heatsink temperature for INV-3 reads between 40.0 C and 41.0 C", "AC power output at least 1150 kW",
                "Ping latency under 10 ms", "INV-3 display shows live readings after the visit",
                "INV-3 SCADA data_age_min under 5", "Cabinet doors closed and latched after the visit"]
    acc, dropped = ground_acceptance(invented, "INV-3")
    assert acc[:4] == verifier_acceptance("INV-3")
    assert acc[4:] == ["Cabinet doors closed and latched after the visit"]  # restatements of the bars are not repeated
    assert set(dropped) == set(invented[:3])
    assert ground_acceptance("INV-3 answers comms_ping", "INV-3")[0] == verifier_acceptance("INV-3")


def test_model_fields_given_as_strings_stay_whole_steps():
    from gridtwin.demo.pipeline import _as_list
    assert _as_list("Open 52-I3") == ["Open 52-I3"]
    assert _as_list(None) == [] and _as_list({"a": 1}) == [] and _as_list(["a", None, " ", 3]) == ["a", "3"]


def test_fresh_memory_also_resets_scores_and_contractor_history(tmp_path):
    from gridtwin.demo import memory as M
    from gridtwin.demo import run as R
    kb = tmp_path / "kb"
    first = R.main(["--stub", "--memory", str(kb), "--run-id", "fm1", "--runs-dir", str(tmp_path / "runs")])
    assert first == 0 and M.Memory(kb).contractor_notes("Mesa Electrical Field Services")  # it remembers the shortcut
    R.main(["--stub", "--fresh-memory", "--memory", str(kb), "--run-id", "fm2", "--runs-dir", str(tmp_path / "runs")])
    evs = events(tmp_path / "runs" / "fm2")
    assert of(evs, "demo.contractor")[0]["shortcut"] is True  # a fresh store shows the catch again
    assert [r["run_id"] for r in M.Memory(kb).ledger.read()["runs"]] == ["fm2"]


def test_a_crash_still_ends_the_log_with_final_and_result(tmp_path, monkeypatch):
    from gridtwin.demo.pipeline import Demo

    def boom(self, goal=None):
        raise RuntimeError("planner exploded")
    monkeypatch.setattr(Demo, "_run", boom)
    demo = Demo(stub=True, memory_dir=tmp_path / "kb", run_id="crash", runs_dir=tmp_path / "runs")
    with pytest.raises(RuntimeError):
        demo.run()
    final = of(events(tmp_path / "runs" / "crash"), "demo.final")
    assert final and final[-1]["status"] == "FAILED" and "planner exploded" in final[-1]["error"]
    assert json.loads((tmp_path / "runs" / "crash" / "result.json").read_text())["status"] == "FAILED"


def test_redispatch_image_says_which_photo_the_verifier_saw(two_runs):
    runs, _, _ = two_runs
    diff = of(events(runs / "r1"), "demo.diffusion")
    assert diff and "seen" in diff[0]


def test_planner_text_about_power_cycles_does_not_cancel_the_shortcut(tmp_path, monkeypatch):
    """Golden run at 0980751: the planner wrote 'Do not skip the power cycle', keyword matching read it as a ban on the
    shortcut, and a fresh run passed in round 1 with no catch. Only the system's own sentence forbids the shortcut."""
    real = B.StubBrains.subagent

    def wordy(self, role, task, diagnosis, lessons, model_role="worker"):
        out = real(self, role, task, diagnosis, lessons, model_role)
        if role == "repair":
            out = {**out, "do_not": ["Do not skip the power cycle; comms will not restore until the module reinitializes."]}
        return out

    monkeypatch.setattr(B.StubBrains, "subagent", wordy)
    res = run_demo(stub=True, memory_dir=tmp_path / "kb", run_id="wordy", runs_dir=tmp_path / "runs")
    visits = of(events(tmp_path / "runs" / "wordy"), "demo.contractor")
    assert visits[0]["shortcut"] is True and res["rounds"] == 2 and res["status"] == "VERIFIED"


def test_the_diagnoser_opens_the_stale_suspect_in_the_twin_before_diagnosing(two_runs):
    from tests.demo.contract import validate_run
    runs, _, _ = two_runs
    evs = events(runs / "r1")
    twin = [e for e in evs if e["type"] == "demo.twin"]
    dx = next(e for e in evs if e["type"] == "demo.diagnosis")
    assert len(twin) == 1 and twin[0]["data"]["asset"] == "INV-3" and twin[0]["data"]["action"] == "open"
    assert twin[0]["seq"] < dx["seq"] and twin[0]["data"]["twin_url"].startswith("twin.html?scene=")
    # (runs in a temp folder have absolute image paths, which the validator flags separately)
    assert not [p for p in validate_run(runs / "r1") if "demo.twin" in p or "unknown demo event" in p]


def test_the_twin_shows_case_data_only_for_the_scenario_it_was_made_for():
    from gridtwin.demo.pipeline import twin_url
    assert twin_url("s06_stale_scada", "r1").endswith("&run=r1&case=assets/demo/s06_case")
    assert twin_url("s06b_stale_scada_inv2", "r2").endswith("&run=r2&case=")  # empty: no s06 data under INV-2

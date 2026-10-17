"""The demo contract validator (tests/demo/contract.py) on synthetic runs, the frontend fixture and real runs.

Synthetic runs follow CONTRACT.md; each mutation test breaks one rule and expects the validator to name it.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path

import pytest

from tests.demo.contract import REPO_ROOT, has_demo_events, validate_run

FIXTURE = REPO_ROOT / "tests" / "demo" / "fixtures" / "demo_events.jsonl"
PNG = bytes.fromhex("89504e470d0a1a0a") + b"\0" * 24  # the validator checks paths, not pixels
WO = "WO-1"


class Run:
    """A synthetic demo run under <root>/runs/<id>, written in the shapes CONTRACT.md specifies."""

    def __init__(self, root: Path, run_id: str = "r1"):
        self.root, self.run_id = Path(root), run_id
        self.dir = self.root / "runs" / run_id
        (self.dir / "mail").mkdir(parents=True)
        (self.dir / "img").mkdir()
        self.events: list[dict] = []
        self.mail_n = 0

    def emit(self, actor: str, type_: str, **data) -> dict:
        self.events.append({"seq": len(self.events) + 1, "t_sim": None, "wall_ts": 0.0,
                            "actor": actor, "type": type_, "data": data})
        return data

    def image(self, rel: str) -> str:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG)
        return rel

    @contextmanager
    def stage(self, name: str, rnd: int):
        self.emit("orchestrator", "demo.stage", stage=name, status="start", round=rnd)
        yield
        self.emit("orchestrator", "demo.stage", stage=name, status="end", round=rnd)

    def email(self, direction: str, rnd: int, actor: str, attachments=()) -> dict:
        self.mail_n += 1
        rel = f"runs/{self.run_id}/mail/{self.mail_n:02d}-{direction}-{WO.lower()}-r{rnd}.eml"  # as the pipeline names them
        ops, crew = "GridTwin Ops <ops@gridtwin.test>", "Dana R. <crew@contractor.test>"  # header quotes "Dana R."
        sender, to = (ops, crew) if direction == "out" else (crew, ops)
        subject = f"[{WO}] INV-3 comms card, round {rnd}"
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = sender, to, subject
        msg.set_content("Work order body.")
        for att in attachments:
            msg.add_attachment(PNG, maintype="image", subtype="png", filename=att["name"])
        (self.root / rel).write_bytes(bytes(msg))
        return self.emit(actor, "demo.email", direction=direction, wo_id=WO, round=rnd, subject=subject,
                         body="Work order body.", eml=rel, attachments=list(attachments),
                         **{"from": sender, "to": to})

    def find(self, type_: str, **match) -> list[dict]:
        return [e for e in self.events if e["type"] == type_ and all(e["data"].get(k) == v for k, v in match.items())]

    def drop(self, pred):
        self.events = [e for e in self.events if not pred(e)]

    def problems(self) -> list[str]:
        for n, e in enumerate(self.events, 1):
            e["seq"] = n
        (self.dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in self.events))
        return validate_run(self.dir)


def demo_run(root, verdicts=(False, True), recall=(), memory_write=None) -> Run:
    """A full run: one verify round per entry in `verdicts`; memory write on a final pass by default.
    Like the pipeline, stages outside the contractor loop carry round 0."""
    r = Run(root)
    healthy = r.image("assets/photos/INV-3/healthy/01.jpg")
    dark = r.image("assets/photos/INV-3/display_off/01.jpg")
    with r.stage("diagnose", 0):
        r.emit("diagnoser", "demo.sensor", asset="INV-3", data_age_min=95.0, last_update_ts="06:25",
               latest={"ac_kw": 410.0}, stale=True)
        r.emit("diagnoser", "demo.camera", viewpoint="inv3_front", asset="INV-3", mode="rgb", image=dark,
               description="Inverter display is dark.", round=1)
        r.emit("planner", "demo.diagnosis", asset="INV-3", fault="comms_card_failed", confidence=0.8,
               summary="Telemetry frozen; display dark.", evidence=["data_age_min 95", "display dark"])
    with r.stage("plan", 0):
        r.emit("memory", "demo.memory", op="recall", lessons=[{"id": lid, "text": "Forbid restart."} for lid in recall],
               effect=f"{len(recall)} lesson(s)")
        planner = dict(id="planner", parent=None, depth=0, role="planner", model="nemotron-super", task="plan",
                       team="plan", round=0)
        r.emit("planner", "demo.agent", **planner, status="spawned", output=None)
        for role in ("safety", "repair", "acceptance"):
            sub = dict(id=f"sub-{role}", parent="planner", depth=1, role=role, model="nemotron-lightning", task=role,
                       team="plan", round=0)
            r.emit(f"subagent:{role}", "demo.agent", **sub, status="spawned", output=None)
            r.emit(f"subagent:{role}", "demo.agent", **sub, status="done", output=f"{role} notes")
        r.emit("planner", "demo.agent", **planner, status="done", output="merged work order")
        r.emit("planner", "demo.workorder", wo_id=WO, asset="INV-3", work_order="Replace the INV-3 comms card.",
               steps=["Lock out INV-3", "Replace comms card"], forbid=["restart"] if recall else [],
               acceptance=["fresh telemetry", "display on"], lessons_applied=list(recall), round=1)
    with r.stage("dispatch", 1):
        r.email("out", 1, "planner")
    for n, passed in enumerate(verdicts, 1):
        with r.stage("contractor", n):
            r.emit("contractor", "demo.contractor", wo_id=WO, round=n, shortcut=not passed, note="Done.",
                   actions=[{"tool": "operate", "args": {"asset": "INV-3", "action": "restart"}, "ok": True}])
            r.email("in", n, "contractor")
        with r.stage("verify", n):
            r.emit("verifier", "demo.camera", viewpoint="inv3_front", asset="INV-3", mode="rgb",
                   image=healthy if passed else dark, description="Display on." if passed else "Display dark.",
                   round=n)
            for kind, name in (("telemetry", "telemetry_fresh"), ("visual", "display_on")):
                r.emit("verifier", "demo.check", wo_id=WO, round=n, kind=kind, name=name, detail="", evidence={},
                       **{"pass": passed})
            r.emit("verifier", "demo.jev", wo_id=WO, round=n, input={"asset": "INV-3"},
                   output={"verdict": "verified" if passed else "not_verified", "p_verified": 0.9 if passed else 0.1,
                           "uncertainty": 0.1, "backend": "local", "reason": "stations agree"})
            r.emit("verifier", "demo.check", wo_id=WO, round=n, kind="jev", name="jev_verified", detail="",
                   evidence={}, **{"pass": passed})
            r.emit("verifier", "demo.verdict", wo_id=WO, round=n, **{"pass": passed},
                   failed=[] if passed else ["telemetry_fresh", "display_on", "jev_verified"])
        if not passed and n < len(verdicts):
            with r.stage("redispatch", n + 1):
                img = r.image(f"runs/{r.run_id}/img/expected-r{n}.png")
                r.emit("verifier", "demo.diffusion", wo_id=WO, round=n, image=img, backend="photo_bank",
                       prompt="INV-3 with the display on", source=healthy)
                r.email("out", n + 1, "verifier", attachments=[{"name": f"expected-r{n}.png", "path": img}])
    last = verdicts[-1]
    if memory_write if memory_write is not None else last:
        with r.stage("memory", 0):
            r.emit("memory", "demo.memory", op="write", lessons=[{"id": "L1", "text": "Forbid restart."}],
                   effect="stored")
    with r.stage("final", 0):
        r.emit("orchestrator", "demo.final", status="VERIFIED" if last else "ESCALATED", rounds=len(verdicts),
               summary="", usd=0.01, wall_s=12.0)
    return r


def assert_names(problems: list[str], *fragments: str):
    for fragment in fragments:
        assert any(fragment in p for p in problems), f"no problem mentions {fragment!r}: {problems}"


# valid runs ---------------------------------------------------------------------------------------------


def test_shortcut_then_redispatch_then_pass_is_valid(tmp_path):
    assert demo_run(tmp_path).problems() == []


def test_first_time_pass_with_a_recalled_lesson_is_valid(tmp_path):
    assert demo_run(tmp_path, verdicts=(True,), recall=("L1",)).problems() == []


def test_three_failures_then_escalation_is_valid(tmp_path):
    assert demo_run(tmp_path, verdicts=(False, False, False)).problems() == []


# depth cap and sub-agents ---------------------------------------------------------------------------------


def test_a_sub_agent_spawning_a_child_is_depth_two(tmp_path):
    r = demo_run(tmp_path)
    for e in r.find("demo.agent", id="sub-acceptance"):
        e["data"].update(parent="sub-safety", depth=2)
    assert_names(r.problems(), "depth cap is 1", "sub-agents cannot spawn")


def test_a_child_of_a_sub_agent_is_caught_even_if_it_claims_depth_one(tmp_path):
    r = demo_run(tmp_path)
    for e in r.find("demo.agent", id="sub-acceptance"):
        e["data"]["parent"] = "sub-safety"
    assert_names(r.problems(), "sub-agents cannot spawn")


def test_one_sub_agent_is_not_enough(tmp_path):
    r = demo_run(tmp_path)
    r.drop(lambda e: e["type"] == "demo.agent" and e["data"]["id"] in ("sub-repair", "sub-acceptance"))
    assert_names(r.problems(), "only 1 sub-agent(s) in the plan team")


def test_a_verify_team_agent_acting_in_the_plan_stage(tmp_path):
    r = demo_run(tmp_path)
    for e in r.find("demo.agent", id="sub-repair"):
        e["data"]["team"] = "verify"
    assert_names(r.problems(), "of team 'verify' acts in stage 'plan'", "its parent 'planner' is in 'plan'")


def test_a_sub_agent_role_outside_the_contract(tmp_path):
    r = demo_run(tmp_path)
    for e in r.find("demo.agent", id="sub-repair"):
        e["data"]["role"] = "freelancer"
        e["actor"] = "subagent:freelancer"
    assert_names(r.problems(), "role 'freelancer', not one of ALL_SUBAGENT_ROLES")


def test_a_sub_agent_with_an_unknown_parent(tmp_path):
    r = demo_run(tmp_path)
    for e in r.find("demo.agent", id="sub-repair"):
        e["data"]["parent"] = "ghost"
    assert_names(r.problems(), "not a known agent")


# emails ----------------------------------------------------------------------------------------------------


def test_a_missing_eml_file(tmp_path):
    r = demo_run(tmp_path)
    (tmp_path / r.find("demo.email", direction="out", round=1)[0]["data"]["eml"]).unlink()
    assert_names(r.problems(), "does not exist")


def test_an_eml_without_headers(tmp_path):
    r = demo_run(tmp_path)
    (tmp_path / r.find("demo.email", direction="in", round=1)[0]["data"]["eml"]).write_text("not an email\n")
    assert_names(r.problems(), "has no From header", "differs from the event")


def test_a_work_order_without_its_outbound_email(tmp_path):
    r = demo_run(tmp_path)
    r.drop(lambda e: e["type"] == "demo.email" and e["data"]["direction"] == "out" and e["data"]["round"] == 1)
    assert_names(r.problems(), "has no outbound email after it")


def test_an_eml_named_for_another_round(tmp_path):
    r = demo_run(tmp_path)
    d = r.find("demo.email", direction="in", round=2)[0]["data"]
    renamed = d["eml"].replace("-r2.eml", "-r1.eml")
    (tmp_path / d["eml"]).rename(tmp_path / renamed)
    d["eml"] = renamed
    assert_names(r.problems(), "named for round 1 but the event is round 2")


def test_an_eml_sent_to_someone_else(tmp_path):
    r = demo_run(tmp_path)
    r.find("demo.email", direction="out", round=1)[0]["data"]["to"] = "Someone <else@contractor.test>"
    assert_names(r.problems(), "does not match the event's 'Someone <else@contractor.test>'")


def test_an_inbound_email_from_the_planner(tmp_path):
    r = demo_run(tmp_path)
    r.find("demo.email", direction="in", round=1)[0]["actor"] = "planner"
    assert_names(r.problems(), "emitted by actor 'planner'")


def test_an_attachment_missing_from_the_eml(tmp_path):
    r = demo_run(tmp_path)
    r.find("demo.email", direction="out", round=2)[0]["data"]["attachments"].append(
        {"name": "extra.png", "path": "runs/r1/img/expected-r1.png"})
    assert_names(r.problems(), "'extra.png' is not in the .eml")


# verdicts and done means done ------------------------------------------------------------------------------


def test_a_contractor_reply_without_a_verdict(tmp_path):
    r = demo_run(tmp_path)
    r.drop(lambda e: e["type"] == "demo.verdict" and e["data"]["round"] == 2)
    assert_names(r.problems(), "has no verdict after it", "done means done")


def test_verified_after_a_failed_last_verdict_breaks_done_means_done(tmp_path):
    r = demo_run(tmp_path, verdicts=(False,))
    r.find("demo.final")[0]["data"]["status"] = "VERIFIED"
    assert_names(r.problems(), "done means done")


def test_a_passing_verdict_must_agree_with_its_checks(tmp_path):
    r = demo_run(tmp_path)
    r.find("demo.check", round=2, kind="telemetry")[0]["data"]["pass"] = False
    assert_names(r.problems(), "check(s) say pass=False", "failed=[] but the failing checks are ['telemetry_fresh']")


def test_a_passing_verdict_needs_jev(tmp_path):
    r = demo_run(tmp_path)
    r.drop(lambda e: e["type"] in ("demo.jev", "demo.check") and e["data"]["round"] == 2
           and e["data"].get("kind", "jev") == "jev")
    assert_names(r.problems(), "without a ['jev'] check", "without a JEV 'verified' decision")


def test_the_jev_check_must_agree_with_jev(tmp_path):
    r = demo_run(tmp_path)
    r.find("demo.jev", round=2)[0]["data"]["output"]["verdict"] = "uncertain"
    assert_names(r.problems(), "JEV said 'uncertain'")


def test_a_passing_verdict_needs_a_verifier_camera_view(tmp_path):
    r = demo_run(tmp_path)
    r.drop(lambda e: e["type"] == "demo.camera" and e["actor"] == "verifier" and e["data"]["round"] == 2)
    assert_names(r.problems(), "without a verifier camera view")


def test_checks_before_the_reply_cannot_verify_the_fix(tmp_path):
    r = demo_run(tmp_path, verdicts=(True,))
    reply = r.find("demo.email", direction="in")[0]
    r.events.remove(reply)
    verdict_at = r.events.index(r.find("demo.verdict")[0])
    r.events.insert(verdict_at, reply)  # reply now lands after the checks, inside verify
    assert_names(r.problems(), "before the contractor's reply")


def test_a_redispatch_without_the_expected_state_image(tmp_path):
    r = demo_run(tmp_path)
    r.find("demo.email", direction="out", round=2)[0]["data"]["attachments"] = []
    assert_names(r.problems(), "no attached expected-state image")


def test_an_escalated_run_may_score_lessons_but_not_write_one(tmp_path):
    r = demo_run(tmp_path, verdicts=(False,), memory_write=True)
    write = r.find("demo.memory", op="write")[0]
    assert_names(r.problems(), "memory write without a passing verdict")
    write["data"].update(op="score", lessons=[{"id": "L0", "text": "score 0.5 (-0.5), flagged"}], effect="flagged")
    assert r.problems() == []


def test_a_failed_verdict_cannot_go_straight_to_a_new_contractor_round(tmp_path):
    r = demo_run(tmp_path)
    r.drop(lambda e: e["type"] == "demo.stage" and e["data"]["stage"] == "redispatch")
    assert_names(r.problems(), "stage 'contractor' cannot follow 'verify'")


def test_final_rounds_must_match_the_verdicts(tmp_path):
    r = demo_run(tmp_path)
    r.find("demo.final")[0]["data"]["rounds"] = 1
    assert_names(r.problems(), "rounds=1 but 2 round(s)")


# stages, schema, memory, images ---------------------------------------------------------------------------


def test_a_run_must_start_with_diagnose(tmp_path):
    r = demo_run(tmp_path)
    r.drop(lambda e: e["type"] == "demo.stage" and e["data"]["stage"] == "diagnose")
    assert_names(r.problems(), "stage 'plan' cannot follow None")


def test_contractor_rounds_must_step_by_one(tmp_path):
    r = demo_run(tmp_path)
    for e in r.find("demo.stage", stage="contractor", round=2):
        e["data"]["round"] = 3
    assert_names(r.problems(), "contractor round 3 does not follow round 1")


def test_loop_rounds_cannot_go_back(tmp_path):
    r = demo_run(tmp_path)
    for e in r.find("demo.stage", stage="verify", round=2):
        e["data"]["round"] = 1
    assert_names(r.problems(), "round went back from 2 to 1")


def test_a_stage_that_never_ends(tmp_path):
    r = demo_run(tmp_path)
    r.drop(lambda e: e["type"] == "demo.stage" and e["data"] == {"stage": "verify", "status": "end", "round": 2})
    assert_names(r.problems(), "starts while 'verify' is still open")


def test_wrong_types_and_missing_keys(tmp_path):
    r = demo_run(tmp_path)
    r.find("demo.final")[0]["data"]["usd"] = "free"
    del r.find("demo.verdict", round=1)[0]["data"]["failed"]
    r.find("demo.jev", round=1)[0]["data"]["output"]["backend"] = "cloud"
    assert_names(r.problems(), "usd='free' is not num", "missing key 'failed'", "backend='cloud'")


def test_an_applied_lesson_must_have_been_recalled(tmp_path):
    r = demo_run(tmp_path, verdicts=(True,))
    r.find("demo.workorder")[0]["data"]["lessons_applied"] = ["L9"]
    assert_names(r.problems(), "applies lesson 'L9'")


def test_a_missing_camera_image(tmp_path):
    r = demo_run(tmp_path)
    (tmp_path / "assets/photos/INV-3/healthy/01.jpg").unlink()
    assert_names(r.problems(), "'assets/photos/INV-3/healthy/01.jpg' does not exist")


# the frontend fixture and real runs -------------------------------------------------------------------------


def test_the_frontend_fixture_honours_the_contract():
    if not FIXTURE.is_file():
        pytest.skip("tests/demo/fixtures/demo_events.jsonl not written yet")
    assert validate_run(FIXTURE) == []


REAL_RUNS = sorted(p.parent for p in (REPO_ROOT / "runs").glob("*/events.jsonl") if has_demo_events(p))
IN_PROGRESS_S = 300  # a run without demo.final whose log changed this recently is still being written


def in_progress(run_dir) -> bool:
    log = run_dir / "events.jsonl"
    return '"demo.final"' not in log.read_text() and time.time() - log.stat().st_mtime < IN_PROGRESS_S


@pytest.mark.parametrize("run_dir", REAL_RUNS or [None], ids=lambda p: p.name if p else "none")
def test_real_demo_runs_honour_the_contract(run_dir):
    if run_dir is None:
        pytest.skip("no runs/*/events.jsonl with demo.* events yet")
    if in_progress(run_dir):
        pytest.skip(f"{run_dir.name} is still running")
    assert validate_run(run_dir) == []

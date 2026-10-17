"""Hand-written demo event logs for the demo page (viewer/demo.html), following gridtwin/demo/CONTRACT.md exactly.

Two runs of s06_stale_scada (INV-3 comms card failed, telemetry frozen at good-looking values, display dark):
- demo_events.jsonl: first run, empty memory. Round 1 the contractor takes the restart shortcut and fails telemetry,
  visual and JEV; the verifier emails back with an expected-state image; round 2 passes; memory writes a lesson.
- demo_events_recall.jsonl: the next run. Memory recalls that lesson, the work order forbids the restart, round 1 passes.

    .venv/bin/python tests/demo/fixtures/demo_fixture.py            # rewrite the two .jsonl files
    .venv/bin/python tests/demo/fixtures/demo_fixture.py --install  # also lay them out as runs/demo-fixture{,-recall}/
                                                                    # (events.jsonl, mail/*.eml, img/expected-r1.png)
then open http://localhost:<port>/demo.html?run=demo-fixture (add &replay=1 to step through).
"""
from __future__ import annotations

import argparse
import json
import shutil
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

PLANNER = "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B"
WORKER = "nvidia/Nemotron-3.5-Lightning-30B-A3B"
OMNI = "nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B"
VERIFIER = "openai/gpt-oss-120b"
RULE = "deterministic"  # agents that are code, not a model call
PLANNER_ADDR = "GridTwin planner <planner@gridtwin.demo>"
VERIFIER_ADDR = "GridTwin verifier <verifier@gridtwin.demo>"
CONTRACTOR_ADDR = "Sunline Field Services <dispatch@sunline-field.demo>"
PHOTO = "assets/photos/INV-3/{state}/0{n}_ai_generated.jpg"
LESSON_ID = "L-INV-COMMS-001"
LESSON_TEXT = ("Frozen inverter telemetry with a dark display is a failed comms card. A restart brings the display "
               "back for a few minutes and fixes nothing. Forbid restart as the fix; require a card swap under "
               "lockout, then fresh SCADA data (age under 5 min) and a lit display on a camera the contractor did not use.")


def photo(state: str, n: int) -> str:
    return PHOTO.format(state=state, n=n)


class Log:
    def __init__(self, run_id: str, t0_wall: float):
        self.run_id, self.events, self.wall, self.minute = run_id, [], t0_wall, 0

    def emit(self, actor: str, type_: str, data: dict, dt_wall: float = 1.5, dt_sim: int = 0):
        self.wall += dt_wall
        self.minute += dt_sim
        h, m = divmod(11 * 60 + self.minute, 60)
        self.events.append({"actor": actor, "data": data, "seq": len(self.events) + 1,
                            "t_sim": f"2026-07-14T{h:02d}:{m:02d}", "type": type_, "wall_ts": round(self.wall, 3)})

    def stage(self, stage: str, status: str, rnd: int, dt_wall: float = 0.4):
        self.emit("orchestrator", "demo.stage", {"stage": stage, "status": status, "round": rnd}, dt_wall)

    def path(self, *parts: str) -> str:
        return "/".join(("runs", self.run_id) + parts)


def team(log: Log, actor_lead: str, lead: dict, kids: list[tuple], dt_work: float = 2.0):
    """One lead (depth 0) spawns its sub-agents (depth 1); each reports, then the lead merges. kids: (role, model, task, output)."""
    base = {"parent": None, "depth": 0, "status": "spawned", "output": None}
    log.emit(actor_lead, "demo.agent", {**base, **{k: v for k, v in lead.items() if k != "output"}}, 0.6)
    for role, model, task, _ in kids:
        log.emit(f"subagent:{role}", "demo.agent", {"id": f"{lead['id']}.{role}", "parent": lead["id"], "depth": 1, "role": role,
                                                     "model": model, "task": task, "status": "spawned", "output": None,
                                                     "team": lead["team"], "round": lead["round"]}, 0.3)
    for role, model, task, output in kids:
        log.emit(f"subagent:{role}", "demo.agent", {"id": f"{lead['id']}.{role}", "parent": lead["id"], "depth": 1, "role": role,
                                                     "model": model, "task": task, "status": "done", "output": output,
                                                     "team": lead["team"], "round": lead["round"]}, dt_work)
    log.emit(actor_lead, "demo.agent", {**base, **lead, "status": "done"}, 1.0)


def diagnose(log: Log):
    log.emit("operator", "goal", {"scenario_id": "s06_stale_scada",
                                  "text": "Plant output looks fine on the dashboard but the owner says revenue is down. Find out why and fix it."}, 0)
    log.emit("orchestrator", "demo.stage", {"stage": "diagnose", "status": "start", "round": 0,
                                            "twin_url": f"twin.html?scene=_dev&run={log.run_id}"}, 0.4)
    feeds = {
        "INV-1": (0.4, "11:00", {"ac_kw": 731.6, "dc_v": 1174, "temp_c": 43.1, "state": "RUNNING"}),
        "INV-2": (0.6, "11:00", {"ac_kw": 728.9, "dc_v": 1169, "temp_c": 42.7, "state": "RUNNING"}),
        "INV-3": (183.0, "07:57", {"ac_kw": 742.0, "dc_v": 1180, "temp_c": 41.2, "state": "RUNNING"}),
        "INV-4": (0.5, "11:00", {"ac_kw": 735.2, "dc_v": 1177, "temp_c": 43.4, "state": "RUNNING"}),
    }
    for asset, (age, ts, latest) in feeds.items():
        log.emit("diagnoser", "demo.sensor", {"asset": asset, "data_age_min": age, "last_update_ts": f"2026-07-14T{ts}",
                                              "latest": latest, "stale": age > 5}, 0.7)
    log.emit("diagnoser", "demo.camera", {
        "viewpoint": "inv3_display", "asset": "INV-3", "mode": "rgb", "image": photo("display_off", 1), "round": 0,
        "description": "Front panel display is dark and no status LEDs are lit on the comms card bay. Cabinet door closed, no visible damage or scorch marks."},
        2.4, 2)
    log.emit("diagnoser", "demo.camera", {
        "viewpoint": "inv3_side", "asset": "INV-3", "mode": "rgb", "image": photo("display_off", 2), "round": 0,
        "description": "Cabinet side: intake louvres clear, no warning lamp lit, cable glands intact. Nothing on this side explains a fault."},
        2.2, 1)
    team(log, "diagnoser", {"id": "diagnosis-lead", "role": "diagnosis_lead", "model": PLANNER, "team": "diagnose", "round": 0,
                            "task": "Find the faulty asset and the fault from independent reads",
                            "output": {"asset": "INV-3", "fault": "comms_card_failed", "confidence": 0.86, "depth_cap_enforced": True,
                                       "votes": {"sensors": "INV-3", "vision": "INV-3", "spatial": "INV-3"}}}, [
        ("sensors", WORKER, "Read the SCADA feed: which asset's values are frozen",
         {"asset": "INV-3", "confidence": 0.95, "evidence": ["INV-3 data_age_min 183 (stale over 10)", "ac_kw flat at 742.0 since 07:57"]}),
        ("vision", OMNI, "Read the Omni camera descriptions: whose display is dark",
         {"asset": "INV-3", "confidence": 0.8, "evidence": ["inv3_display: front display dark, no comms LEDs"]}),
        ("spatial", RULE, "Map the splat twin's registered camera stations to the assets they see",
         {"asset": "INV-3", "coverage": {"INV-3": ["inv3_display", "inv3_cabinet"]}, "confidence": 0.6}),
    ], 1.6)
    log.emit("planner", "demo.diagnosis", {
        "asset": "INV-3", "fault": "comms_card_failed", "confidence": 0.86,
        "summary": "INV-3's SCADA feed froze at 07:57 on plausible numbers while its display is dark. The comms card has failed, so the dashboard shows stale output and the inverter is most likely not exporting.",
        "evidence": [
            "INV-3 data_age_min 183; INV-1, INV-2 and INV-4 are under 1 min",
            "INV-3 ac_kw stuck at 742.0 for 3 h while irradiance rose 38%",
            "Camera inv3_display: front display dark, no comms LEDs",
            "Revenue meter 24% under the irradiance model, about one inverter in four",
        ],
        "model": PLANNER}, 3.0, 1)
    log.stage("diagnose", "end", 0)


def plan(log: Log, recall: bool):
    log.stage("plan", "start", 0)
    if recall:
        log.emit("memory", "demo.memory", {"op": "recall", "lessons": [{"id": LESSON_ID, "text": LESSON_TEXT}],
                                           "effect": "Work order forbids restart as the fix and asks for a comms card swap with a fresh-data acceptance test."}, 1.2)
    else:
        log.emit("memory", "demo.memory", {"op": "recall", "lessons": [],
                                           "effect": "No verified lessons for this symptom yet, so the planner starts from scratch."}, 1.2)
    log.emit("planner", "demo.agent", {"id": "planner", "parent": None, "depth": 0, "role": "planner", "model": PLANNER,
                                       "task": "Turn the INV-3 diagnosis into one contractor work order", "status": "spawned", "output": None,
                                       "team": "plan", "round": 0}, 0.8)
    tasks = {
        "safety": "Lockout and PPE steps for opening INV-3 under DC voltage",
        "repair": "What the contractor must physically do to restore INV-3",
        "acceptance": "The evidence the verifier will demand before closing the ticket",
    }
    for role, task in tasks.items():
        log.emit(f"subagent:{role}", "demo.agent", {"id": f"planner.{role}", "parent": "planner", "depth": 1, "role": role,
                                                     "model": WORKER, "task": task, "status": "spawned", "output": None,
                                                     "team": "plan", "round": 0}, 0.5)
    outputs = {
        "safety": "1. Open AC breaker 52-I3 and the DC disconnect, apply personal locks.\n2. Verify zero energy at the card bay with a rated meter.\n3. Arc-rated PPE category 2 until verified dead.",
        "repair": ("1. Swap the SCADA comms card (spare P/N CC-485-2) in slot 3.\n2. Re-seat the RS-485 trunk and check termination.\n3. Re-energise and confirm the display shows live kW."
                   if recall else
                   "1. Get INV-3 reporting again and exporting power.\n2. Check the comms card and the RS-485 trunk.\n3. Re-energise and confirm the display shows live kW."),
        "acceptance": "1. SCADA data_age_min under 5 for INV-3 within 15 min of the visit.\n2. ac_kw within 5% of INV-1, INV-2 and INV-4 at the same irradiance.\n3. Display lit with live kW on a camera view the contractor did not use.",
    }
    for i, role in enumerate(tasks):
        log.emit(f"subagent:{role}", "demo.agent", {"id": f"planner.{role}", "parent": "planner", "depth": 1, "role": role,
                                                     "model": WORKER, "task": tasks[role], "status": "done", "output": outputs[role],
                                                     "team": "plan", "round": 0}, 2.6 + i * 0.7)
    log.emit("planner", "demo.agent", {"id": "planner", "parent": None, "depth": 0, "role": "planner", "model": PLANNER,
                                       "task": "Turn the INV-3 diagnosis into one contractor work order", "status": "done",
                                       "output": {"work_order": "WO-INV3-01", "depth_cap_enforced": True,
                                                  "lessons_applied": [LESSON_ID] if recall else []},
                                       "team": "plan", "round": 0}, 2.2)
    wo = workorder(log, 1, strict=recall)
    log.stage("plan", "end", 0)
    return wo


def workorder(log: Log, rnd: int, strict: bool):
    steps = ["Lock out INV-3: open 52-I3 and the DC disconnect, verify zero energy",
             "Replace the SCADA comms card in slot 3 (spare CC-485-2)" if strict else "Restore INV-3 SCADA reporting and export",
             "Re-seat the RS-485 trunk and check termination",
             "Re-energise and send a photo of the lit display"]
    data = {"wo_id": "WO-INV3-01", "asset": "INV-3",
            "work_order": "INV-3 SCADA comms card failed: telemetry frozen since 07:57, display dark",
            "steps": steps,
            "forbid": ["Restart or power-cycle INV-3 as the fix"] if strict else [],
            "acceptance": ["SCADA data_age_min < 5 within 15 min", "ac_kw within 5% of peer inverters",
                           "Display lit with live kW on an independent camera"],
            "lessons_applied": [LESSON_ID] if strict and rnd == 1 else [], "round": rnd}
    log.emit("planner", "demo.workorder", data, 1.4)
    return data


def email(log: Log, actor: str, direction: str, n: int, rnd: int, frm: str, to: str, subject: str, body: str, attachments):
    eml = log.path("mail", f"{n:02d}-{direction}-WO-INV3-01.eml")
    log.emit(actor, "demo.email", {"direction": direction, "wo_id": "WO-INV3-01", "round": rnd, "from": frm, "to": to,
                                   "subject": subject, "body": body, "eml": eml, "attachments": attachments}, 1.6)


def wo_body(wo: dict) -> str:
    lines = [f"Work order {wo['wo_id']} for {wo['asset']}: {wo['work_order']}.", "", "Steps:"]
    lines += [f"{i}. {s}" for i, s in enumerate(wo["steps"], 1)]
    if wo["forbid"]:
        lines += ["", "Do not:"] + [f"- {s}" for s in wo["forbid"]]
    lines += ["", "We close this ticket only when our own checks see:"] + [f"- {s}" for s in wo["acceptance"]]
    lines += ["", "Reply to this email when done, with a photo of the display."]
    return "\n".join(lines)


def contractor_round(log: Log, rnd: int, n: int, shortcut: bool, wo: dict | None):
    if wo:  # round 1 is dispatched by the planner; later rounds start from the verifier's email back
        log.stage("dispatch", "start", rnd)
        email(log, "planner", "out", n, rnd, PLANNER_ADDR, CONTRACTOR_ADDR, f"{wo['wo_id']}: INV-3 comms card failed, SCADA frozen",
              wo_body(wo), [{"name": "inv3_display_dark.jpg", "path": photo("display_off", 1)}])
        log.stage("dispatch", "end", rnd)
    log.stage("contractor", "start", rnd)
    if shortcut:
        actions = [{"tool": "operate", "args": {"asset": "INV-3", "action": "restart"}, "ok": True},
                   {"tool": "wait", "args": {"minutes": 15}, "ok": True}]
        note = "Restarted INV-3 from the HMI. Display came back for a moment. Closing the ticket."
        after = photo("display_off", 3)
        body = "Done. We restarted INV-3 and it is running. Photo attached.\n\nSunline dispatch"
    else:
        actions = [{"tool": "operate", "args": {"asset": "52-I3", "action": "open"}, "ok": True},
                   {"tool": "dispatch_tech", "args": {"asset": "INV-3", "task": "replace comms card CC-485-2"}, "ok": True},
                   {"tool": "wait", "args": {"minutes": 40}, "ok": True},
                   {"tool": "operate", "args": {"asset": "52-I3", "action": "close"}, "ok": True}]
        note = "Locked out INV-3, swapped the comms card, re-seated the RS-485 trunk, re-energised."
        after = photo("healthy", 1)
        body = "Done. Comms card in slot 3 replaced under lockout, trunk re-seated, unit re-energised. Display is live, photo attached.\n\nSunline dispatch"
    log.emit("contractor", "demo.contractor", {"wo_id": "WO-INV3-01", "round": rnd, "actions": actions, "shortcut": shortcut, "note": note},
             3.5, 15 if shortcut else 40)
    email(log, "contractor", "in", n + 1, rnd, CONTRACTOR_ADDR, PLANNER_ADDR, "Re: WO-INV3-01: done", body,
          [{"name": "inv3_after_visit.jpg", "path": after}])
    log.stage("contractor", "end", rnd)


def jev_input(age: float, latest_kw: float, visual_pass: bool, conf: float) -> dict:
    return {
        "asset": "INV-3", "asset_xyz": [3.42, 0.31, 1.58],
        "stations": [
            {"viewpoint": "inv3_display", "xyz": [3.42, -0.06, -0.82], "target_xyz": [3.42, 0.41, 1.58], "distance_m": 2.43,
             "angle_to_asset_deg": 2.3, "sees_asset": True},
            {"viewpoint": "inv3_cabinet", "xyz": [2.31, -0.04, -0.71], "target_xyz": [3.42, 0.36, 1.58], "distance_m": 2.57,
             "angle_to_asset_deg": 11.8, "sees_asset": True},
            {"viewpoint": "inv2_display", "xyz": [0.3, -0.08, -0.82], "target_xyz": [0.3, 0.36, 1.58], "distance_m": 3.95,
             "angle_to_asset_deg": 52.6, "sees_asset": False},
        ],
        "sensors": {"data_age_min": age, "stale": age > 5, "latest": {"ac_kw": latest_kw, "state": "RUNNING"}},
        "visual": [{"viewpoint": "inv3_display", "claim": "display lit with live kW", "pass": visual_pass, "confidence": conf},
                   {"viewpoint": "inv3_cabinet", "claim": "comms LED green", "pass": visual_pass, "confidence": round(conf - 0.07, 2)}],
        "coverage": {"stations_seeing_asset": 2, "independent_views": 2},
        "units": {"xyz": "m, splat frame", "angle": "deg", "age": "min", "power": "kW"},
    }


def verify(log: Log, rnd: int, ok: bool):
    log.stage("verify", "start", rnd)
    if ok:
        log.emit("verifier", "demo.camera", {"viewpoint": "inv3_cabinet", "asset": "INV-3", "mode": "rgb", "image": photo("healthy", 2), "round": rnd,
                                             "description": "Display lit, reads RUNNING 736 kW. Comms LED on the card bay is green."}, 2.2)
        checks = [("telemetry", "telemetry_fresh", True, "INV-3 data_age_min 0.7 (limit 5); ac_kw 736.4, within 1% of peers",
                   {"data_age_min": 0.7, "ac_kw": 736.4, "peer_mean_kw": 731.9}),
                  ("visual", "display_live", True, "Omni on inv3_cabinet, a view the contractor did not use: display lit with live kW (0.93)",
                   {"viewpoint": "inv3_cabinet", "confidence": 0.93})]
        state, out = jev_input(0.7, 736.4, True, 0.93), {"verdict": "verified", "p_verified": 0.94, "uncertainty": 0.04, "backend": "jev",
                                                         "reason": "Fresh telemetry agrees with peers and two stations that see INV-3 show a lit display."}
    else:
        log.emit("verifier", "demo.camera", {"viewpoint": "inv3_cabinet", "asset": "INV-3", "mode": "rgb", "image": photo("display_off", 3), "round": rnd,
                                             "description": "Display dark. No LEDs lit on the comms card bay."}, 2.2)
        checks = [("telemetry", "telemetry_fresh", False, "INV-3 data_age_min 18 after the visit (limit 5); ac_kw still 742.0, the value frozen at 07:57",
                   {"data_age_min": 18.0, "ac_kw": 742.0, "frozen_since": "07:57"}),
                  ("visual", "display_live", False, "Omni on inv3_cabinet, a view the contractor did not use: display dark, no comms LED (0.91)",
                   {"viewpoint": "inv3_cabinet", "confidence": 0.91})]
        state, out = jev_input(18.0, 742.0, False, 0.91), {"verdict": "not_verified", "p_verified": 0.06, "uncertainty": 0.05, "backend": "jev",
                                                           "reason": "Telemetry is still stale and both stations that see INV-3 show a dark display; the contractor's photo is not enough."}
    lead = {"id": f"verify-lead.r{rnd}", "role": "verify_lead", "model": VERIFIER, "team": "verify", "round": rnd,
            "task": "Prove or refute the fix of INV-3 from the plant, not the contractor's word",
            "output": {"pass": ok, "failed": [] if ok else ["telemetry_fresh", "display_live", "jev_decision"], "depth_cap_enforced": True,
                       "rule": "plant checks and meta judge agree"}}
    team(log, "verifier", lead, [
        ("telemetry_judge", RULE, "SCADA for INV-3: data_age_min at most 5 and peers agree", {"pass": checks[0][2], "detail": checks[0][3]}),
        ("visual_judge", RULE, "Omni camera check of INV-3 on a view the contractor did not use", {"pass": checks[1][2], "detail": checks[1][3]}),
        ("jev_judge", RULE, "JEV spatial verdict for INV-3 must be verified", {"verdict": out["verdict"], "p_verified": out["p_verified"]}),
        ("meta_judge", VERIFIER, "Read the judges and the contractor's claim: does the evidence prove the fix?",
         {"verdict": "pass" if ok else "fail", "reason": out["reason"]}),
    ], 0.9)
    for kind, name, passed, detail, ev in checks:
        log.emit("verifier", "demo.check", {"wo_id": "WO-INV3-01", "round": rnd, "kind": kind, "name": name, "pass": passed,
                                            "detail": detail, "evidence": ev}, 1.8)
    log.emit("verifier", "demo.jev", {"wo_id": "WO-INV3-01", "round": rnd, "input": state, "output": out}, 2.4)
    log.emit("verifier", "demo.check", {"wo_id": "WO-INV3-01", "round": rnd, "kind": "jev", "name": "jev_decision", "pass": ok,
                                        "detail": f"{out['verdict']}, p_verified {out['p_verified']:.2f} ({out['backend']})",
                                        "evidence": {"verdict": out["verdict"], "p_verified": out["p_verified"]}}, 0.6)
    failed = [] if ok else ["telemetry_fresh", "display_live", "jev_decision"]
    log.emit("verifier", "demo.verdict", {"wo_id": "WO-INV3-01", "round": rnd, "pass": ok, "failed": failed}, 0.8)
    log.stage("verify", "end", rnd)


def redispatch(log: Log, rnd: int, n: int):
    """The verifier's email back carries the revised work order, so it goes out as the next round's email."""
    log.stage("redispatch", "start", rnd)
    image = log.path("img", f"expected-r{rnd}.png")
    log.emit("verifier", "demo.diffusion", {"wo_id": "WO-INV3-01", "round": rnd, "image": image, "backend": "diffusion",
                                            "prompt": "the same INV-3 inverter cabinet, front display lit showing RUNNING and live kW, green comms LED",
                                            "source": photo("display_off", 3)}, 4.0)
    body = ("Not verified. Our own checks after your visit:\n"
            "- Telemetry: INV-3 data is 18 min old and still reads 742.0 kW, the value frozen at 07:57.\n"
            "- Camera inv3_cabinet: display dark, no comms LED.\n"
            "- JEV decision: not verified (p 0.06).\n\n"
            "A restart does not fix this. Replace the comms card in slot 3 under lockout. "
            "The attached image shows the state we expect to see when it is fixed.")
    workorder(log, rnd + 1, strict=True)
    email(log, "verifier", "out", n, rnd + 1, VERIFIER_ADDR, CONTRACTOR_ADDR, "Re: WO-INV3-01: not verified, please return", body,
          [{"name": "expected-state.png", "path": image}, {"name": "seen-after-visit.jpg", "path": photo("display_off", 3)}])
    log.stage("redispatch", "end", rnd)


def memory_and_final(log: Log, rounds: int, recall: bool, usd: float):
    log.stage("memory", "start", rounds)
    if recall:
        log.emit("memory", "demo.memory", {"op": "write", "lessons": [{"id": LESSON_ID, "text": LESSON_TEXT}],
                                           "effect": "Lesson confirmed by a first-round pass; kept as is."}, 1.4)
    else:
        log.emit("memory", "demo.memory", {"op": "write", "lessons": [{"id": LESSON_ID, "text": LESSON_TEXT}],
                                           "effect": "Next run on this symptom: the work order forbids restart and demands a card swap, so the contractor gets it right first time."}, 1.4)
    log.stage("memory", "end", rounds)
    log.stage("final", "start", rounds)
    wall = round(log.wall - log.events[0]["wall_ts"] + 0.6, 1)
    summary = ("INV-3 verified in round 2 after the restart shortcut failed. Comms card replaced; SCADA live again."
               if rounds == 2 else "INV-3 verified in round 1. The recalled lesson stopped the restart shortcut.")
    judge = {"success": True, "false_done": False, "safety_violations": 0, "claimed_done": True, "resolved": True}
    log.emit("orchestrator", "demo.final", {"status": "VERIFIED", "rounds": rounds, "summary": summary, "usd": usd, "wall_s": wall,
                                            "judge": judge, "lesson": LESSON_ID, "stub": False}, 0.6)
    log.stage("final", "end", rounds)


def build(run_id: str, recall: bool) -> list[dict]:
    log = Log(run_id, 1792270800.0 if not recall else 1792271400.0)
    diagnose(log)
    wo = plan(log, recall)
    if recall:
        contractor_round(log, 1, 1, shortcut=False, wo=wo)
        verify(log, 1, ok=True)
        memory_and_final(log, 1, recall, 0.031)
    else:
        contractor_round(log, 1, 1, shortcut=True, wo=wo)
        verify(log, 1, ok=False)
        redispatch(log, 1, 3)
        contractor_round(log, 2, 3, shortcut=False, wo=None)
        verify(log, 2, ok=True)
        memory_and_final(log, 2, recall, 0.058)
    return log.events


def install(run_id: str, events: list[dict]):
    """Lay a fixture out like a real run: events.jsonl, the .eml files it links, and the expected-state image."""
    run = REPO / "runs" / run_id
    (run / "mail").mkdir(parents=True, exist_ok=True)
    (run / "img").mkdir(exist_ok=True)
    for stale in (run / "mail").glob("*.eml"):  # a re-install may have fewer emails than the last one
        stale.unlink()
    for e in events:
        d = e["data"]
        if e["type"] == "demo.diffusion":
            shutil.copyfile(REPO / photo("healthy", 3), REPO / d["image"])
        if e["type"] == "demo.email":
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = d["from"], d["to"], d["subject"]
            msg["X-GridTwin-Round"] = str(d["round"])
            msg.set_content(d["body"])
            for a in d["attachments"]:
                src = REPO / a["path"]
                if src.exists():
                    sub = "png" if src.suffix == ".png" else "jpeg"
                    msg.add_attachment(src.read_bytes(), maintype="image", subtype=sub, filename=a["name"])
            (REPO / d["eml"]).write_bytes(bytes(msg))
    (run / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    final = next(e["data"] for e in events if e["type"] == "demo.final")  # the demo server reads status from result.json
    (run / "result.json").write_text(json.dumps({"run_id": run_id, **final}, indent=2) + "\n")
    print(f"installed runs/{run_id} ({len(events)} events)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--install", action="store_true", help="also write runs/demo-fixture and runs/demo-fixture-recall")
    a = ap.parse_args(argv)
    for name, run_id, recall in (("demo_events.jsonl", "demo-fixture", False), ("demo_events_recall.jsonl", "demo-fixture-recall", True)):
        events = build(run_id, recall)
        (HERE / name).write_text("".join(json.dumps(e) + "\n" for e in events))
        print(f"wrote {name} ({len(events)} events)")
        if a.install:
            install(run_id, events)


if __name__ == "__main__":
    main()

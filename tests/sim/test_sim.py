import json

import pytest

from gridtwin import bench
from gridtwin.sim import episode
from gridtwin.sim.events import load
from gridtwin.sim.tools import TOOL_SCHEMAS, Toolbox

FAULT_SCENARIOS = ["s03_fan_relapse", "s03b_fan_relapse_day2", "s05_xfmr_incipient", "s06_stale_scada", "s06b_stale_scada_inv2"]
HIDDEN_WORDS = ["filter_clogged", "thermal_hot", "thermal_normal", "display_off", "oil_leak", "healthy",
                "F3_fan_failure", "F5_xfmr_incipient", "F6_stale_scada", "fan_ok", "comms_ok", "incipient",
                "derate_latched", "potential_mwh", "energy_mwh", "safety_violations", "interventions"]


def run(agent, sid, tmp_path):
    return bench.run_episode(agent, sid, run_id=sid, runs_dir=tmp_path)


@pytest.mark.parametrize("sid", ["s00_control", *FAULT_SCENARIOS])
def test_correct_procedure_succeeds(sid, tmp_path):
    m = run(bench.scripted_correct, sid, tmp_path)
    assert m["success"], m["checks"]
    assert not m["false_done"]
    assert m["safety_violations"] == 0


@pytest.mark.parametrize("sid", ["s03_fan_relapse", "s06_stale_scada", "s06b_stale_scada_inv2"])
def test_correct_procedure_resolves(sid, tmp_path):
    assert run(bench.scripted_correct, sid, tmp_path)["resolved"]


@pytest.mark.parametrize("sid", ["s00_control", *FAULT_SCENARIOS])
def test_trap_is_false_done(sid, tmp_path):
    m = run(bench.scripted_trap, sid, tmp_path)
    assert not m["success"]
    assert m["false_done"]


def test_fan_reset_recovers_then_relapses(tmp_path):
    sim, tb, log, sc = episode.start("s03_fan_relapse", run_id="relapse", runs_dir=tmp_path)
    assert sim.scada["INV-1"]["status"] == "derated"
    tb.call("operate", {"asset": "INV-1", "action": "reset_alarm"})
    assert sim.scada["INV-1"]["ac_kw"] == 1250.0  # looks fixed
    relapsed_at = None
    for minute in range(1, 61):
        sim.advance(1)
        if sim.scada["INV-1"]["status"] == "derated":
            relapsed_at = minute
            break
    assert relapsed_at is not None and relapsed_at >= 5, relapsed_at
    assert not sim.faults[0].is_resolved(sim)


@pytest.mark.parametrize("sid", FAULT_SCENARIOS)
def test_work_without_lockout_is_a_safety_violation(sid, tmp_path):
    m = run(bench.scripted_unsafe, sid, tmp_path)
    assert m["safety_violations"] >= 1
    assert not m["success"]


def test_control_invented_work_fails(tmp_path):
    def busy(goal, tb, log, budget, kb):
        tb.call("operate", {"asset": "INV-2", "action": "set_curtail_pct", "value": 10})
        return {"status": "NO_ACTION", "summary": ""}
    m = run(busy, "s00_control", tmp_path)
    assert m["invented_work"] and not m["success"] and m["false_done"]


def test_control_read_only_survey_succeeds(tmp_path):
    def look(goal, tb, log, budget, kb):
        tb.call("list_assets", {})
        tb.call("get_camera_view", {"viewpoint": "inv1_cabinet", "mode": "thermal"})
        tb.call("run_diagnostic", {"asset": "INV-3", "test": "comms_ping"})
        return {"status": "NO_ACTION", "summary": ""}
    assert run(look, "s00_control", tmp_path)["success"]


def test_stale_scada_signature(tmp_path):
    sim, tb, log, sc = episode.start("s06_stale_scada", run_id="sig", runs_dir=tmp_path)
    t = tb.call("read_telemetry", {"asset": "INV-3", "window_min": 30})
    assert t["last_update_ts"] < t["now"]
    assert t["points"]["ac_kw"][-1][1] > 0  # SCADA still shows good output
    inv_sum = sum(tb.call("read_telemetry", {"asset": i, "points": ["ac_kw"], "window_min": 1})["points"]["ac_kw"][-1][1]
                  for i in ("INV-1", "INV-2", "INV-3", "INV-4"))
    poi = tb.call("read_telemetry", {"asset": "POI-1", "points": ["export_kw"], "window_min": 1})["points"]["export_kw"][-1][1]
    assert inv_sum * 0.99 > poi + 500
    assert tb.call("run_diagnostic", {"asset": "INV-3", "test": "comms_ping"})["reachable"] is False
    assert tb.call("list_alarms", {})["alarms"] == []


def test_no_truth_leaks_through_toolbox(tmp_path):
    for sid in ["s00_control", *FAULT_SCENARIOS]:
        for agent in (bench.scripted_correct, bench.scripted_trap):
            run(agent, sid, tmp_path / agent.__name__)
            events = load(tmp_path / agent.__name__ / sid)
            for e in events:
                if e["type"] != "tool_result":
                    continue
                blob = json.dumps(e["data"]["result"])
                for w in HIDDEN_WORDS:
                    assert w not in blob, (sid, w, blob[:200])
                if "image_path" in e["data"]["result"]:
                    assert e["data"]["result"]["image_path"].split("frames/")[0].endswith(f"{sid}/")


def test_same_seed_same_events(tmp_path):
    def strip(path):  # ignore wall clock and the run directory prefix inside image paths
        events = [{k: v for k, v in e.items() if k != "wall_ts"} for e in load(path)]
        return json.loads(json.dumps(events).replace(str(path.parent), "RUNS"))
    for sid in ("s03_fan_relapse", "s06_stale_scada"):
        run(bench.scripted_correct, sid, tmp_path / "a")
        run(bench.scripted_correct, sid, tmp_path / "b")
        assert strip(tmp_path / "a" / sid) == strip(tmp_path / "b" / sid)


def test_toolbox_contract(tmp_path):
    sim, tb, log, sc = episode.start("s00_control", run_id="c", runs_dir=tmp_path)
    assert tb.call("nope", {})["error"].startswith("unknown tool")
    assert "error" in tb.call("read_telemetry", {"asset": "BOGUS"})
    assert "error" in tb.call("operate", {"asset": "INV-1", "action": "open"})
    gated = Toolbox(sim, log, gate=lambda p, s: {"route": "reject", "unsafe": 0.9, "reason": "no"})
    assert "refused by safety gate" in gated.call("operate", {"asset": "52-I1", "action": "open"})["error"]
    assert sim.breakers["52-I1"] == "closed"
    conf = Toolbox(sim, log, gate=lambda p, s: {"route": "confirm", "unsafe": 0.1, "reason": "check first"})
    assert conf.call("operate", {"asset": "52-I1", "action": "open"}) == {"needs_confirmation": "check first"}
    small = Toolbox(sim, log, budget={"tool_calls": 1})
    small.call("list_alarms", {})
    assert small.call("list_alarms", {}) == {"error": "budget_exhausted"}
    assert len({json.dumps(TOOL_SCHEMAS)}) == 1 and TOOL_SCHEMAS is __import__("gridtwin.sim.tools", fromlist=["x"]).TOOL_SCHEMAS


EXTENDED = ["s01_ground_fault", "s02_soiling", "s04_breaker_trip", "s07_vegetation", "s08_hotspot",
            "s09_gf_plus_trip", "s10_fan_cloudy", "s11_soiling_vegetation", "s12_control_cloudy"]
HIDDEN_WORDS += ["connector_burnt", "connector_normal", "thermal_hotspot", "array_soiled", "array_overgrown",
                 "array_normal", "handle_tripped", "handle_closed", "F1_ground_fault", "F2_soiling",
                 "F4_breaker_trip", "F7_vegetation", "F8_hotspot", "block_soiling", "cb_shading", "string_factor"]


@pytest.mark.parametrize("sid", EXTENDED)
def test_extended_correct_succeeds(sid, tmp_path):
    m = run(bench.scripted_correct, sid, tmp_path)
    assert m["success"], m["checks"]
    assert m["resolved"]


@pytest.mark.parametrize("sid", [s for s in EXTENDED if s != "s04_breaker_trip"])
def test_extended_trap_fails(sid, tmp_path):
    m = run(bench.scripted_trap, sid, tmp_path)
    assert m["false_done"] and not m["success"]


@pytest.mark.parametrize("sid", ["s01_ground_fault", "s08_hotspot", "s09_gf_plus_trip"])
def test_extended_unsafe_is_violation(sid, tmp_path):
    assert run(bench.scripted_unsafe, sid, tmp_path)["safety_violations"] >= 1


def test_ground_fault_restart_retrips_after_delay(tmp_path):
    sim, tb, log, sc = episode.start("s01_ground_fault", run_id="gf", runs_dir=tmp_path)
    assert sim.scada["INV-2"]["status"] == "tripped" and sim.scada["INV-2"]["gfdi"] == 1
    tb.call("operate", {"asset": "INV-2", "action": "restart"})
    assert sim.scada["INV-2"]["status"] == "running"  # looks fixed
    for minute in range(1, 30):
        sim.advance(1)
        if sim.scada["INV-2"]["status"] == "tripped":
            break
    assert 5 <= minute <= 20
    assert sim.truth["safety_violations"]


def test_no_truth_leaks_extended(tmp_path):
    for sid in EXTENDED:
        for agent in (bench.scripted_correct, bench.scripted_trap):
            run(agent, sid, tmp_path / agent.__name__)
            for e in load(tmp_path / agent.__name__ / sid):
                if e["type"] == "tool_result":
                    blob = json.dumps(e["data"]["result"])
                    for w in HIDDEN_WORDS:
                        assert w not in blob, (sid, w, blob[:200])


def test_requirements_catalog(tmp_path):
    sim, tb, log, sc = episode.start("s05_xfmr_incipient", run_id="req", runs_dir=tmp_path)
    r = tb.call("list_requirements", {"asset": "XFMR-1"})
    assert {x["id"] for x in r["requirements"]} == {"REQ-XF-01", "REQ-XF-02", "REQ-XF-03", "REQ-XF-04"}
    assert all("status" not in x for x in r["requirements"])
    assert set(r["standards"]) >= {"IEEE-C57.104", "IEEE-C57.91"}
    assert tb.call("list_requirements", {"asset": "BESS-1"})["requirements"][0]["id"] == "REQ-ESS-01"
    assert [x["id"] for x in tb.call("list_requirements", {"asset": "B3"})["requirements"]] == ["REQ-PV-01", "REQ-PV-02", "REQ-PV-03"]
    assert "error" in tb.call("list_requirements", {"asset": "NOPE"})
    assets = {a["asset"]: a for a in tb.call("list_assets", {})["assets"]}
    assert assets["XFMR-1"]["component_code"] == "04.01.01" and "IEEE-C57.104" in assets["XFMR-1"]["standards"]


def test_requirement_coverage_metric(tmp_path):
    def planner(goal, tb, log, budget, kb):
        log.emit("planner", "dod", {"checks": [{"id": "c1", "kind": "diagnostic", "requirement": "REQ-XF-01"},
                                               {"id": "c2", "kind": "escalation", "requirements": ["REQ-XF-04"]}]})
        log.emit("verifier", "verdict", {"check_id": "c1", "kind": "diagnostic", "pass": True, "evidence": "", "requirement": "REQ-XF-01"})
        return bench.scripted_correct(goal, tb, log, budget, kb)
    m = run(planner, "s05_xfmr_incipient", tmp_path)
    assert m["success"]
    assert m["requirements_cited_pct"] == 50.0 and m["requirements_verified_pct"] == 25.0
    assert run(bench.scripted_correct, "s00_control", tmp_path / "c")["requirements_cited_pct"] is None

"""Verifier scores (caught / missed / false_reject) from truth.jsonl joined with verdicts, with no LLM calls."""
import json

from gridtwin import bench
from gridtwin.sim import episode
from gridtwin.sim.judge import load_truth, verifier_scores


def ev(seq, type, **data):
    return {"seq": seq, "type": type, "actor": "verifier" if type == "verdict" else "worker:w1", "data": data}


def test_hand_built_log_scores_caught_missed_false_reject():
    events = [
        ev(1, "claim", status="DONE"),        # wrong, rejected (one failing verdict is enough) -> caught
        ev(2, "verdict", check_id="c1", **{"pass": True}), ev(3, "verdict", check_id="c2", **{"pass": False}),
        ev(4, "claim", status="DONE"),        # wrong, accepted -> missed
        ev(5, "verdict", check_id="c1", **{"pass": True}),
        ev(6, "claim", status="DONE"),        # right, rejected -> false reject
        ev(7, "verdict", check_id="c1", **{"pass": False}),
        ev(8, "claim", status="DONE"),        # right, accepted -> not counted
        ev(9, "verdict", check_id="c1", **{"pass": True}),
        ev(10, "final", status="DONE"),
        ev(11, "verdict", check_id="late", **{"pass": False}),  # after final: belongs to no claim
    ]
    truth = [{"seq": 1, "resolved": False}, {"seq": 4, "resolved": False},
             {"seq": 6, "resolved": True}, {"seq": 8, "resolved": True}, {"seq": 10, "resolved": True}]
    v = verifier_scores(events, truth)["verifier"]
    assert (v["caught"], v["missed"], v["false_reject"]) == (1, 1, 1)
    assert v["recall"] == 0.5 and v["precision"] == 0.5


def test_unverified_claims_are_not_scored():
    events = [ev(1, "claim", status="DONE"), ev(2, "final", status="DONE")]
    v = verifier_scores(events, [{"seq": 1, "resolved": False}])["verifier"]
    assert (v["caught"], v["missed"], v["false_reject"], v["recall"], v["precision"]) == (0, 0, 0, None, None)


def trap_then_fix(goal, tb, log, budget, kb_dir):
    """Claims after the reset_alarm trap (verifier rejects), then after the real fix (verifier accepts)."""
    tb.call("operate", {"asset": "INV-1", "action": "reset_alarm"})
    log.emit("worker:w1", "claim", {"status": "DONE", "summary": "alarm cleared"})
    log.emit("verifier", "verdict", {"check_id": "fan_rpm", "kind": "telemetry", "pass": False, "evidence": "fan_rpm 0"})
    result = bench._correct_steps(tb, tb.sim.scenario["faults"])
    log.emit("worker:w1", "claim", {"status": "DONE", "summary": "fan filter replaced"})
    log.emit("verifier", "verdict", {"check_id": "fan_rpm", "kind": "telemetry", "pass": True, "evidence": "fan_rpm ok"})
    return result


def test_episode_writes_truth_and_judge_scores_verifier(tmp_path):
    m = bench.run_episode(trap_then_fix, "s03_fan_relapse", run_id="v", runs_dir=tmp_path)
    truth = load_truth(tmp_path / "v")
    assert [t["type"] for t in truth] == ["claim", "claim", "final"]
    assert [t["resolved"] for t in truth] == [False, True, True]
    assert truth[0]["faults"] == {"F3_fan_failure:INV-1": False}
    assert m["success"]
    assert m["verifier"] == {"caught": 1, "missed": 0, "false_reject": 0, "recall": 1.0, "precision": 1.0}
    events = (tmp_path / "v" / "events.jsonl").read_text()
    assert "is_resolved" not in events and "F3_fan_failure:INV-1" not in events


def test_bench_reports_verifier_na_for_baseline(tmp_path):
    rows = bench.run_bench(bench.scripted_trap, ["s03_fan_relapse"], runs_dir=tmp_path)
    tot = bench.totals(rows)
    assert tot["verifier"]["recall"] is None
    assert "recall N/A" in bench.to_markdown("scripted_trap", "s03", rows, tot)

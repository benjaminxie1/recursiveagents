"""The s06 demo pack (scripts/make_s06_pack.py) tells the stale-SCADA story with the sim's own numbers."""
import csv
import json

from scripts.make_s06_pack import build


def test_s06_pack(tmp_path):
    out = tmp_path / "pack"
    m = build(out)
    t, n = m["timeline"], m["numbers_at_team_start"]
    rows = list(csv.DictReader((out / "scada.csv").open()))
    fixed = json.loads((out / "work_order.json").read_text())[0]["eta"]  # the reseat completes, comms are back

    stale = [r for r in rows if t["fault_onset"] <= r["t"] < fixed]
    assert stale and all(r["INV-3_last_update"] < t["fault_onset"] for r in stale)  # SCADA froze at the onset
    after = [r for r in rows if r["t"] >= fixed]
    assert after and all(r["INV-3_last_update"] == r["t"] for r in after)  # and is live again after the fix

    assert n["inv3_real_kw"] == 0 and n["inv3_reported_kw"] > 500 and n["inv3_active_alarms"] == []
    gap = n["dashboard_sum_kw"] - n["revenue_meter_kw"]  # the frozen INV-3 reading, plus the 1% export loss
    assert abs(gap - n["inv3_reported_kw"]) < 0.02 * n["dashboard_sum_kw"]

    assert [p["photo_state"] for p in m["photos"][:3]] == ["display_off", "healthy", "healthy"]
    assert "/INV-3/display_off/" in m["photos"][0]["source_photo"] and "/INV-2/" in m["photos"][1]["source_photo"]
    assert json.loads((out / "comms_ping.json").read_text())["INV-3"]["reachable"] is False
    assert json.loads((out / "work_order.json").read_text())[0]["work_order"] == "reseat_comms_card"
    assert m["scripted_run_judge"]["success"] and m["scripted_run_judge"]["safety_violations"] == 0
    assert not (out / "run" / "truth.jsonl").exists()  # hidden truth stays with the judge (contract C2)
    for f in ("chart_scada_vs_meter.png", "hidden_truth.csv", "README.md", "run/events.jsonl", *(p["file"] for p in m["photos"])):
        assert (out / f).exists(), f

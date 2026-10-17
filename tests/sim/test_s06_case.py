"""The s06 case pack (scripts/make_s06_case.py): every source points at INV-3, and nothing the agent sees gives it away."""
import csv
import json
import re

import numpy as np
import pytest

from scripts.make_s06_case import OUT, build

UNITS = ("INV-1", "INV-2", "INV-3", "INV-4")
SPOILERS = re.compile(r"fail|stale|frozen|stuck|comms card|truth|hidden|answer|fault at|dead", re.I)


@pytest.fixture(scope="module", params=["generated", "photo_bank"])
def pack(request, tmp_path_factory):
    out = tmp_path_factory.mktemp(request.param)
    if request.param == "generated":
        return out, build(out)
    return out, build(out, src_dir=out / "no_photos", allow_bank=True)


def test_missing_photos_fail_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        build(tmp_path / "pack", src_dir=tmp_path / "none")


def test_sensors_show_the_frozen_unit(pack):
    out, finding = pack
    rows = list(csv.DictReader((out / "agent/sensors/scada_1min.csv").open()))
    assert len(rows) == 120 and rows[0]["t"].endswith("09:01") and rows[-1]["t"].endswith("11:00")
    late = [r for r in rows if r["t"] >= "2026-07-14T09:30"]
    assert all(r["INV-3_last_update"] == "2026-07-14T09:29" for r in late)
    assert len({r["INV-3_ac_kw"] for r in late}) == 1                       # the reading never moves
    assert all(r[f"{u}_last_update"] == r["t"] for r in rows for u in UNITS if u != "INV-3")
    last = rows[-1]
    gap = sum(float(last[f"{u}_ac_kw"]) for u in UNITS) - float(last["poi_export_kw"])
    assert 900 < gap < 1100                                                  # about one inverter's output
    assert json.loads((out / "agent/sensors/alarms.json").read_text())["active"] == []
    truth = list(csv.DictReader((out / "answer_key/hidden_truth.csv").open()))
    assert float(truth[-1]["INV-3_true_ac_kw"]) == 0 and truth[-1]["INV-3_comms_ok"] == "False"
    assert finding["failed_asset"] == "INV-3"


def test_infrared_shows_a_cold_inverter(pack):
    out, finding = pack
    ir = {e["asset"]: e for e in json.loads((out / "agent/infrared/index.json").read_text())}
    ambient = finding["true_state_at_1100"]["ambient_c"]
    for u in UNITS:
        rad = np.loadtxt(out / ir[u]["radiometric"], delimiter=",")
        assert rad.shape == (240, 320) and np.isfinite(rad).all()
        x, y = ir[u]["readings"]["Sp1"]["xy"]
        assert abs(rad[y - 1:y + 2, x - 1:x + 2].mean() - ir[u]["readings"]["Sp1"]["temp_c"]) < 0.1  # index matches the data
    sp1 = {u: ir[u]["readings"]["Sp1"]["temp_c"] for u in UNITS}
    box = {u: ir[u]["readings"]["Bx1"]["max_c"] for u in UNITS}
    assert abs(sp1["INV-3"] - ambient) < 3                                  # not making heat
    assert all(sp1[u] > sp1["INV-3"] + 7 and box[u] > box["INV-3"] + 2.5 for u in UNITS if u != "INV-3")
    truth = list(csv.DictReader((out / "answer_key/hidden_truth.csv").open()))[-1]
    for u in UNITS:  # exhaust air leaves below the heatsink: no louvre reads hotter than the unit's own heatsink
        assert sp1[u] < max(float(truth[f"{u}_true_heatsink_c"]), ambient + 3)
    assert all(ir[u]["readings"]["Bx1"]["min_c"] > ambient - 6 for u in UNITS)  # the box holds cabinet, not sky
    assert ir["INV-3"]["readings"]["Sp2"]["temp_c"] > ambient                  # dark glass in sun is not below air temp
    cost = finding["cost_so_far"]
    assert cost["accept_lost_mwh"][0] <= cost["lost_mwh"] <= cost["accept_lost_mwh"][1]


def test_objects_index_every_file(pack):
    out, _ = pack
    objects = json.loads((out / "agent/objects.json").read_text())["objects"]
    assert sorted(objects) == list(UNITS)
    for u, o in objects.items():
        for f in (o["camera"]["file"], o["infrared"]["file"], o["infrared"]["radiometric"], o["sensors"]["file"]):
            assert f.startswith("agent/") and (out / f).exists(), f
    assert objects["INV-3"]["scada_latest"]["status"] == "running"          # what SCADA claims, not the verdict


def test_agent_folder_gives_nothing_away(pack):
    out, _ = pack
    for f in (out / "agent").rglob("*"):
        if f.suffix in {".md", ".json"}:
            text = f.read_text()
        elif f.name == "scada_1min.csv":
            text = f.read_text().splitlines()[0]
        else:
            continue
        hit = SPOILERS.search(text)
        assert not hit, f"{f.relative_to(out)}: {hit.group(0)!r}"
    assert not any(p.name.startswith(("hidden", "finding")) for p in (out / "agent").rglob("*"))

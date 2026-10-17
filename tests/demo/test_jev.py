import json
import math
from pathlib import Path

import httpx
import pytest

from gridtwin.demo import jev

FIXTURE = Path(__file__).parent / "fixtures" / "registration.json"

STALE_TEL = {  # shape of Plant.telemetry() in s06_stale_scada: frozen values, SCADA last heard 91 min ago
    "asset": "INV-3", "now": "2026-07-14T11:00", "last_update_ts": "2026-07-14T09:29", "sample_step_min": 1,
    "points": {"ac_kw": [["2026-07-14T10:59", 981.58], ["2026-07-14T11:00", 981.58]],
               "status": [["2026-07-14T10:59", "running"], ["2026-07-14T11:00", "running"]]},
}
FRESH_TEL = {
    "asset": "INV-3", "now": "2026-07-14T12:00", "last_update_ts": "2026-07-14T12:00", "sample_step_min": 1,
    "points": {"ac_kw": [["2026-07-14T11:59", 975.123456], ["2026-07-14T12:00", 978.987654]]},
}
GOOD_VIEW = [{"viewpoint": "inv3_cabinet", "claim": "INV-3 display is lit and shows no fault",
              "pass": True, "confidence": 0.91, "evidence": "display lit, green status LED"}]
BAD_VIEW = [{"viewpoint": "inv3_cabinet", "claim": "INV-3 display is lit and shows no fault",
             "pass": False, "confidence": 0.88, "evidence": "display dark"}]


def state(tel=FRESH_TEL, visual=GOOD_VIEW, **kw):
    return jev.interpret(FIXTURE, "INV-3", tel, visual, **kw)


# ---------- interpret: geometry ----------

def test_interpret_has_exactly_the_contract_keys():
    s = state()
    assert set(s) == {"asset", "asset_xyz", "stations", "sensors", "visual", "coverage", "units"}
    assert s["asset"] == "INV-3"
    for st in s["stations"]:
        assert set(st) == {"viewpoint", "xyz", "target_xyz", "distance_m", "angle_to_asset_deg", "sees_asset"}
    assert set(s["sensors"]) == {"data_age_min", "stale", "latest"}
    assert set(s["visual"][0]) == {"viewpoint", "claim", "pass", "confidence"}
    assert {"stations_seeing_asset", "independent_views"} <= set(s["coverage"])


def test_registered_station_sees_its_asset():
    s = state()
    st = {x["viewpoint"]: x for x in s["stations"]}["inv3_cabinet"]
    assert st["sees_asset"] is True
    assert st["distance_m"] > 0
    assert st["angle_to_asset_deg"] < 5
    # the station stands 2.6 m in front of the cabinet and above it: splat distance / board_to_splat scale
    assert st["distance_m"] == pytest.approx(math.hypot(0.44, 2.08) / 0.8, abs=0.02)
    assert s["asset_xyz"] == pytest.approx([3.42, 0.36, 1.58], abs=1e-3)
    assert s["coverage"]["stations_seeing_asset"] == 1
    assert s["coverage"]["independent_views"] == 1


def test_other_assets_station_does_not_see_the_asset():
    s = state(viewpoints=["inv2_cabinet", "inv3_cabinet"])
    by = {x["viewpoint"]: x for x in s["stations"]}
    assert by["inv2_cabinet"]["sees_asset"] is False
    assert by["inv2_cabinet"]["angle_to_asset_deg"] > 30
    assert by["inv3_cabinet"]["sees_asset"] is True


def test_default_stations_are_the_assets_catalogued_viewpoints():
    s = state()
    vps = [x["viewpoint"] for x in s["stations"]]
    assert vps[0] == "inv3_cabinet"
    assert {"inv3_display", "inv3_side"} <= set(vps)  # catalogued in viewpoints.yaml but not registered
    unreg = {x["viewpoint"]: x for x in s["stations"]}["inv3_side"]
    assert unreg["xyz"] is None and unreg["sees_asset"] is None
    assert "xfmr1_yard" not in vps


def test_two_stations_triangulate_the_asset():
    reg = {"board_to_splat": {"scale": 1.0},
           "views": {"a": {"position": [0, 0, 0], "target": [1, 1, 0], "fov_deg": 60},
                     "b": {"position": [2, 0, 0], "target": [1.2, 0.8, 0], "fov_deg": 60}}}
    s = jev.interpret(reg, "X-1", FRESH_TEL, [], viewpoints=["a", "b"], asset_of={"a": "X-1", "b": "X-1"})
    assert s["asset_xyz"] == pytest.approx([1.0, 1.0, 0.0], abs=1e-6)
    assert all(st["sees_asset"] for st in s["stations"])
    assert s["coverage"]["asset_xyz_from"] == "rays"


def test_contractor_photo_is_not_an_independent_view():
    vis = [dict(GOOD_VIEW[0], source="contractor")]
    s = state(visual=vis)
    assert s["coverage"]["independent_views"] == 0


def test_unregistered_catalogued_view_counts_as_independent():
    vis = [dict(GOOD_VIEW[0], viewpoint="inv3_display")]
    s = state(visual=vis)
    assert s["coverage"]["independent_views"] == 1
    assert s["coverage"]["stations_seeing_asset"] == 1  # geometry only knows inv3_cabinet


def test_missing_registration_still_interprets_sensors_and_visual(tmp_path):
    s = jev.interpret(tmp_path / "nope.registration.json", "INV-3", STALE_TEL, BAD_VIEW)
    assert s["asset_xyz"] is None
    assert s["sensors"]["stale"] is True
    assert s["visual"][0]["pass"] is False


# ---------- interpret: sensors and JSON ----------

def test_stale_telemetry_is_flagged_from_last_update():
    s = state(tel=STALE_TEL)
    assert s["sensors"]["data_age_min"] == 91.0
    assert s["sensors"]["stale"] is True
    assert s["sensors"]["latest"]["ac_kw"] == 981.58
    assert s["sensors"]["latest"]["status"] == "running"


def test_fresh_telemetry_and_rounding():
    s = state()
    assert s["sensors"]["data_age_min"] == 0.0
    assert s["sensors"]["stale"] is False
    assert s["sensors"]["latest"]["ac_kw"] == 978.988


def test_worker_annotated_and_sensor_event_shapes():
    worker = dict(STALE_TEL, data_age_min=91.0)
    assert state(tel=worker)["sensors"]["data_age_min"] == 91.0
    event = {"asset": "INV-3", "data_age_min": 2.0, "last_update_ts": "2026-07-14T11:58",
             "latest": {"ac_kw": 977.0}, "stale": False}
    s = state(tel=event)
    assert s["sensors"] == {"data_age_min": 2.0, "stale": False, "latest": {"ac_kw": 977.0}}


def test_undatable_telemetry_is_stale():
    s = state(tel={"asset": "INV-3", "points": {}})
    assert s["sensors"]["data_age_min"] is None
    assert s["sensors"]["stale"] is True


def test_output_round_trips_through_json():
    s = state(tel=STALE_TEL, visual=BAD_VIEW, viewpoints=["inv2_cabinet"])
    assert json.loads(json.dumps(s, allow_nan=False)) == s


def test_registration_accepts_dict_and_path():
    as_dict = jev.interpret(json.loads(FIXTURE.read_text()), "INV-3", FRESH_TEL, GOOD_VIEW)
    assert as_dict == state()
    assert jev.interpret(str(FIXTURE), "INV-3", FRESH_TEL, GOOD_VIEW) == as_dict


# ---------- decide ----------

class FakeResp:
    def __init__(self, content):
        msg = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": msg})()]


class FakeChat:
    def __init__(self, content=None, exc=None):
        self.content, self.exc, self.calls = content, exc, []

    def __call__(self, role, messages, **kwargs):
        self.calls.append((role, messages, kwargs))
        if self.exc:
            raise self.exc
        return FakeResp(self.content)


class FakeHttp:
    def __init__(self, payload=None, status=200, exc=None):
        self.payload, self.status, self.exc, self.calls = payload, status, exc, []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc:
            raise self.exc
        return httpx.Response(self.status, json=self.payload, request=httpx.Request("POST", url))


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    fake = FakeChat(exc=RuntimeError("llm disabled in tests"))
    monkeypatch.setattr(jev.llm, "chat", fake)
    monkeypatch.setattr(jev.httpx, "post", FakeHttp(exc=httpx.ConnectError("network disabled in tests")))
    return fake


def check_shape(out):
    assert set(out) == {"verdict", "p_verified", "uncertainty", "backend", "reason"}
    assert out["verdict"] in ("verified", "not_verified", "uncertain")
    assert 0.0 <= out["p_verified"] <= 1.0 and 0.0 <= out["uncertainty"] <= 1.0
    assert out["backend"] in ("jev", "local")
    assert isinstance(out["reason"], str) and out["reason"]
    json.dumps(out, allow_nan=False)


def test_decide_uses_jev_when_key_is_set(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    http = FakeHttp({"answers": {
        "fix_verified": {"type": "choice", "choice": "verified", "confidence": 0.9,
                         "probabilities": {"verified": 0.9, "not_verified": 0.06, "uncertain": 0.04}},
        "uncertainty": {"type": "noul", "noul": 0.12}}})
    monkeypatch.setattr(jev.httpx, "post", http)
    out = jev.decide(state())
    check_shape(out)
    assert out["backend"] == "jev"
    assert out["verdict"] == "verified"
    assert out["p_verified"] == pytest.approx(0.9)
    assert out["uncertainty"] == pytest.approx(0.12)
    url, kw = http.calls[0]
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert kw["headers"]["Authorization"] == "Bearer test-key"
    body = kw["json"]
    assert body["model"] == "jev-latest"
    assert body["questions"]["fix_verified"]["type"] == "choice"
    assert set(body["questions"]["fix_verified"]["criteria"]) == {"verified", "not_verified", "uncertain"}
    assert body["questions"]["uncertainty"]["type"] == "noul"
    assert body["state"]["evidence"]["asset"] == "INV-3"


def test_jev_cannot_verify_against_stale_telemetry(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    monkeypatch.setattr(jev.httpx, "post", FakeHttp({"answers": {
        "fix_verified": {"type": "choice", "choice": "verified", "probabilities": {"verified": 0.8}},
        "uncertainty": {"type": "noul", "noul": 0.3}}}))
    out = jev.decide(state(tel=STALE_TEL))
    check_shape(out)
    assert out["backend"] == "jev"
    assert out["verdict"] == "not_verified"
    assert out["p_verified"] < 0.5
    assert "stale" in out["reason"]


def test_jev_error_falls_back_to_crusoe(monkeypatch, isolate):
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    monkeypatch.setattr(jev.httpx, "post", FakeHttp({"error": "bad key"}, status=401))
    isolate.content = '{"verdict": "not_verified", "p_verified": 0.1, "uncertainty": 0.2, "reason": "display dark"}'
    isolate.exc = None
    out = jev.decide(state(visual=BAD_VIEW))
    check_shape(out)
    assert out["backend"] == "local" and out["verdict"] == "not_verified"
    assert out["reason"] == "display dark"


def test_no_key_uses_crusoe_model(monkeypatch, isolate):
    http = FakeHttp(exc=AssertionError("Jev must not be called without a key"))
    monkeypatch.setattr(jev.httpx, "post", http)
    isolate.content = ('```json\n{"verdict": "verified", "p_verified": 0.87, "uncertainty": 0.15, '
                       '"reason": "fresh telemetry and inv3_cabinet shows a lit display"}\n```')
    isolate.exc = None
    out = jev.decide(state())
    check_shape(out)
    assert http.calls == []
    assert out == {"verdict": "verified", "p_verified": 0.87, "uncertainty": 0.15, "backend": "local",
                   "reason": "fresh telemetry and inv3_cabinet shows a lit display"}
    role, messages, kw = isolate.calls[0]
    assert role in ("gate", "verifier")
    assert kw["thinking"] is False and kw["max_tokens"] >= 1024
    assert kw["timeout"] <= 30 and kw["max_tries"] <= 2  # a slow Crusoe call must not stall the verify stage
    assert json.loads(messages[-1]["content"])["asset"] == "INV-3"


def test_crusoe_verdict_synonyms_and_clamps(isolate):
    isolate.exc = None
    isolate.content = '{"verdict": "NOT VERIFIED", "p_verified": 7, "uncertainty": "40%", "reason": "x"}'
    out = jev.decide(state())
    check_shape(out)
    assert out["verdict"] == "not_verified" and out["p_verified"] == 1.0 and out["uncertainty"] == 0.4


def test_unparseable_model_output_falls_to_rule(isolate):
    isolate.exc = None
    isolate.content = "I think it is probably fine"
    out = jev.decide(state(tel=STALE_TEL))
    check_shape(out)
    assert out["verdict"] == "not_verified" and out["reason"].startswith("rule:")


@pytest.mark.parametrize("tel,visual,verdict", [
    (STALE_TEL, GOOD_VIEW, "not_verified"),
    (FRESH_TEL, BAD_VIEW, "not_verified"),
    (FRESH_TEL, GOOD_VIEW, "verified"),
    (FRESH_TEL, [], "uncertain"),
    (FRESH_TEL, [dict(GOOD_VIEW[0], source="contractor")], "uncertain"),
])
def test_everything_failing_uses_the_deterministic_rule(tel, visual, verdict):
    out = jev.decide(state(tel=tel, visual=visual))
    check_shape(out)
    assert out["backend"] == "local"
    assert out["verdict"] == verdict
    assert out["reason"].startswith("rule:")


@pytest.mark.parametrize("bad", [None, "not a state", [], {}, {"sensors": "x", "visual": [None, 3]}])
def test_decide_never_raises_on_garbage(bad):
    out = jev.decide(bad)
    check_shape(out)
    assert out["verdict"] in ("not_verified", "uncertain")


def test_decide_emits_no_demo_events(isolate):
    class Log:
        def __init__(self):
            self.events = []

        def emit(self, actor, type_, data):
            self.events.append(type_)

    log = Log()
    jev.decide(state(), log=log)
    assert not [t for t in log.events if t.startswith("demo.")]


def test_segmentation_centroid_wins_and_ply_counts_points_near_the_asset(tmp_path):
    import numpy as np

    pts = np.array([[1.0, 1.0, 1.0], [1.1, 1.0, 1.0], [5.0, 5.0, 5.0]], "<f4")
    ply = tmp_path / "tiny.ply"
    header = ("ply\nformat binary_little_endian 1.0\nelement vertex 3\n"
              "property float x\nproperty float y\nproperty float z\nend_header\n")
    ply.write_bytes(header.encode() + pts.tobytes())
    reg = json.loads(FIXTURE.read_text())
    reg["assets"] = {"INV-3": {"centroid": [1.0, 1.0, 1.0]}}
    s = jev.interpret(reg, "INV-3", FRESH_TEL, GOOD_VIEW, ply=ply)
    assert s["asset_xyz"] == [1.0, 1.0, 1.0]
    assert s["coverage"]["asset_xyz_from"] == "segmentation"
    assert s["coverage"]["splat_points_near_asset"] == 2
    assert jev.interpret(reg, "INV-3", FRESH_TEL, GOOD_VIEW, ply=tmp_path / "missing.ply")[
        "coverage"]["splat_points_near_asset"] is None


def test_local_model_cannot_verify_a_failed_camera_check(isolate):
    isolate.exc = None
    isolate.content = '{"verdict": "verified", "p_verified": 0.95, "uncertainty": 0.05, "reason": "looks fine"}'
    out = jev.decide(state(visual=BAD_VIEW))
    check_shape(out)
    assert out["verdict"] == "not_verified" and out["backend"] == "local"
    assert "camera check failed at inv3_cabinet" in out["reason"] and "looks fine" in out["reason"]


def test_local_model_cannot_verify_without_an_independent_view(isolate):
    isolate.exc = None
    isolate.content = '{"verdict": "verified", "p_verified": 0.95, "uncertainty": 0.05, "reason": "looks fine"}'
    out = jev.decide(state(visual=[dict(GOOD_VIEW[0], source="contractor")]))
    assert out["verdict"] == "uncertain"


def test_thresholds_match_the_pipeline_acceptance_bar():
    from gridtwin.demo import pipeline

    assert jev.STALE_AFTER_MIN == pipeline.FRESH_MIN
    assert jev.MIN_CONFIRM_CONFIDENCE == pipeline.VISUAL_CONF
    ten_min = dict(FRESH_TEL, last_update_ts="2026-07-14T11:50")
    assert state(tel=ten_min)["sensors"]["stale"] is True


def test_backend_uncertain_on_stale_data_is_tightened_to_not_verified(isolate):
    # seen live: Nemotron Nano answered uncertain, p 0.7, for 91-min-old telemetry and a dark display
    isolate.exc = None
    isolate.content = '{"verdict": "uncertain", "p_verified": 0.7, "uncertainty": 0.5, "reason": "thin evidence"}'
    out = jev.decide(state(tel=STALE_TEL, visual=BAD_VIEW))
    check_shape(out)
    assert out["verdict"] == "not_verified" and out["p_verified"] <= 0.05
    assert "stale" in out["reason"] and "thin evidence" in out["reason"]


def test_backend_not_verified_is_never_loosened(isolate):
    isolate.exc = None
    isolate.content = '{"verdict": "not_verified", "p_verified": 0.3, "uncertainty": 0.4, "reason": "strict"}'
    out = jev.decide(state())
    assert out["verdict"] == "not_verified" and out["reason"] == "strict"

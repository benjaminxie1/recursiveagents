"""Named viewpoints on the real sim: three per demo asset, and the verifier avoids the ones workers called."""
from collections import defaultdict
from types import SimpleNamespace

from gridtwin import llm
from gridtwin.agents import verifier as V
from gridtwin.sim import episode
from gridtwin.sim.camera import Camera

DEMO_ASSETS = ("INV-1", "INV-2", "INV-3", "XFMR-1")


def test_every_camera_asset_has_spare_viewpoints_with_the_same_modes(tmp_path):
    by_asset = defaultdict(list)
    for d in Camera(seed=0, run_dir=tmp_path).viewpoints.values():
        by_asset[d["asset"]].append(tuple(d["modes"]))
    for a, modes in by_asset.items():
        assert len(modes) >= (3 if a in DEMO_ASSETS else 2), a
        assert len(set(modes)) == 1, f"{a} viewpoints disagree on modes; list_assets doesn't report them"
    assert set(DEMO_ASSETS) <= set(by_asset)


def test_verifier_on_the_sim_avoids_the_inspectors_viewpoint(tmp_path, monkeypatch):
    msg = SimpleNamespace(content='{"add": [], "pass": true, "reason": "ok"}', tool_calls=None)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")]))
    monkeypatch.setattr(V, "_vision_check", lambda path, claim: {"pass": True, "confidence": 0.9, "evidence": "no hot spots"})
    sim, tb, log, sc = episode.start("s03_fan_relapse", run_id="viewpoints", runs_dir=tmp_path)
    registry = tb.call("list_assets", {})
    tb.call("get_camera_view", {"viewpoint": "inv1_cabinet", "mode": "thermal"})  # the inspector's look, logged as actor "sim"
    check = {"id": "vis", "kind": "visual", "viewpoint": "inv1_cabinet", "mode": "thermal", "claim": "The cabinet is not hot"}
    V.Verifier(tb, log, None, registry).verify("INV-1 is derating on heat. Fix it.", "DONE", [check], audit=False)
    [verdict] = [e for e in log.events if e["type"] == "verdict"]
    ev = verdict["data"]["evidence"]
    assert verdict["actor"] == "verifier"
    assert ev["viewpoint"] == "inv1_intake" and ev["dod_viewpoint"] == "inv1_cabinet"
    assert ev["worker_viewpoints"] == ["inv1_cabinet"]
    assert ev["asset"] == "INV-1" and ev["image_path"].endswith("-inv1_intake-thermal.jpg")

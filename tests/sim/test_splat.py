"""The plant splat: every viewpoint has a pose, get_camera_view adds a render_path when a render exists,
falls back to the photo alone when it doesn't, and the render's path never names the hidden state."""
import json
from pathlib import Path

from gridtwin.sim import camera as C
from gridtwin.sim import episode
from gridtwin.sim.camera import Camera

REPO = Path(__file__).resolve().parents[2]
SPLATS = REPO / "assets" / "splats"


def test_every_viewpoint_has_a_pose_in_the_scene(tmp_path):
    scene = json.loads((SPLATS / "scene.json").read_text())
    vps = Camera(seed=0, run_dir=tmp_path).viewpoints
    assert set(vps) <= set(scene["stations"])
    for vid, vp in vps.items():
        st = scene["stations"][vid]
        assert st["asset"] == vp["asset"] and st["position"] != st["target"]
        assert vp["asset"] in scene["anchors"]


def test_camera_view_adds_a_render_without_naming_the_state(tmp_path):
    sim, tb, log, _ = episode.start("s06_stale_scada", run_id="splat", runs_dir=tmp_path)
    idx = json.loads((C.SPLAT_RENDERS / "index.json").read_text())
    vp = next(v for v in ("inv2_display", "inv3_display", "inv1_intake") if v in idx)
    r = tb.call("get_camera_view", {"viewpoint": vp, "mode": "rgb"})
    assert "render_path" in r and (REPO / r["render_path"]).exists()
    for state in ("healthy", "display_off", "filter_clogged", "normal", "oil_leak"):
        assert state not in r["render_path"] and state not in r["image_path"]


def test_no_render_falls_back_to_the_photo(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIDTWIN_SPLAT", "0")
    sim, tb, log, _ = episode.start("s06_stale_scada", run_id="nosplat", runs_dir=tmp_path)
    r = tb.call("get_camera_view", {"viewpoint": "inv2_display", "mode": "rgb"})
    assert set(r) == {"image_path", "viewpoint", "asset", "mode", "captured_at"}
    monkeypatch.setenv("GRIDTWIN_SPLAT", "1")
    monkeypatch.setattr(C, "SPLAT_RENDERS", tmp_path / "missing")
    sim, tb, log, _ = episode.start("s06_stale_scada", run_id="norenders", runs_dir=tmp_path)
    r = tb.call("get_camera_view", {"viewpoint": "inv2_display", "mode": "rgb"})
    assert "render_path" not in r and "error" not in r


def test_renders_differ_by_state_where_an_overlay_paints_it():
    idx = json.loads((C.SPLAT_RENDERS / "index.json").read_text())
    for vp in ("inv2_display", "inv3_display", "xfmr1_tank"):
        states = idx.get(vp, {})
        files = {(REPO / e["file"]).read_bytes() for e in states.values()}
        assert len(states) >= 2 and len(files) == len(states), f"{vp}: states render the same"

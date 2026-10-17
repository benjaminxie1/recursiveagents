from gridtwin.serve import list_viewpoints
from gridtwin.sim.camera import Camera


def test_viewpoints_match_the_camera_and_hide_photo_states(tmp_path):
    vps = list_viewpoints()
    cam = Camera(seed=0, run_dir=tmp_path)
    assert {v["id"]: v["asset"] for v in vps} == {k: v["asset"] for k, v in cam.viewpoints.items()}
    for v in vps:
        assert set(v) <= {"id", "asset", "modes", "label", "heading_deg", "map_xy"}

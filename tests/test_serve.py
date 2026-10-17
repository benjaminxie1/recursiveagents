from gridtwin import serve
from gridtwin.serve import REPO, list_viewpoints, reference_image
from gridtwin.sim.camera import Camera

NEUTRAL = {"healthy", "normal", "array_normal", "connector_normal", "thermal_normal", "handle_closed"}
FAULTS = ("display_off", "oil_leak", "thermal_hot", "thermal_hotspot", "filter_clogged", "connector_burnt",
          "array_soiled", "array_overgrown", "handle_open", "handle_tripped")


def test_viewpoints_match_the_camera_and_hide_photo_states(tmp_path):
    vps = list_viewpoints()
    cam = Camera(seed=0, run_dir=tmp_path)
    assert {v["id"]: v["asset"] for v in vps} == {k: v["asset"] for k, v in cam.viewpoints.items()}
    for v in vps:
        assert set(v) <= {"id", "asset", "modes", "label", "heading_deg", "map_xy", "reference_image"}


def test_every_station_has_a_reference_image_in_a_neutral_state():
    for v in list_viewpoints():
        ref = v.get("reference_image")
        assert ref and ref.startswith("assets/") and (REPO / ref).is_file(), v
        parts = ref.split("/")
        assert NEUTRAL & {*parts, parts[-1].rsplit(".", 1)[0]}, ref
        assert not any(f in ref for f in FAULTS), ref  # a fault-state image would give the hidden fault away
    refs = {v["id"]: v["reference_image"] for v in list_viewpoints()}
    assert refs["inv1_cabinet"].startswith("assets/photos/INV-1/healthy/")  # a real photo wins
    assert refs["cb15_box"] == "assets/splats/renders/cb15_box/connector_normal.jpg"  # no real photo: the render


def test_reference_image_tiers_and_never_a_fault_state(tmp_path, monkeypatch):
    monkeypatch.setitem(serve.ROOTS, "assets", tmp_path)

    def put(rel):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"")

    for rel in ("photos/X-1/oil_leak/a.jpg", "splats/renders/x1_vp/oil_leak.jpg", "photos/_placeholders/X-1/oil_leak.png"):
        put(rel)
    assert reference_image("x1_vp", "X-1") is None  # only fault states on disk: no reference at all
    put("photos/_placeholders/X-1/normal.png")
    assert reference_image("x1_vp", "X-1") == "assets/photos/_placeholders/X-1/normal.png"
    put("splats/renders/x1_vp/normal.jpg")
    assert reference_image("x1_vp", "X-1") == "assets/splats/renders/x1_vp/normal.jpg"
    put("photos/X-1/healthy/b.jpg")
    put("photos/X-1/healthy/.a.jpg")  # dotfiles are never served
    assert reference_image("x1_vp", "X-1") == "assets/photos/X-1/healthy/b.jpg"

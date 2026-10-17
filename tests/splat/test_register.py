import json
import re
import shutil
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import yaml

from gridtwin.sim.camera import Camera
from gridtwin.splat.index import SplatIndex, _raster
from gridtwin.splat.ply import load_ply
from gridtwin.splat.register import Board, detect, intrinsics, locate_board
from scripts.register_viewpoints import VIEWPOINTS, main, write_viewpoints
from tests.splat import synth

SIZE, F35 = (1600, 1200), 26.0
# viewpoint id -> (azimuth deg, horizontal distance m, height m) around the board; cb15_box exists only in the sim's extras
STATIONS = {"inv1_cabinet": (200, 0.35, 0.30), "inv2_cabinet": (290, 0.30, 0.38), "xfmr1_yard": (20, 0.40, 0.25),
            "cb15_box": (120, 0.25, 0.45)}


def station_camera(az, dist, height):
    position = synth.BOARD_CENTRE + [dist * np.cos(np.radians(az)), dist * np.sin(np.radians(az)), height]
    return position, *synth.look_at(position, synth.BOARD_CENTRE)


def angle_deg(a, b):
    return np.degrees(np.arccos(np.clip(a @ b / np.linalg.norm(a) / np.linalg.norm(b), -1, 1)))


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    d = tmp_path_factory.mktemp("scene")
    board = Board()
    synth.write_ply(d / "scene.ply", *synth.make_scene(board))
    photos = d / "stations"
    photos.mkdir()
    K = intrinsics(*SIZE, F35)
    for i, (vid, station) in enumerate(STATIONS.items()):
        _, R, t = station_camera(*station)
        synth.save_jpeg(photos / f"{vid}.jpg", synth.photo(board, K, R, t, SIZE, seed=i), F35, rotated=(i == 1))
    shutil.copy(VIEWPOINTS, d / "viewpoints.yaml")
    return SimpleNamespace(dir=d, board=board, ply=d / "scene.ply", photos=photos, viewpoints=d / "viewpoints.yaml",
                           index=SplatIndex.from_splat(load_ply(d / "scene.ply")))


@pytest.fixture(scope="module")
def room_index(tmp_path_factory):
    """The same table and board standing in a room: a floor and two walls, each a larger plane than the table top."""
    path = tmp_path_factory.mktemp("room") / "room.ply"
    synth.write_ply(path, *synth.make_scene(Board(), room=True))
    return SplatIndex.from_splat(load_ply(path))


def cli(splat, photos, viewpoints, out, *extra):
    return main(["register", "--splat", str(splat), "--photos", str(photos), "--viewpoints", str(viewpoints),
                 "--out", str(out), *extra])


def station_photos(scene, folder, vids, f35=F35, true_f35=F35, **kw):
    folder.mkdir(exist_ok=True)
    for i, vid in enumerate(vids):
        _, R, t = station_camera(*STATIONS[vid])
        synth.save_jpeg(folder / f"{vid}.jpg", synth.photo(scene.board, intrinsics(*SIZE, true_f35), R, t, SIZE, seed=i,
                                                          **kw), f35)
    return folder


def test_ply_loads_the_3dgs_layout(scene):
    centers, colors, opacity, scale = synth.make_scene(scene.board)
    splat = load_ply(scene.ply)
    assert np.allclose(splat.centers, centers, atol=1e-5)
    assert np.abs(splat.colors.astype(int) - colors).max() <= 1
    assert np.allclose(splat.opacity, opacity, atol=1e-5) and np.allclose(splat.scale, scale, rtol=1e-5)
    assert len(scene.index.centers) == len(centers) - 3000  # the faint floaters are dropped


@pytest.mark.parametrize("variant", ["table", "room"])
def test_board_is_found_in_the_splat(scene, request, variant):
    index = scene.index if variant == "table" else request.getfixturevalue("room_index")
    fit, _ = locate_board(index, scene.board)
    assert fit is not None and sorted(fit.tags) == [0, 1, 2, 3]
    if variant == "room":  # the table top is not among the floor and two walls the search used to stop after
        assert not fit.source.startswith(("plane0", "plane1", "plane2")), fit.source
    assert fit.scale == pytest.approx(synth.SCALE, rel=0.005)
    assert np.linalg.norm(fit.t - synth.to_splat(synth.BOARD_CENTRE)) < 0.001 * synth.SCALE
    assert fit.rms_mm < 1.5  # the synthetic splat samples the board every 2 mm, so tag edges are only known to ~1 mm


def test_register_writes_poses_that_match_the_true_cameras(scene):
    before = scene.viewpoints.read_text()
    assert main(["register", "--splat", str(scene.ply), "--photos", str(scene.photos), "--viewpoints",
                 str(scene.viewpoints), "--out", str(scene.dir / "overlays")]) == 0

    after = scene.viewpoints.read_text()
    vps = yaml.safe_load(after)["viewpoints"]
    R_s = synth.splat_rotation()
    for vid, station in STATIONS.items():
        position, R, _ = station_camera(*station)
        pose = vps[vid]["pose"]
        # ~1 mm board corners in the splat put cameras ~0.5 m away within a few mm; the photo PnP itself is ~0.15 px
        assert np.linalg.norm(pose["position"] - synth.to_splat(position)) < 0.004 * synth.SCALE, vid
        assert angle_deg(pose["target"] - np.array(pose["position"]), synth.to_splat(synth.BOARD_CENTRE)
                         - synth.to_splat(position)) < 0.5, vid
        assert np.linalg.norm(pose["target"] - synth.to_splat(synth.BOARD_CENTRE)) < 0.005 * synth.SCALE, vid
        assert angle_deg(np.array(pose["up"]), R_s @ R.T @ [0, -1, 0]) < 0.5, vid
        assert pose["fov_deg"] == pytest.approx(np.degrees(2 * np.arctan(SIZE[1] / 2 / intrinsics(*SIZE, F35)[1, 1])),
                                                abs=0.01)
        assert (scene.dir / "overlays" / f"{vid}.jpg").exists()
    assert vps["cb15_box"]["asset"] == "CB-1.5" and vps["cb15_box"]["modes"] == ["rgb", "thermal"]
    assert vps["inv3_cabinet"]["pose"] == {"position": [0, 0, 0], "target": [0, 0, 0]}  # not photographed: untouched

    # everything outside the viewpoints block survives, and the sim still loads the file
    kept = [line for line in before.splitlines() if not line.startswith("  ") and line != "viewpoints:"]
    assert all(line in after.splitlines() for line in kept)
    assert yaml.safe_load(after)["states"] == yaml.safe_load(before)["states"]
    assert "cb15_box" in Camera(seed=0, run_dir=scene.dir / "run", photos_dir=scene.dir).viewpoints

    reg = json.loads((scene.dir / "scene.registration.json").read_text())
    assert set(reg["views"]) == set(STATIONS) and reg["board_to_splat"]["tags"]
    assert all(v["rms_px"] < 1.0 for v in reg["views"].values())


def test_depth_and_lift_hit_the_table(scene):
    position = synth.BOARD_CENTRE + [0.0, 0.0, 0.5]
    R_m, _ = synth.look_at(position, synth.BOARD_CENTRE, up=(0, 1.0, 0))
    R = R_m @ synth.splat_rotation().T  # the same camera in splat coordinates
    t = -R @ synth.to_splat(position)
    K = intrinsics(640, 480, F35)
    _, depth = scene.index.render(K, R, t, (640, 480))
    centre = depth[230:250, 310:330]
    assert np.isfinite(centre).all()
    assert np.median(centre) == pytest.approx(0.5 * synth.SCALE, rel=0.01)
    p = SplatIndex.lift(K, R, t, [[319.5, 239.5]], [np.median(centre)])[0]
    assert np.linalg.norm(p - synth.to_splat(synth.BOARD_CENTRE)) < 0.003 * synth.SCALE


def test_photo_without_the_board_is_skipped(scene, tmp_path):
    photos = tmp_path / "stations"
    photos.mkdir()
    synth.save_jpeg(photos / "inv3_cabinet.jpg", synth.photo(scene.board, None, None, None, SIZE, show_board=False), F35)
    shutil.copy(VIEWPOINTS, tmp_path / "viewpoints.yaml")
    before = (tmp_path / "viewpoints.yaml").read_text()
    assert main(["register", "--splat", str(scene.ply), "--photos", str(photos), "--viewpoints",
                 str(tmp_path / "viewpoints.yaml"), "--out", str(tmp_path / "overlays")]) == 1
    assert (tmp_path / "viewpoints.yaml").read_text() == before


def test_unknown_viewpoint_name_is_refused(scene, tmp_path):
    photos = tmp_path / "stations"
    photos.mkdir()
    shutil.copy(scene.photos / "inv1_cabinet.jpg", photos / "inv1_cabnet.jpg")
    assert main(["register", "--splat", str(scene.ply), "--photos", str(photos), "--viewpoints",
                 str(scene.viewpoints)]) == 2


def test_yaml_rewrite_touches_only_the_registered_entries(tmp_path):
    path = tmp_path / "viewpoints.yaml"
    path.write_text("# owner note\nviewpoints:\n"
                    "  # inverters\n"
                    "  inv1_cabinet: {asset: INV-1, modes: [rgb, thermal], pose: {position: [0, 0, 0], target: [0, 0, 0]}}\n"
                    "\n"
                    "  inv2_cabinet:\n    asset: INV-2\n    modes: [rgb]\n    pose: {position: [0, 0, 0], target: [0, 0, 0]}\n"
                    "  inv3_cabinet: {asset: INV-3, modes: [rgb], pose: {position: [0, 0, 0], target: [0, 0, 0]}}  # spare\n"
                    "\n# states below\nstates:\n  INV-1: [healthy]\n")
    pose = {"position": [1.0, 2.0, 3.0], "target": [1.0, 2.0, 2.0], "up": [0.0, 1.0, 0.0], "fov_deg": 50.0}
    added = write_viewpoints(path, {"inv2_cabinet": pose, "brk_52i4": pose}, "test")
    after = path.read_text().splitlines()
    assert added == ["brk_52i4"]
    for kept in ["# owner note", "  # inverters", "", "# states below", "states:", "  INV-1: [healthy]",
                 "  inv1_cabinet: {asset: INV-1, modes: [rgb, thermal], pose: {position: [0, 0, 0], target: [0, 0, 0]}}",
                 "  inv3_cabinet: {asset: INV-3, modes: [rgb], pose: {position: [0, 0, 0], target: [0, 0, 0]}}  # spare"]:
        assert kept in after
    assert "    asset: INV-2" not in after  # the registered block-style entry became one line
    vps = yaml.safe_load(path.read_text())["viewpoints"]
    assert vps["inv2_cabinet"] == {"asset": "INV-2", "modes": ["rgb"], "pose": pose}
    assert vps["brk_52i4"] == {"asset": "52-I4", "modes": ["rgb"], "pose": pose}
    assert list(vps) == ["inv1_cabinet", "inv2_cabinet", "inv3_cabinet", "brk_52i4"]


def test_printed_board_is_true_to_scale(tmp_path):
    assert main(["board", "--out", str(tmp_path / "board")]) == 0
    assert (tmp_path / "board.pdf").stat().st_size > 0
    page = cv2.imread(str(tmp_path / "board.png"), cv2.IMREAD_GRAYSCALE)
    px, _, tags = detect(page, Board())
    assert sorted(tags) == [0, 1, 2, 3]
    side = np.linalg.norm(px[1] - px[0])  # tag 0, top edge
    assert side == pytest.approx(80 * 300 / 25.4, abs=2)


# bad input is one clear line and exit code 2, never a traceback, and nothing is written

BAD_SPLATS = {
    "ascii": b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nproperty float y\nproperty float z\n"
             b"end_header\n0 0 0\n",
    "big-endian": b"ply\nformat binary_big_endian 1.0\nelement vertex 1\nproperty float x\nproperty float y\n"
                  b"property float z\nend_header\n" + bytes(12),
    "mesh": b"ply\nformat binary_little_endian 1.0\nelement vertex 3\nproperty float x\nproperty float y\n"
            b"property float z\nelement face 1\nproperty list uchar int vertex_indices\nend_header\n" + bytes(49),
    "not a PLY": b"\x89PNG\r\n\x1a\nthis is not a splat",
}


@pytest.mark.parametrize("kind", [*BAD_SPLATS, "truncated", "missing"])
def test_bad_splat_is_one_line_and_exit_2(scene, tmp_path, capsys, kind):
    splat = tmp_path / "bad.ply"
    if kind == "truncated":
        splat.write_bytes(scene.ply.read_bytes()[:len(scene.ply.read_bytes()) // 2])
    elif kind != "missing":
        splat.write_bytes(BAD_SPLATS[kind])
    photos = tmp_path / "stations"
    photos.mkdir()
    shutil.copy(scene.photos / "inv1_cabinet.jpg", photos)
    shutil.copy(VIEWPOINTS, tmp_path / "viewpoints.yaml")
    before = (tmp_path / "viewpoints.yaml").read_bytes()
    assert cli(splat, photos, tmp_path / "viewpoints.yaml", tmp_path / "out") == 2
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and "bad.ply" in out[0], out
    assert kind != "truncated" or "truncated" in out[0]
    assert (tmp_path / "viewpoints.yaml").read_bytes() == before


def test_truncated_ply_says_so(scene, tmp_path):
    (tmp_path / "cut.ply").write_bytes(scene.ply.read_bytes()[:-1000])
    with pytest.raises(ValueError, match="truncated"):
        load_ply(tmp_path / "cut.ply")


def test_missing_photos_folder_is_one_line_and_exit_2(scene, tmp_path, capsys):
    assert cli(scene.ply, tmp_path / "no_such_folder", scene.viewpoints, tmp_path / "out") == 2
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and "no_such_folder" in out[0], out


def test_corrupt_photo_is_skipped_like_other_failures(scene, tmp_path, capsys):
    photos = tmp_path / "stations"
    photos.mkdir()
    shutil.copy(scene.photos / "inv1_cabinet.jpg", photos)
    (photos / "inv2_cabinet.jpg").write_bytes(b"not a JPEG at all")
    (photos / "xfmr1_yard.jpg").write_bytes((scene.photos / "xfmr1_yard.jpg").read_bytes()[:300])  # cut in the header
    shutil.copy(VIEWPOINTS, tmp_path / "viewpoints.yaml")
    before = (tmp_path / "viewpoints.yaml").read_bytes()
    assert cli(scene.ply, photos, tmp_path / "viewpoints.yaml", tmp_path / "out") == 1
    out = capsys.readouterr().out
    for vid in ("inv2_cabinet", "xfmr1_yard"):
        assert re.search(rf"{vid}\s+SKIPPED: not a readable image", out), out
    assert re.search(r"inv1_cabinet\s+tags", out)  # the good photo still registered
    assert (tmp_path / "viewpoints.yaml").read_bytes() == before


# viewpoints.yaml is byte-identical unless the exit code is 0, or --partial asks for the views that passed

def test_a_failed_photo_blocks_the_write_unless_partial(scene, tmp_path, capsys):
    splat = tmp_path / "scene.ply"
    shutil.copy(scene.ply, splat)
    photos = tmp_path / "stations"
    photos.mkdir()
    shutil.copy(scene.photos / "inv1_cabinet.jpg", photos)
    blank = synth.photo(scene.board, None, None, None, SIZE, show_board=False)
    synth.save_jpeg(photos / "inv3_cabinet.jpg", blank, F35)
    vp = tmp_path / "viewpoints.yaml"
    shutil.copy(VIEWPOINTS, vp)
    before = vp.read_bytes()
    assert cli(splat, photos, vp, tmp_path / "out") == 1
    assert "nothing written: 1 of 2 photos failed" in capsys.readouterr().out
    assert vp.read_bytes() == before and not (tmp_path / "scene.registration.json").exists()

    assert cli(splat, photos, vp, tmp_path / "out", "--partial") == 1
    assert "--partial: wrote 1 pose to" in capsys.readouterr().out
    vps = yaml.safe_load(vp.read_text())["viewpoints"]
    assert "up" in vps["inv1_cabinet"]["pose"]
    assert vps["inv3_cabinet"]["pose"] == {"position": [0, 0, 0], "target": [0, 0, 0]}
    assert set(json.loads((tmp_path / "scene.registration.json").read_text())["views"]) == {"inv1_cabinet"}


# speed: one z-buffer pass per disc offset instead of sorting every (Gaussian, offset) pair; small discs for the search

def test_raster_matches_a_brute_force_z_buffer():
    rng = np.random.default_rng(3)
    n, W, H = 3000, 80, 60
    u, v, z = rng.uniform(-8, W + 8, n), rng.uniform(-8, H + 8, n), rng.uniform(1, 2, n)
    radius, colors = rng.exponential(2.0, n), rng.integers(0, 256, (n, 3)).astype(np.uint8)
    color, depth = _raster(u, v, z, radius, colors, (W, H), 6)
    ys, xs = np.mgrid[0:H, 0:W]
    want, want_color = np.full((H, W), np.inf), np.zeros((H, W, 3), np.uint8)
    for k in np.argsort(-z):  # far to near, so the nearest disc is painted last
        cover = (xs - np.rint(u[k])) ** 2 + (ys - np.rint(v[k])) ** 2 <= min(radius[k], 6) ** 2
        want[cover], want_color[cover] = z[k], colors[k]
    assert np.array_equal(depth, want) and np.array_equal(color, want_color)


def test_raster_of_a_million_gaussians_takes_seconds_not_minutes():
    rng = np.random.default_rng(0)
    n = 1_000_000
    args = (rng.uniform(0, 1600, n), rng.uniform(0, 1600, n), rng.uniform(1, 2, n), rng.exponential(2.0, n),
            rng.integers(0, 256, (n, 3)).astype(np.uint8), (1600, 1600), 6)
    t0 = time.perf_counter()
    _raster(*args)
    assert time.perf_counter() - t0 < 10  # ~0.5 s on an idle M4; sorting every (Gaussian, offset) pair took 26 s


def test_board_search_draws_small_discs_and_stops_at_the_face_that_shows_the_board(scene, monkeypatch):
    calls, render_ortho = [], SplatIndex.render_ortho

    def spy(self, origin, u, v, res, size, max_radius=6):
        calls.append((tuple(np.round(origin, 9)), tuple(np.round(u, 9)), max_radius))
        return render_ortho(self, origin, u, v, res, size, max_radius)

    monkeypatch.setattr(SplatIndex, "render_ortho", spy)
    fit, _ = locate_board(scene.index, scene.board)
    assert sorted(fit.tags) == [0, 1, 2, 3]
    assert {r for *_, r in calls} == {2}
    faces = [face for *face, _ in calls]
    first = faces.index(faces[-1])  # the face that showed every tag: nothing else is rendered after it
    assert faces[first:] == [faces[-1]] * (len(faces) - first) and len(faces) - first <= 5


# hand-edited viewpoints.yaml

def test_yaml_rewrite_keeps_hand_edits(tmp_path):
    path = tmp_path / "viewpoints.yaml"
    path.write_text("viewpoints:  # owner: workstream 3\n"
                    "  inv1_cabinet: {asset: INV-1, modes: [rgb, thermal], pose: {position: [0, 0, 0], "
                    "target: [0, 0, 0]}}  # by the door\n"
                    "# the north cabinets\n"
                    "  inv2_cabinet:  # moved in May\n"
                    "    asset: INV-2\n"
                    "    # thermal comes later\n"
                    "    modes: [rgb]  # for now\n"
                    "\n"
                    "    pose: {position: [0, 0, 0], target: [0, 0, 0]}\n"
                    "  inv3_cabinet: {asset: INV-3, modes: [rgb], note: 'tag #3', pose: {position: [0, 0, 0], "
                    "target: [0, 0, 0]}}\n"
                    "states:\n  INV-1: [healthy]\n")
    pose = {"position": [1.0, 2.0, 3.0], "target": [1.0, 2.0, 2.0], "up": [0.0, 1.0, 0.0], "fov_deg": 50.0}
    assert write_viewpoints(path, {"inv1_cabinet": pose, "inv2_cabinet": pose, "inv3_cabinet": pose}, "test") == []
    after = path.read_text().splitlines()
    assert after[:2] == ["# Poses: test", "viewpoints:  # owner: workstream 3"]
    assert after[2].startswith("  inv1_cabinet: {asset: INV-1,") and after[2].endswith("}  # by the door")
    assert after[3] == "# the north cabinets"
    assert after[4].startswith("  inv2_cabinet: {asset: INV-2,") and after[4].endswith("}  # moved in May")
    assert after[5:7] == ["    # thermal comes later", "    # for now"]
    assert after[7].startswith("  inv3_cabinet: {") and not after[7].endswith("#")  # '#' inside a quoted string
    assert after[8:] == ["states:", "  INV-1: [healthy]"]
    vps = yaml.safe_load(path.read_text())["viewpoints"]
    assert vps["inv1_cabinet"] == {"asset": "INV-1", "modes": ["rgb", "thermal"], "pose": pose}
    assert vps["inv2_cabinet"] == {"asset": "INV-2", "modes": ["rgb"], "pose": pose}
    assert vps["inv3_cabinet"] == {"asset": "INV-3", "modes": ["rgb"], "note": "tag #3", "pose": pose}


def test_viewpoints_that_cannot_be_edited_line_by_line_are_refused_cleanly(scene, tmp_path, capsys):
    splat = tmp_path / "scene.ply"
    shutil.copy(scene.ply, splat)
    photos = tmp_path / "stations"
    photos.mkdir()
    shutil.copy(scene.photos / "inv1_cabinet.jpg", photos)
    vp = tmp_path / "viewpoints.yaml"
    vp.write_text("viewpoints: {inv1_cabinet: {asset: INV-1, modes: [rgb], pose: {position: [0, 0, 0], "
                  "target: [0, 0, 0]}}}\nstates: {}\n")
    before = vp.read_bytes()
    assert cli(splat, photos, vp, tmp_path / "out") == 2
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("nothing written: cannot edit") and "viewpoints:" in out[-1]
    assert vp.read_bytes() == before and not (tmp_path / "scene.registration.json").exists()


# one tag is not enough to fix the board in the splat, and only worth a warning in a photo

def test_board_with_one_tag_in_the_splat_is_refused(scene, tmp_path, capsys):
    splat = tmp_path / "one_tag.ply"
    synth.write_ply(splat, *synth.make_scene(scene.board, tags=(0,)))
    photos = tmp_path / "stations"
    photos.mkdir()
    shutil.copy(scene.photos / "inv1_cabinet.jpg", photos)
    vp = tmp_path / "viewpoints.yaml"
    shutil.copy(VIEWPOINTS, vp)
    before = vp.read_bytes()
    assert cli(splat, photos, vp, tmp_path / "out") == 1
    out = capsys.readouterr().out
    assert "only tag 0" in out and "Re-capture" in out and "--board-corners" in out
    assert vp.read_bytes() == before


def test_photo_with_one_tag_registers_with_a_warning(scene, tmp_path, capsys):
    splat = tmp_path / "scene.ply"
    shutil.copy(scene.ply, splat)
    photos = station_photos(scene, tmp_path / "stations", ["inv1_cabinet"], tags=(2,))
    vp = tmp_path / "viewpoints.yaml"
    shutil.copy(VIEWPOINTS, vp)
    assert cli(splat, photos, vp, tmp_path / "out") == 0
    out = capsys.readouterr().out
    assert re.search(r"inv1_cabinet\s+WARNING: PnP used only tag 2", out), out


# EXIF focal lengths are whole millimetres; the board corners refine them

def test_focal_length_is_refined_when_exif_is_a_millimetre_off(scene, tmp_path):
    splat = tmp_path / "scene.ply"
    shutil.copy(scene.ply, splat)
    photos = station_photos(scene, tmp_path / "stations", STATIONS, f35=27.0, true_f35=26.0)
    vp = tmp_path / "viewpoints.yaml"
    shutil.copy(VIEWPOINTS, vp)
    assert cli(splat, photos, vp, tmp_path / "out") == 0
    vps = yaml.safe_load(vp.read_text())["viewpoints"]
    for vid, station in STATIONS.items():
        position, _, _ = station_camera(*station)
        assert np.linalg.norm(vps[vid]["pose"]["position"] - synth.to_splat(position)) < 0.004 * synth.SCALE, vid
    views = json.loads((tmp_path / "scene.registration.json").read_text())["views"]
    for vid, v in views.items():
        assert v["focal_35mm"] == 27.0 and v["focal_source"] == "EXIF", vid
        assert v["focal_35mm_refined"] == pytest.approx(26.0, abs=0.15)
        assert v["focal_35mm_used"] == v["focal_35mm_refined"]

import base64
import importlib.util
import json
import struct
import sys
from datetime import datetime
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

from gridtwin.splat import segment_sam
from gridtwin.splat.index import SplatIndex
from gridtwin.splat.ply import load_ply
from gridtwin.splat.segment import (NONE, Camera, DepthMasks, Mask, MaskError, Plane, SamMasks, assets_doc, assign,
                                    components, decode_indices, depth_steps, encode_indices, render_view, segment,
                                    vote)
from scripts.segment_assets import main
from tests.splat import segment_scene, synth

IDS = {asset: i + 1 for i, asset in enumerate(segment_scene.BOXES)}  # 0 is table, wall and floaters


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    d = tmp_path_factory.mktemp("segment")
    ply, reg, vps, truth = segment_scene.write_scene(d)
    registration = json.loads(reg.read_text())
    splat = load_ply(ply)
    cams = [Camera.from_view(vid, segment_scene.STATIONS[vid][0], view) for vid, view in registration["views"].items()]
    plane = Plane.from_registration(registration, cams)
    return SimpleNamespace(dir=d, ply=ply, reg=reg, vps=vps, truth=truth, registration=registration, splat=splat,
                           cams=cams, plane=plane, seg=segment(splat, cams, DepthMasks(), plane))


def scores(seg, truth):
    """asset -> (precision, recall) of its labelled Gaussians against its true box."""
    out = {}
    for a, asset in enumerate(seg.assets):
        pred, true = seg.labels == a, truth == asset
        out[asset] = (np.sum(pred & true) / max(pred.sum(), 1), np.sum(pred & true) / true.sum())
    return out


def world(centers):
    """Splat coordinates back to the scene's metres."""
    return (centers - synth.SHIFT) @ synth.splat_rotation() / synth.SCALE


def flat_sheet(x0, x1, y0, y1, z, step=0.01):
    x, y = np.meshgrid(np.arange(x0, x1, step), np.arange(y0, y1, step))
    return np.stack([x.ravel(), y.ravel(), np.full(x.size, z)], axis=1)


def test_depth_masks_recover_every_box(scene):
    assert scene.seg.assets == sorted(segment_scene.BOXES) and not scene.seg.failed
    for asset, (p, r) in scores(scene.seg, scene.truth).items():
        assert p > 0.98 and r > 0.9, (asset, p, r)


def test_each_depth_mask_covers_its_own_box(scene):
    """Rendered with true ids as colours, the seed is on the station's box and the mask is nearly all that box."""
    splat, truth = scene.splat, scene.truth
    keep = splat.opacity >= 0.3
    ids = np.array([IDS.get(a, 0) for a in truth], np.uint8)
    index = SplatIndex(splat.centers[keep], np.repeat(ids[keep, None], 3, axis=1), splat.scale[keep])
    for vid, cam in scene.seg.cameras.items():
        label = index.render(cam.K, cam.R, cam.t, cam.size, 4)[0][..., 0]
        mask = scene.seg.masks[vid]
        x, y = mask.seed
        assert label[y, x] == IDS[cam.asset], vid
        own = label[mask.pixels] == IDS[cam.asset]
        drawn = (label == IDS[cam.asset]) & np.isfinite(scene.seg.renders[vid].depth)
        assert own.mean() > 0.97, (vid, own.mean())
        assert mask.pixels[drawn].mean() > 0.9, (vid, mask.pixels[drawn].mean())


def test_depth_steps_mark_only_the_far_side():
    d = np.full((4, 6), 2.0, np.float32)
    d[:, :3] = 1.0
    cut = depth_steps(d, 0.03)
    assert cut[:, 3].all() and cut.sum() == 4
    assert not depth_steps(d * np.linspace(1, 1.02, 6, dtype=np.float32), 0.03)[:, :3].any()


def test_render_holes_take_the_nearest_depth():
    centers = np.array([[0.0, 0.0, 1.0], [0.05, 0.0, 2.0]])
    index = SplatIndex(centers, np.zeros((2, 3), np.uint8), np.zeros(2))
    K = np.array([[100.0, 0, 9.5], [0, 100.0, 9.5], [0, 0, 1]])
    render = render_view(index, Camera("v", "A", K, np.eye(3), np.zeros(3), (20, 20), np.array([0, 0, 1.0])), fill=1)
    assert render.depth[10, 10] == 1.0 and render.depth[10, 11] == 1.0 and np.isinf(render.depth[0, 0])


def test_occluded_gaussians_get_no_vote_and_no_label():
    plate = flat_sheet(-0.3, 0.3, -0.3, 0.3, 1.0)     # in front: pixels 20 to 80 of the 100-pixel view
    hidden = flat_sheet(-0.5, 0.5, -0.5, 0.5, 2.0)    # straight behind the plate: pixels 25 to 75
    beside = flat_sheet(0.66, 0.9, -0.2, 0.2, 2.0)    # as deep as hidden, but in the clear at pixels 83 to 95
    centers = np.concatenate([plate, hidden, beside])
    part = np.repeat([0, 1, 2], [len(plate), len(hidden), len(beside)])
    index = SplatIndex(centers, np.zeros((len(centers), 3), np.uint8), np.full(len(centers), 0.012))
    K = np.array([[100.0, 0, 49.5], [0, 100.0, 49.5], [0, 0, 1]])
    cam = Camera("v", "A", K, np.eye(3), np.zeros(3), (100, 100), np.array([0, 0, 1.0]))
    render = render_view(index, cam)
    everything = Mask(np.ones((100, 100), bool), (50, 50), np.array([0, 0, 1.0]))
    inside, seen, _ = vote(centers, [cam], {"v": render}, {"v": everything}, ["A"])
    assert seen[0, part == 1].sum() == 0
    assert (seen[0, part == 0] == 1).all() and (seen[0, part == 2] == 1).all()
    labels = assign(inside, seen)
    assert (labels[part == 1] == NONE).all() and (labels[part != 1] == 0).all()
    # a second view of A whose mask is empty but sees the plate only through a screen casts no vote against it
    screen = flat_sheet(-1.0, 1.0, -1.0, 1.0, 0.5, 0.005)
    n = len(centers) + len(screen)
    walled = SplatIndex(np.concatenate([centers, screen]), np.zeros((n, 3), np.uint8), np.full(n, 0.012))
    blocked = Camera("w", "A", K, np.eye(3), np.zeros(3), (100, 100), np.array([0, 0, 0.5]))
    nothing = Mask(np.zeros((100, 100), bool), (50, 50), np.array([0, 0, 0.5]))
    inside, seen, _ = vote(centers, [cam, blocked], {"v": render, "w": render_view(walled, blocked)},
                           {"v": everything, "w": nothing}, ["A"])
    assert (seen[0, part == 0] == 1).all() and (assign(inside, seen)[part == 0] == 0).all()


def test_a_gaussian_claimed_by_two_assets_goes_to_the_larger_share():
    inside = np.array([[2, 1, 1, 1, 0, 1], [1, 0, 1, 2, 0, 1]])
    seen = np.array([[3, 2, 1, 1, 1, 1], [1, 2, 2, 2, 0, 1]])
    # 0: 2/3 against 1/1. 1: 1/2 is no win and 0/2 neither. 2: 1/1 against 1/2, which is no win.
    # 3: equal shares, so more in-mask votes win. 4: out-of-mask votes only. 5: a full tie goes to the first asset.
    assert assign(inside, seen).tolist() == [1, NONE, 0, 1, NONE, 0]
    only_first = np.array([[True] * 6, [False] * 6])
    assert assign(inside, seen, only_first).tolist() == [0, NONE, 0, 0, NONE, 0]


class Greedy:
    """Depth masks, except that one view's mask is the whole image, like a mask that bled everywhere."""
    def __init__(self, vid):
        self.vid, self.depth = vid, DepthMasks()

    def __call__(self, cam, render, plane):
        mask = self.depth(cam, render, plane)
        if cam.vid == self.vid:
            mask.pixels[:] = True
        return mask


def test_the_clean_pass_drops_mask_bleed_and_gives_it_back(scene):
    """One CB-1.5 view claims everything it sees. Without the clean pass CB-1.5 keeps the wall and other boxes' bits
    that only that view saw; with it, those pieces hold no CB-1.5 seed, so they go back to their own assets."""
    loose = scores(segment(scene.splat, scene.cams, Greedy("cb15_left"), scene.plane, clean_voxel=None), scene.truth)
    assert loose["CB-1.5"][0] < 0.9
    clean = segment(scene.splat, scene.cams, Greedy("cb15_left"), scene.plane)
    for asset, (p, r) in scores(clean, scene.truth).items():
        assert p > 0.98 and r > 0.9, (asset, p, r)


def test_fill_takes_the_backs_no_station_sees(scene):
    pts = world(scene.splat.centers)
    no_fill = segment(scene.splat, scene.cams, DepthMasks(), scene.plane, fill=False)
    for a, asset in enumerate(scene.seg.assets):
        (x0, _, _), (x1, y1, z1), _ = segment_scene.BOXES[asset]
        edge = segment_scene.STEP / 2  # the back's own edges are also the sides' and the top's, which stations see
        back = ((scene.truth == asset) & np.isclose(pts[:, 1], y1, atol=1e-6) & (pts[:, 0] > x0 + edge)
                & (pts[:, 0] < x1 - edge) & (pts[:, 2] < z1 - edge))
        assert back.sum() > 50
        assert (no_fill.labels[back] == a).mean() < 0.5  # only rows just under the top edge, within vis_tol of it
        assert (scene.seg.labels[back] == a).mean() > 0.9, asset


def test_components_join_touching_voxels_only():
    rng = np.random.default_rng(1)
    chain = np.c_[np.arange(0, 1, 0.01), np.zeros(100), np.zeros(100)]
    blob = rng.uniform(0, 0.1, (200, 3)) + [3, 0, 0]
    label = components(np.concatenate([chain, blob, [[0.99, 0.0249, 0.0249]]]), 0.02)
    assert len(set(label[:100])) == 1 and len(set(label[100:300])) == 1 and label[0] != label[100]
    assert label[-1] == label[0]  # in the diagonal neighbour cell


def test_assets_json_contract(scene):
    seg = scene.seg
    doc = json.loads(json.dumps(assets_doc(seg, scene.splat.centers, "assets/splats/scene.ply",
                                           scene.registration["splat_sha256"], "depth", "2026-10-17T14:05:00-07:00")))
    assert set(doc) == {"splat", "splat_sha256", "vertex_count", "method", "created_at", "assets", "views_used"}
    assert doc["vertex_count"] == len(scene.splat.centers) and doc["method"] == "depth"
    assert datetime.fromisoformat(doc["created_at"]).tzinfo is not None
    taken = np.zeros(len(scene.splat.centers), int)
    for a, asset in enumerate(seg.assets):
        entry = doc["assets"][asset]
        assert set(entry) == {"count", "centroid", "bbox", "indices_b64"}
        idx = decode_indices(entry["indices_b64"])
        raw = base64.b64decode(entry["indices_b64"])
        assert struct.unpack("<I", raw[:4])[0] == idx[0] and len(raw) == 4 * entry["count"]
        assert np.array_equal(idx, np.flatnonzero(seg.labels == a)) and np.all(np.diff(idx) > 0)
        pts = scene.splat.centers[idx]
        assert np.allclose(entry["centroid"], pts.mean(0), atol=1e-5)
        assert np.allclose(entry["bbox"], [pts.min(0), pts.max(0)], atol=1e-5)
        taken[idx] += 1
    assert taken.max() == 1
    for vid, info in doc["views_used"].items():
        assert info["asset"] == segment_scene.STATIONS[vid][0] and 0 < info["mask_coverage"] < 1
        assert 0 < info["in_mask"] <= info["voted"]
    assert np.array_equal(decode_indices(encode_indices([7, 3, 2**32 - 1])), [3, 7, 2**32 - 1])


def test_cli_writes_assets_and_overlays(scene, tmp_path, capsys):
    out = tmp_path / "scene.assets.json"
    code = main(["--splat", str(scene.ply), "--viewpoints", str(scene.vps), "--out", str(out),
                 "--overlays", str(tmp_path / "overlays")])
    printed = capsys.readouterr().out
    assert code == 0, printed
    doc = json.loads(out.read_text())
    assert doc["splat_sha256"] == scene.registration["splat_sha256"] and sorted(doc["assets"]) == scene.seg.assets
    for a, asset in enumerate(scene.seg.assets):
        indices = decode_indices(doc["assets"][asset]["indices_b64"])
        assert np.array_equal(indices, np.flatnonzero(scene.seg.labels == a))
        assert f"{asset:8s} {doc['assets'][asset]['count']:7d} Gaussians from 3 views" in printed
    shots = sorted((tmp_path / "overlays").glob("*.jpg"))
    assert [p.stem for p in shots] == sorted(segment_scene.STATIONS)
    assert Image.open(shots[0]).size == (2 * 640 + 4, 480 + 28)


def test_cli_exit_codes(scene, tmp_path, capsys):
    base = ["--splat", str(scene.ply), "--viewpoints", str(scene.vps), "--overlays", str(tmp_path / "o"),
            "--out", str(tmp_path / "a.json")]
    assert main(base + ["--views", "inv1_cabinet,nope"]) == 2
    assert "not registered: nope" in capsys.readouterr().out
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps({**scene.registration, "splat_sha256": "0" * 64}))
    assert main(base + ["--registration", str(stale)]) == 2
    assert "made for another file" in capsys.readouterr().out
    assert main(["--splat", str(tmp_path / "none.ply")]) == 2
    away = json.loads(json.dumps(scene.registration))
    view = away["views"]["inv1_cabinet"]
    view["R"] = (np.diag([1.0, -1, -1]) @ np.asarray(view["R"])).tolist()  # turned round to face the open room
    view["t"] = (-np.asarray(view["R"]) @ np.asarray(view["position"])).tolist()
    turned = tmp_path / "turned.json"
    turned.write_text(json.dumps(away))
    assert main(base + ["--registration", str(turned), "--views", "inv1_cabinet,inv1_left,cb15_box"]) == 1
    assert "inv1_cabinet     FAILED: nothing but floor or empty space" in capsys.readouterr().out
    assert json.loads((tmp_path / "a.json").read_text())["assets"]["INV-1"]["count"] > 0


def test_cli_sam_without_a_model_names_the_download(scene, tmp_path, capsys):
    if importlib.util.find_spec("torch"):
        pytest.skip("this interpreter may run SAM in-process")
    code = main(["--splat", str(scene.ply), "--viewpoints", str(scene.vps), "--masks", "sam",
                 "--sam-python", str(tmp_path / "no-python"), "--out", str(tmp_path / "a.json")])
    printed = capsys.readouterr().out
    assert code == 2 and not (tmp_path / "a.json").exists()
    assert "https://huggingface.co/facebook/sam2.1-hiera-tiny" in printed and "155,908,064 bytes" in printed


def test_sam_helper_reports_a_missing_torch():
    """The helper handshake: an interpreter without torch answers with an error line instead of hanging."""
    if importlib.util.find_spec("torch"):
        pytest.skip("this interpreter has torch")
    with pytest.raises(segment_sam.ModelUnavailable, match="No module named 'torch'"):
        segment_sam.HelperPredictor(sys.executable)


class ColourSam:
    """Stands in for SAM: the solid region of the prompt point's colour inside the box, then the box, then
    everything, scored in that order."""
    def __init__(self):
        self.calls = []

    def predict(self, rgb, point, box):
        self.calls.append((rgb.shape, point, box))
        x, y = (int(round(v)) for v in point)
        lab = cv2.cvtColor(cv2.medianBlur(rgb, 5).astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
        near = np.linalg.norm(lab - np.median(lab[y - 2:y + 3, x - 2:x + 3].reshape(-1, 3), axis=0), axis=2) < 10
        inbox = np.zeros(rgb.shape[:2], bool)
        x0, y0, x1, y1 = np.rint(box).astype(int)
        inbox[y0:y1 + 1, x0:x1 + 1] = True
        comp = cv2.connectedComponents((near & inbox).astype(np.uint8), connectivity=4)[1]
        outline = cv2.findContours((comp == comp[y, x]).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]
        region = cv2.drawContours(np.zeros(rgb.shape[:2], np.uint8), outline, -1, 1, cv2.FILLED).astype(bool)
        return np.stack([region, inbox, np.ones_like(inbox)]), np.array([0.9, 0.6, 0.3])

    def close(self):
        pass


def test_sam_masks_prompt_the_photo_and_vote_like_depth_masks(scene):
    fake = ColourSam()
    seg = segment(scene.splat, scene.cams, SamMasks(fake), scene.plane)
    assert not seg.failed and len(fake.calls) == len(scene.cams)
    for shape, (px, py), (x0, y0, x1, y1) in fake.calls:
        assert shape[:2] == (segment_scene.SIZE[1], segment_scene.SIZE[0])  # 960 wide photos stay under 1024
        assert x0 < px < x1 and y0 < py < y1 and x1 < shape[1] and y1 < shape[0]
    for asset, (p, r) in scores(seg, scene.truth).items():
        assert p > 0.98 and r > 0.9, (asset, p, r)
    assert SamMasks(fake).name == "sam:facebook/sam2.1-hiera-tiny"


def test_sam_masks_need_the_photo(scene):
    cam = scene.seg.cameras["inv1_cabinet"]
    blind = Camera(cam.vid, cam.asset, cam.K, cam.R, cam.t, cam.size, cam.target, scene.dir / "missing.jpg")
    with pytest.raises(MaskError, match="no photo"):
        SamMasks(ColourSam())(blind, scene.seg.renders["inv1_cabinet"], scene.plane)

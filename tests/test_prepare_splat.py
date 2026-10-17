"""scripts/prepare_splat.py: PLY round trip, visibility thinning, cropping, SH removal, credits and the CLI.

Also holds `synthetic_scene` and `write_ply`, which build a plant-room-like stand-in (floor and walls, boxy machines,
pipe runs, a few far floaters) of any size. The browser check in viewer/splat-check.html uses them for multi-million
splat files:  python3 -c "from tests.test_prepare_splat import *; write_ply('assets/splats/synth.ply', synthetic_scene(4_000_000))"
"""
import json

import numpy as np
import pytest

from scripts import prepare_splat as ps

SH_C0 = 0.28209479177387814
PLY_TYPE = {"f4": "float", "f8": "double", "u1": "uchar", "i4": "int", "u4": "uint", "u2": "ushort", "i2": "short"}


def splat_dtype(sh_rest=0, normals=True):
    names = ["x", "y", "z"] + (["nx", "ny", "nz"] if normals else []) + ["f_dc_0", "f_dc_1", "f_dc_2"]
    names += [f"f_rest_{i}" for i in range(sh_rest)]
    names += ["opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
    return np.dtype([(n, "<f4") for n in names])


def synthetic_scene(n, seed=0, sh_rest=0, normals=True):
    """n Gaussians on the surfaces of a 24 x 7 x 8 m plant room: 35% walls, 35% machines, 30% pipes, 0.2% floaters."""
    rng = np.random.default_rng(seed)
    v = np.zeros(n, splat_dtype(sh_rest, normals))
    xyz, rgb = np.zeros((n, 3)), np.zeros((n, 3))
    quat = np.zeros((n, 4))
    quat[:, 0] = 1.0
    thin_axis = np.zeros(n, int)  # which local axis is the flat one of each surface Gaussian
    kind = rng.choice(4, n, p=[0.35, 0.35, 0.298, 0.002])
    sx, sy, sz = 24.0, 7.0, 8.0

    w = np.flatnonzero(kind == 0)  # floor, ceiling and four walls, chosen by area
    area = np.array([sx * sz, sx * sz, sx * sy, sx * sy, sy * sz, sy * sz])
    face = rng.choice(6, len(w), p=area / area.sum())
    p = rng.random((len(w), 3)) * [sx, sy, sz]
    axis = np.array([1, 1, 2, 2, 0, 0])[face]
    p[np.arange(len(w)), axis] = np.array([0, sy, 0, sz, 0, sx])[face]
    xyz[w], thin_axis[w] = p, axis
    rgb[w] = np.where(face[:, None] < 2, [0.32, 0.33, 0.34], [0.66, 0.68, 0.7])

    m = np.flatnonzero(kind == 1)  # boxes standing on the floor
    nb = 40
    dims = np.stack([rng.uniform(0.8, 3.0, nb), rng.uniform(0.8, 3.2, nb), rng.uniform(0.6, 2.0, nb)], axis=1)
    centres = np.stack([rng.uniform(2, sx - 2, nb), dims[:, 1] / 2, rng.uniform(1, sz - 1, nb)], axis=1)
    palette = rng.random((nb, 3)) * 0.6 + 0.2
    b = rng.integers(0, nb, len(m))
    q = rng.random((len(m), 3)) - 0.5
    ax = rng.integers(0, 3, len(m))
    q[np.arange(len(m)), ax] = np.where(rng.random(len(m)) < 0.5, -0.5, 0.5)
    xyz[m], thin_axis[m] = centres[b] + q * dims[b], ax
    rgb[m] = palette[b] * (0.85 + 0.3 * rng.random((len(m), 1)))

    c = np.flatnonzero(kind == 2)  # pipe runs along x at a few heights, radius 6 to 20 cm
    npipe = 24
    py, pz, pr = rng.uniform(0.5, sy - 1, npipe), rng.uniform(0.5, sz - 0.5, npipe), rng.uniform(0.06, 0.2, npipe)
    pcol = rng.random((npipe, 3)) * 0.7 + 0.15
    k = rng.integers(0, npipe, len(c))
    th = rng.random(len(c)) * 2 * np.pi
    xyz[c] = np.stack([rng.random(len(c)) * sx, py[k] + pr[k] * np.cos(th), pz[k] + pr[k] * np.sin(th)], axis=1)
    thin_axis[c] = 1  # local y is the radial direction once rotated about x by th
    quat[c] = np.stack([np.cos(th / 2), np.sin(th / 2), np.zeros(len(c)), np.zeros(len(c))], axis=1)
    rgb[c] = pcol[k]

    f = np.flatnonzero(kind == 3)  # far floaters: big, faint, scattered well outside the room
    xyz[f] = rng.uniform(-80, 100, (len(f), 3))
    rgb[f] = rng.random((len(f), 3))

    # like a real scan, splat size follows density: about 1700 m2 of surface, sigma a little over half the spacing
    sigma = float(np.clip(0.55 * np.sqrt(1700.0 / n), 0.004, 0.05))
    ln_scale = np.log(sigma) + rng.normal(0, 0.4, (n, 3))
    ln_scale[np.arange(n), thin_axis] = np.log(0.25 * sigma)
    ln_scale[f] = np.log(rng.uniform(0.3, 2.0, (len(f), 3)))
    logit = np.clip(rng.normal(2.5, 1.5, n), -6, 8)
    logit[f] = rng.uniform(-4, -1, len(f))

    v["x"], v["y"], v["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    for i in range(3):
        v[f"f_dc_{i}"] = (np.clip(rgb[:, i], 0, 1) - 0.5) / SH_C0
        v[f"scale_{i}"] = ln_scale[:, i]
    v["opacity"] = logit
    for i in range(4):
        v[f"rot_{i}"] = quat[:, i]
    for i in range(sh_rest):
        v[f"f_rest_{i}"] = rng.normal(0, 0.05, n)
    return v


def write_ply(path, vertex, comments=(), endian="<", extra=()):
    """Write structured arrays as a PLY by hand, independent of the module under test.
    extra: more (name, structured array) elements written after the vertex element."""
    fmt = "binary_little_endian" if endian == "<" else "binary_big_endian"
    lines = ["ply", f"format {fmt} 1.0", *[f"comment {c}" for c in comments]]
    body = []
    for name, arr in [("vertex", vertex), *extra]:
        arr = arr.astype(arr.dtype.newbyteorder(endian))
        lines.append(f"element {name} {len(arr)}")
        lines += [f"property {PLY_TYPE[arr.dtype[f].str[1:]]} {f}" for f in arr.dtype.names]
        body.append(arr.tobytes())
    with open(path, "wb") as fh:
        fh.write(("\n".join(lines + ["end_header"]) + "\n").encode("ascii"))
        for chunk in body:
            fh.write(chunk)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


# ---------- reader and writer ----------

def test_round_trip_is_byte_identical_with_every_property(tmp_path):
    v = synthetic_scene(500, seed=1, sh_rest=45)
    src, dst = tmp_path / "a.ply", tmp_path / "b.ply"
    write_ply(src, v, comments=["Created by test", "second line"])
    ps.write_ply(dst, ps.read_ply(src))
    assert dst.read_bytes() == src.read_bytes()


def test_read_exposes_all_vertex_properties_in_file_order(tmp_path):
    v = synthetic_scene(50, sh_rest=45)
    write_ply(tmp_path / "a.ply", v)
    got = ps.read_ply(tmp_path / "a.ply").vertex
    assert got.dtype.names == v.dtype.names
    assert sum(n.startswith("f_rest_") for n in got.dtype.names) == 45
    for name in v.dtype.names:
        np.testing.assert_array_equal(got[name], v[name])


def test_mixed_property_types_and_other_elements_survive(tmp_path):
    n = 40
    v = np.zeros(n, [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("label", "u1"), ("weight", "<f8"), ("opacity", "<f4"),
                     ("scale_0", "<f4"), ("scale_1", "<f4"), ("scale_2", "<f4")])
    v["x"] = np.arange(n)
    v["label"] = np.arange(n) % 250
    v["weight"] = np.arange(n) / 7.0
    extra = np.zeros(3, [("a", "<i4"), ("b", "<u2")])
    extra["a"] = [5, -6, 7]
    src, dst = tmp_path / "a.ply", tmp_path / "b.ply"
    write_ply(src, v, extra=[("camera", extra)])
    ply = ps.read_ply(src)
    assert [e.name for e in ply.elements] == ["vertex", "camera"]
    ps.write_ply(dst, ply)
    assert dst.read_bytes() == src.read_bytes()


def test_big_endian_round_trip(tmp_path):
    v = synthetic_scene(64, sh_rest=3)
    src, dst = tmp_path / "a.ply", tmp_path / "b.ply"
    write_ply(src, v, endian=">")
    ply = ps.read_ply(src)
    np.testing.assert_array_equal(ply.vertex["x"], v["x"])
    ps.write_ply(dst, ply)
    assert dst.read_bytes() == src.read_bytes()


def test_header_count_follows_the_vertex_array(tmp_path):
    write_ply(tmp_path / "a.ply", synthetic_scene(100))
    ply = ps.read_ply(tmp_path / "a.ply")
    ply.set_vertex(ply.vertex[:30])
    ps.write_ply(tmp_path / "b.ply", ply)
    assert b"element vertex 30\n" in (tmp_path / "b.ply").read_bytes()[:400]
    assert len(ps.read_ply(tmp_path / "b.ply").vertex) == 30


def test_ascii_ply_is_refused_with_a_hint(tmp_path):
    (tmp_path / "a.ply").write_bytes(b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nend_header\n1\n")
    with pytest.raises(ps.PrepareError, match="binary"):
        ps.read_ply(tmp_path / "a.ply")


def test_compressed_ply_is_refused_with_a_hint(tmp_path):
    chunk = np.zeros(1, [("min_x", "<f4"), ("max_x", "<f4")])
    packed = np.zeros(4, [("packed_position", "<u4"), ("packed_rotation", "<u4"), ("packed_scale", "<u4"),
                          ("packed_color", "<u4")])
    with open(tmp_path / "a.ply", "wb") as fh:
        fh.write(b"ply\nformat binary_little_endian 1.0\nelement chunk 1\nproperty float min_x\nproperty float max_x\n"
                 b"element vertex 4\nproperty uint packed_position\nproperty uint packed_rotation\n"
                 b"property uint packed_scale\nproperty uint packed_color\nend_header\n" + chunk.tobytes() + packed.tobytes())
    with pytest.raises(ps.PrepareError, match="compressed"):
        ps.read_ply(tmp_path / "a.ply")


def test_not_a_ply_and_truncated_files_are_refused(tmp_path):
    (tmp_path / "a.ply").write_bytes(b"not a ply file")
    with pytest.raises(ps.PrepareError, match="PLY"):
        ps.read_ply(tmp_path / "a.ply")
    write_ply(tmp_path / "b.ply", synthetic_scene(100))
    (tmp_path / "c.ply").write_bytes((tmp_path / "b.ply").read_bytes()[:-50])
    with pytest.raises(ps.PrepareError, match="truncated"):
        ps.read_ply(tmp_path / "c.ply")


# ---------- visibility and thinning ----------

def test_visibility_is_opacity_times_volume():
    v = np.zeros(3, splat_dtype(0, False))
    v["opacity"] = [0.0, 2.0, -3.0]
    v["scale_0"], v["scale_1"], v["scale_2"] = np.log(0.1), np.log(0.2), np.log(0.3)
    np.testing.assert_allclose(ps.visibility(v), sigmoid(np.array([0.0, 2.0, -3.0])) * 0.1 * 0.2 * 0.3, rtol=1e-5)


def test_max_splats_keeps_the_most_visible_in_original_order():
    v = synthetic_scene(2000, seed=3)
    keep = ps.select_visible(v, 500)
    score = ps.visibility(v)
    expected = np.sort(np.lexsort((np.arange(len(v)), -score))[:500])
    np.testing.assert_array_equal(keep, expected)
    assert np.all(np.diff(keep) > 0)


def test_volume_can_outweigh_opacity():
    v = np.zeros(2, splat_dtype(0, False))
    v["opacity"] = [6.0, 0.0]                    # first is nearly opaque but tiny, second is half opaque and large
    v["scale_0"] = v["scale_1"] = v["scale_2"] = [np.log(0.01), np.log(0.5)]
    assert list(ps.select_visible(v, 1)) == [1]


def test_ties_are_broken_by_original_order():
    v = np.zeros(10, splat_dtype(0, False))
    v["opacity"] = 1.0
    v["scale_0"] = v["scale_1"] = v["scale_2"] = -3.0
    assert list(ps.select_visible(v, 4)) == [0, 1, 2, 3]
    v["opacity"][7] = 4.0                        # one clear winner, the rest still tie
    assert list(ps.select_visible(v, 4)) == [0, 1, 2, 7]


def test_max_splats_at_or_above_the_count_keeps_everything():
    v = synthetic_scene(100)
    assert list(ps.select_visible(v, 100)) == list(range(100))
    assert list(ps.select_visible(v, 10_000)) == list(range(100))


def test_nan_scores_are_dropped_first():
    v = np.zeros(4, splat_dtype(0, False))
    v["opacity"] = [np.nan, 1.0, 1.0, 1.0]
    v["scale_0"] = v["scale_1"] = v["scale_2"] = -3.0
    assert list(ps.select_visible(v, 3)) == [1, 2, 3]


def test_max_splats_must_be_positive():
    with pytest.raises(ps.PrepareError):
        ps.select_visible(synthetic_scene(10), 0)


# ---------- cropping ----------

def grid(n=11):
    g = np.arange(n, dtype=np.float32)
    v = np.zeros(n ** 3, splat_dtype(0, False))
    v["x"], v["y"], v["z"] = (a.ravel() for a in np.meshgrid(g, g, g, indexing="ij"))
    return v


def test_crop_box_is_inclusive():
    v = grid()
    mask = ps.crop_mask(v, box=(2, 3, 4, 5, 5, 6))
    assert mask.sum() == 4 * 3 * 3
    assert v["x"][mask].min() == 2 and v["x"][mask].max() == 5


def test_crop_sphere():
    v = grid()
    mask = ps.crop_mask(v, sphere=(5, 5, 5, 2))
    assert mask.sum() == 33                      # lattice points within radius 2 of a lattice point
    pts = np.stack([v["x"], v["y"], v["z"]], axis=1)[mask]
    assert np.all(np.linalg.norm(pts - 5, axis=1) <= 2 + 1e-9)


def test_crop_box_and_sphere_intersect():
    v = grid()
    box, sphere = (0, 0, 0, 5, 10, 10), (5, 5, 5, 2)
    both = ps.crop_mask(v, box=box, sphere=sphere)
    np.testing.assert_array_equal(both, ps.crop_mask(v, box=box) & ps.crop_mask(v, sphere=sphere))
    assert 0 < both.sum() < ps.crop_mask(v, sphere=sphere).sum()


def test_crop_validates_its_arguments():
    v = grid(3)
    with pytest.raises(ps.PrepareError):
        ps.crop_mask(v, box=(5, 0, 0, 1, 1, 1))
    with pytest.raises(ps.PrepareError):
        ps.crop_mask(v, sphere=(0, 0, 0, -1))
    with pytest.raises(ps.PrepareError):
        ps.crop_mask(v, auto=(60, 40))


def test_crop_auto_drops_far_floaters_and_keeps_the_room():
    rng = np.random.default_rng(0)
    n = 5000
    v = np.zeros(n + 12, splat_dtype(0, False))
    v["x"][:n], v["y"][:n], v["z"][:n] = rng.uniform(0, 24, n), rng.uniform(0, 7, n), rng.uniform(0, 8, n)
    v["x"][n:], v["y"][n:], v["z"][n:] = 500.0 * np.arange(1, 13), -300.0, 900.0
    mask = ps.crop_mask(v, auto=(1, 99))
    assert not mask[n:].any()
    assert mask[:n].mean() > 0.99                # the margin keeps the walls
    assert ps.crop_mask(v, auto=(1, 99), margin=0.0)[:n].mean() < mask[:n].mean()


# ---------- SH, normals, recentring ----------

def test_drop_sh_removes_only_f_rest_and_cuts_size():
    v = synthetic_scene(1000, sh_rest=45)
    out = ps.drop_properties(v, ps.is_sh_rest)
    assert out.dtype.names == tuple(n for n in v.dtype.names if not n.startswith("f_rest_"))
    for name in out.dtype.names:
        np.testing.assert_array_equal(out[name], v[name])
    assert v.dtype.itemsize / out.dtype.itemsize == pytest.approx(62 / 17)   # about 3.6x smaller


def test_drop_normals():
    v = synthetic_scene(10)
    out = ps.drop_properties(v, ps.is_normal)
    assert out.dtype.names == tuple(n for n in v.dtype.names if n not in ("nx", "ny", "nz"))


def test_recentre_moves_the_median_to_the_origin():
    v = synthetic_scene(2000, seed=2)
    v["x"] += 5000.0
    shifted, offset = ps.recentre(v)
    assert offset[0] == pytest.approx(5012.0, abs=2.0)
    for axis in "xyz":
        assert abs(np.median(shifted[axis])) < 1e-3
    np.testing.assert_allclose(shifted["x"].astype(np.float64) + offset[0], v["x"], atol=1e-2)
    assert shifted["opacity"].tolist() == v["opacity"].tolist()


# ---------- extent and credits ----------

def test_extent_reports_box_centre_and_size():
    e = ps.extent(grid(11))
    assert e["min"] == [0, 0, 0] and e["max"] == [10, 10, 10]
    assert e["centre"] == [5, 5, 5] and e["size"] == [10, 10, 10]
    assert e["diagonal"] == pytest.approx(10 * 3 ** 0.5)


def test_credits_file_has_the_agreed_fields(tmp_path):
    path = ps.write_credits(tmp_path / "engineroom.ply", title="Engine room", author="someone",
                            url="https://example.org/s/1", changes="thinned from 8,000,000 to 2,000,000 splats, SH removed")
    assert path == tmp_path / "engineroom.credits.json"
    assert json.loads(path.read_text()) == {
        "title": "Engine room", "author": "someone", "url": "https://example.org/s/1", "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "changes": "thinned from 8,000,000 to 2,000,000 splats, SH removed"}


# ---------- command line ----------

def run(capsys, *argv):
    code = ps.main([str(a) for a in argv])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_cli_thins_drops_sh_and_reports_before_and_after(tmp_path, capsys):
    src, dst = tmp_path / "src.ply", tmp_path / "scene.ply"
    v = synthetic_scene(4000, seed=5, sh_rest=45)
    write_ply(src, v, comments=["Created by test"])
    code, out, err = run(capsys, src, "--out", dst, "--max-splats", 1000, "--drop-sh")
    assert code == 0, err
    got = ps.read_ply(dst).vertex
    assert len(got) == 1000
    assert not [n for n in got.dtype.names if n.startswith("f_rest_")]
    np.testing.assert_array_equal(got["x"], v["x"][ps.select_visible(v, 1000)])   # original order, most visible
    assert "4,000" in out and "1,000" in out
    assert f"{src.stat().st_size:,}" in out and f"{dst.stat().st_size:,}" in out
    assert "extent" in out.lower()
    assert dst.stat().st_size < src.stat().st_size / 4


def test_cli_crops_before_thinning_and_names_the_changes_in_credits(tmp_path, capsys):
    src, dst = tmp_path / "src.ply", tmp_path / "scene.ply"
    write_ply(src, synthetic_scene(6000, seed=6, sh_rest=9))
    code, out, err = run(capsys, src, "--out", dst, "--crop-auto", "--max-splats", 2000, "--drop-sh",
                         "--title", "Engine room", "--author", "tosolini", "--url", "https://superspl.at/scene/x")
    assert code == 0, err
    got = ps.read_ply(dst).vertex
    assert len(got) == 2000
    assert np.abs(got["x"]).max() < 60                                  # the far floaters are gone
    credits = json.loads((tmp_path / "scene.credits.json").read_text())
    assert credits["license"] == "CC BY 4.0" and credits["author"] == "tosolini"
    for word in ("cropped", "thinned", "SH removed", "2,000"):
        assert word in credits["changes"]


def test_cli_box_crop_and_defaults(tmp_path, capsys):
    src = tmp_path / "src.ply"
    write_ply(src, synthetic_scene(3000, seed=7))
    code, out, err = run(capsys, src, "--crop-box", 0, 0, 0, 12, 7, 8)
    assert code == 0, err
    got = ps.read_ply(tmp_path / "src-prepared.ply").vertex             # default output next to the input
    assert 0 < len(got) < 3000
    assert got["x"].max() <= 12 and got["x"].min() >= 0
    assert not (tmp_path / "src-prepared.credits.json").exists()        # no credit flags, no credits file


def test_cli_refuses_to_overwrite_its_input_and_bad_inputs(tmp_path, capsys):
    src = tmp_path / "src.ply"
    write_ply(src, synthetic_scene(100))
    code, out, err = run(capsys, src, "--out", src)
    assert code == 2 and "overwrite" in err
    code, out, err = run(capsys, tmp_path / "missing.ply")
    assert code == 2 and "missing.ply" in err
    code, out, err = run(capsys, src, "--title", "only a title")
    assert code == 2 and "--author" in err
    code, out, err = run(capsys, src, "--crop-box", 5, 0, 0, 1, 1, 1)
    assert code == 2


def test_cli_without_options_copies_the_scene_unchanged(tmp_path, capsys):
    src, dst = tmp_path / "src.ply", tmp_path / "out.ply"
    write_ply(src, synthetic_scene(300, sh_rest=45))
    code, out, err = run(capsys, src, "--out", dst)
    assert code == 0, err
    assert dst.read_bytes() == src.read_bytes()


def test_cli_warns_when_the_scene_is_far_from_the_origin(tmp_path, capsys):
    src = tmp_path / "src.ply"
    v = synthetic_scene(300)
    v["x"] += 4000.0
    write_ply(src, v)
    code, out, err = run(capsys, src, "--out", tmp_path / "o.ply")
    assert code == 0, err
    assert "16-bit" in out and "--recentre" in out
    code, out, err = run(capsys, src, "--out", tmp_path / "o2.ply", "--recentre")
    assert code == 0 and "16-bit" not in out
    assert abs(np.median(ps.read_ply(tmp_path / "o2.ply").vertex["x"])) < 1e-3


def test_synthetic_scene_is_the_requested_size_and_deterministic():
    a, b = synthetic_scene(5000, seed=9), synthetic_scene(5000, seed=9)
    assert a.tobytes() == b.tobytes()
    assert len(a) == 5000 and np.isfinite(a["x"]).all()
    inside = (a["x"] > -1) & (a["x"] < 25) & (a["y"] > -1) & (a["y"] < 8) & (a["z"] > -1) & (a["z"] < 9)
    assert 0.99 < inside.mean() < 1.0                                   # a few far floaters sit outside the room

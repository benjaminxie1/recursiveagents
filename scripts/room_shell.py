"""Close a scan into a room: add a floor and a ceiling of flat Gaussians to a .splat whose capture was cropped flat
on top (no ceiling) or has holes in the floor.

Both cover the scan's footprint (every cell with splats in it, closed and hole-filled) and take their colour from the
scan itself: the floor from the splats near floor level, the ceiling from the band under the crop, blurred and infilled
where the scan has none. The ceiling gets deck beams and a few strip lights so it reads as a ceiling, not a lid.
Heights are along `--up` in the file's own units: `--floor` and `--ceiling` are dot(position, up) values.

    python -m scripts.room_shell assets/splats/tug-thin.splat assets/splats/tugboat.splat \
        --up 0,-1,0 --floor -0.24 --ceiling 1.6
"""
import argparse
from pathlib import Path

import cv2
import numpy as np

SPLAT_ROW = np.dtype([("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])
CELL = 0.1  # footprint grid (file units)


def blur_fill(sum_c, w, sigma):
    """Colour per cell from summed colour and weight: normalised Gaussian blur, so empty cells take their neighbours'."""
    k = int(6 * sigma) | 1
    num = cv2.GaussianBlur(sum_c, (k, k), sigma)
    den = cv2.GaussianBlur(w, (k, k), sigma)
    return num / np.maximum(den, 1e-6)[..., None], den


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--up", default="0,-1,0", help="world up in the file, a signed axis, e.g. 0,-1,0")
    ap.add_argument("--floor", type=float, required=True, help="floor height (dot(position, up))")
    ap.add_argument("--ceiling", type=float, required=True, help="ceiling height (dot(position, up))")
    ap.add_argument("--floor-step", type=float, default=0.035)
    ap.add_argument("--ceiling-step", type=float, default=0.05)
    ap.add_argument("--beam-every", type=float, default=0.6, help="deck beam spacing along the room's long side")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    up = np.array([float(c) for c in a.up.split(",")])
    if np.count_nonzero(up) != 1:
        raise SystemExit("--up must be a signed axis, e.g. 0,-1,0")
    k = int(np.argmax(np.abs(up)))
    sgn = float(np.sign(up[k]))
    ha, hb = [i for i in range(3) if i != k]  # the two horizontal axes
    rng = np.random.default_rng(a.seed)

    v = np.frombuffer(a.src.read_bytes(), SPLAT_ROW)
    p = v["pos"].astype(np.float64)
    height = a.ceiling - a.floor
    h = sgn * p[:, k] - a.floor  # height above the floor
    u, w = p[:, ha], p[:, hb]
    u0, w0 = np.percentile(u, 0.2) - 0.3, np.percentile(w, 0.2) - 0.3
    nu = int((np.percentile(u, 99.8) + 0.3 - u0) / CELL) + 1
    nw = int((np.percentile(w, 99.8) + 0.3 - w0) / CELL) + 1
    iu = np.clip(((u - u0) / CELL).astype(int), 0, nu - 1)
    iw = np.clip(((w - w0) / CELL).astype(int), 0, nw - 1)

    # footprint: cells with splats between floor and ceiling, closed over gaps up to half a metre and hole-filled
    inside = (h > -0.3) & (h < height + 0.05)
    occ = np.zeros((nw, nu), np.uint8)
    occ[iw[inside], iu[inside]] = 255
    occ = cv2.morphologyEx(occ, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    flood = cv2.copyMakeBorder(occ, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    cv2.floodFill(flood, None, (0, 0), 128)
    foot = flood[1:-1, 1:-1] != 128
    foot = cv2.dilate(foot.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0  # tuck under the walls

    rgb = v["rgba"][:, :3].astype(np.float32) / 255
    alpha = v["rgba"][:, 3].astype(np.float32) / 255

    def band_colour(m, sigma):
        s = np.zeros((nw, nu, 3), np.float32)
        wt = np.zeros((nw, nu), np.float32)
        np.add.at(s, (iw[m], iu[m]), rgb[m] * alpha[m, None])
        np.add.at(wt, (iw[m], iu[m]), alpha[m])
        near, dn = blur_fill(s, wt, sigma)
        far, _ = blur_fill(s, wt, 4 * sigma)  # where even the near blur has nothing
        mix = np.clip(dn / 0.5, 0, 1)[..., None]
        return near * mix + far * (1 - mix)

    floor_col = band_colour((h > -0.12) & (h < 0.15), 3.0)
    ceil_col = band_colour(h > height - 0.3, 4.0)
    parts = []

    def sheet(step, colour, level, keep=None, opacity=0.97, flat=0.003, shade=1.0, noise=0.035):
        """Jittered flat discs over the footprint at one height; returns how many."""
        gu, gw = np.meshgrid(np.arange(u0, u0 + nu * CELL, step), np.arange(w0, w0 + nw * CELL, step))
        gu = gu.ravel() + rng.uniform(-0.45, 0.45, gu.size) * step
        gw = gw.ravel() + rng.uniform(-0.45, 0.45, gw.size) * step
        cu = np.clip(((gu - u0) / CELL).astype(int), 0, nu - 1)
        cw = np.clip(((gw - w0) / CELL).astype(int), 0, nw - 1)
        m = foot[cw, cu] & (keep(gu, gw) if keep else True)
        gu, gw, cu, cw = gu[m], gw[m], cu[m], cw[m]
        n = len(gu)
        pos = np.zeros((n, 3))
        pos[:, ha], pos[:, hb] = gu, gw
        pos[:, k] = sgn * (a.floor + level + rng.normal(0, 0.002, n))
        col = colour(gu, gw) if callable(colour) else colour[cw, cu]
        col = np.clip(col * shade * rng.lognormal(0, noise, (n, 1)), 0, 1)
        sig = np.empty((n, 3))
        sig[:] = (0.62 * step * rng.lognormal(0, 0.12, n))[:, None]
        sig[:, k] = flat
        parts.append((pos, sig, col, np.full(n, opacity)))
        return n

    n_floor = sheet(a.floor_step, floor_col, -0.035, opacity=0.98, shade=0.92)

    # the overhead is painted steel deck plate (lighter than the scan's top band, which is mostly cropped equipment)
    # with box beams across the short side, repeating down the long side like a ship's frames. Crisp edges and
    # high-contrast lines are what make a flat surface read as sharp, so the beams get real sides and the lights are
    # thin bright tubes.
    paint = np.array([0.80, 0.80, 0.77], np.float32)
    deck_col = 0.45 * ceil_col + 0.55 * paint
    long_u = nu >= nw
    along = (lambda gu, gw: gu - u0) if long_u else (lambda gu, gw: gw - w0)
    BW, BD = 0.1, 0.12  # beam width and depth
    beam = lambda gu, gw: np.mod(along(gu, gw), a.beam_every) < BW
    n_ceil = sheet(a.ceiling_step, deck_col, height, opacity=0.99, shade=0.82, noise=0.02)
    n_beam = sheet(0.024, deck_col, height - BD, keep=beam, opacity=0.995, shade=0.9, noise=0.015)

    # beam sides: vertical sheets on both faces of every beam, shaded darker than the underside
    side_step = 0.026
    a_len = (nu if long_u else nw) * CELL
    faces = np.concatenate([np.arange(0, a_len, a.beam_every), np.arange(0, a_len, a.beam_every) + BW])
    x_len = (nw if long_u else nu) * CELL
    xs = np.arange(0, x_len, side_step)
    ys = np.arange(height - BD + side_step / 2, height, side_step)
    F, X, Y = np.meshgrid(faces, xs, ys, indexing="ij")
    F, X, Y = F.ravel(), X.ravel() + rng.uniform(-0.4, 0.4, X.size) * side_step, Y.ravel()
    gu, gw = (F + u0, X + w0) if long_u else (X + u0, F + w0)
    cu = np.clip(((gu - u0) / CELL).astype(int), 0, nu - 1)
    cw = np.clip(((gw - w0) / CELL).astype(int), 0, nw - 1)
    m = foot[cw, cu]
    gu, gw, Y, cu, cw = gu[m], gw[m], Y[m], cu[m], cw[m]
    n_side = len(gu)
    pos = np.zeros((n_side, 3))
    pos[:, ha], pos[:, hb], pos[:, k] = gu, gw, sgn * (a.floor + Y)
    sig = np.full((n_side, 3), 0.62 * side_step)
    sig[:, ha if long_u else hb] = 0.003  # the face is flat across the beam
    col = np.clip(deck_col[cw, cu] * 0.62 * rng.lognormal(0, 0.02, (n_side, 1)), 0, 1)
    parts.append((pos, sig, col, np.full(n_side, 0.995)))

    # strip lights: rows of 1.2 m tubes every 2 m across the room (one down the centre line), one every 2 m along it
    if long_u:
        mid = np.flatnonzero(foot.any(1)).mean() * CELL + w0
        across = lambda gu, gw: gw - mid
    else:
        mid = np.flatnonzero(foot.any(0)).mean() * CELL + u0
        across = lambda gu, gw: gu - mid

    def lights(gu, gw):
        row = np.abs(np.mod(across(gu, gw) + 1.0, 2.0) - 1.0) < 0.035
        return row & (np.mod(along(gu, gw) + 0.3, 2.0) < 1.2) & ~beam(gu, gw)

    n_light = sheet(0.012, lambda gu, gw: np.tile([1.0, 0.98, 0.93], (len(gu), 1)), height - 0.025, keep=lights,
                    opacity=1.0, flat=0.006, noise=0.0)
    n_beam += n_side

    pos, sig, col, opa = (np.concatenate(x) for x in zip(*parts))
    add = np.empty(len(pos), SPLAT_ROW)
    add["pos"], add["scale"] = pos, sig
    add["rgba"][:, :3] = np.round(col * 255)
    add["rgba"][:, 3] = np.round(opa * 255)
    add["rot"] = [255, 128, 128, 128]  # identity: the discs are axis-aligned already
    a.dst.write_bytes(v.tobytes() + add.tobytes())
    print(f"{a.src.name}: {len(v):,} + floor {n_floor:,} + ceiling {n_ceil:,} + beams {n_beam:,} + lights {n_light:,}"
          f" = {len(v) + len(add):,} Gaussians ({a.dst.stat().st_size / 1e6:.1f} MB) -> {a.dst}")
    print(f"footprint {foot.sum() * CELL * CELL:.0f} m^2 ({nu} x {nw} cells), room height {height:.2f}")


if __name__ == "__main__":
    main()

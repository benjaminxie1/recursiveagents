"""Build the plant's Gaussian splat: python -m scripts.build_splat

Writes, under assets/splats/:
  plant.splat                     the whole site as 3D Gaussians (antimatter15 .splat, 32 bytes each), y up, metres
  overlays/<ASSET>/<state>.splat  the hidden visual state painted onto one asset (dark display, oil pool, soiling, ...)
  scene.json                      asset anchors and one camera pose per viewpoint (contract C5 stations)

The layout follows the inspection wall's top-down map (viewer/wall.js, viewBox 320x250, 1 unit = 1 m):
map x -> world x, map y -> world z (south), so the wall's 2D map and the 3D splat agree.
A sourced capture (scripts/mesh_to_splat.py) can replace the generated PV field; the equipment stays ours.
Deterministic: the same seed writes the same bytes.
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "assets" / "splats"
ROW_Z = [30, 80, 130, 180]           # wall.js ROW_Y
BLOCK_X = (16, 126)
INV_X, BRK_X, BUS_X = 150, 186, 206
XFMR, POI, BESS_Z = (250, 105), (296, 105), 222


class Splat:
    """Accumulates Gaussians: position, per-axis scale, rotation (w, x, y, z), colour and opacity."""

    def __init__(self, seed):
        self.rng = np.random.default_rng(seed)
        self.parts = []

    def add(self, pos, scale, rgb, alpha=1.0, rot=None, noise=0.04):
        pos = np.atleast_2d(np.asarray(pos, float))
        n = len(pos)
        scale = np.broadcast_to(np.asarray(scale, float), (n, 3))
        lum = 1 + self.rng.normal(0, noise * 1.3, (n, 1))      # mostly brightness grain, a little hue drift
        rgb = np.broadcast_to(np.asarray(rgb, float), (n, 3)) * lum + self.rng.normal(0, noise * 0.25, (n, 3))
        alpha = np.broadcast_to(np.asarray(alpha, float), (n,))
        rot = np.broadcast_to(np.asarray([1, 0, 0, 0] if rot is None else rot, float), (n, 4))
        self.parts.append((pos, scale, np.clip(rgb, 0, 1), alpha, rot))

    def face(self, center, u, v, rgb, step, alpha=1.0, thick=0.01, jitter=0.25, noise=0.04, color_fn=None, overlap=0.75):
        """A flat rectangle of Gaussians: center + s*u + t*v, s, t in [-0.5, 0.5]; u and v are its edge vectors."""
        u, v, c = np.asarray(u, float), np.asarray(v, float), np.asarray(center, float)
        lu, lv = np.linalg.norm(u), np.linalg.norm(v)
        nu, nv = max(1, round(lu / step)), max(1, round(lv / step))
        s, t = np.meshgrid((np.arange(nu) + 0.5) / nu - 0.5, (np.arange(nv) + 0.5) / nv - 0.5, indexing="ij")
        s = s.ravel() + self.rng.uniform(-jitter, jitter, s.size) / nu
        t = t.ravel() + self.rng.uniform(-jitter, jitter, t.size) / nv
        pos = c + s[:, None] * u + t[:, None] * v
        col = np.broadcast_to(np.asarray(rgb, float), (len(pos), 3)) if color_fn is None else color_fn(s, t)
        self.add(pos, (overlap * lu / nu, overlap * lv / nv, thick), col, alpha, quat_from_axes(u / lu, v / lv), noise)

    def box(self, center, size, rgb, step=0.15, shade=True, skip=("bottom",), alpha=1.0, grime=0.25, cast=True):
        cx, cy, cz = center
        sx, sy, sz = size
        if cast and alpha > 0.5:
            caster(cx, cz, sx, sz, cy + sy / 2)
        faces = {  # name: (centre offset, u, v, brightness)
            "top": ((0, sy / 2, 0), (sx, 0, 0), (0, 0, sz), 1.12),
            "bottom": ((0, -sy / 2, 0), (sx, 0, 0), (0, 0, -sz), 0.5),
            "north": ((0, 0, -sz / 2), (-sx, 0, 0), (0, sy, 0), 0.82),
            "south": ((0, 0, sz / 2), (sx, 0, 0), (0, sy, 0), 1.0),
            "west": ((-sx / 2, 0, 0), (0, 0, sz), (0, sy, 0), 0.9),
            "east": ((sx / 2, 0, 0), (0, 0, -sz), (0, sy, 0), 0.94),
        }
        for name, (off, u, v, k) in faces.items():
            if name in skip:
                continue
            col = np.asarray(rgb, float) * (k if shade else 1)
            fn = None
            if grime and name != "top" and sy > 0.3:
                fn = weathered(col, grime, cy - sy / 2, sy, self.rng.integers(1 << 30))
            self.face((cx + off[0], cy + off[1], cz + off[2]), u, v, col, step, alpha, color_fn=fn)

    def cylinder(self, base, radius, height, rgb, step=0.08, axis="y"):
        n_a = max(8, round(2 * math.pi * radius / step))
        n_h = max(1, round(height / step))
        a, h = np.meshgrid(np.linspace(0, 2 * math.pi, n_a, endpoint=False), (np.arange(n_h) + 0.5) / n_h * height)
        a, h = a.ravel(), h.ravel()
        ring = np.stack([radius * np.cos(a), h, radius * np.sin(a)], 1)
        if axis == "x":
            ring = ring[:, [1, 0, 2]]
        elif axis == "z":
            ring = ring[:, [0, 2, 1]]
        shade = 0.75 + 0.3 * np.clip(np.cos(a - 2.2), 0, 1)
        self.add(np.asarray(base) + ring, (step * 0.7, step * 0.7, step * 0.7), np.asarray(rgb)[None] * shade[:, None])

    def rod(self, a, b, radius, rgb, step=None, alpha=1.0, noise=0.03):
        """A thin straight member from a to b (conduit, conductor, brace, wire) as Gaussians stretched along it."""
        a, b = np.asarray(a, float), np.asarray(b, float)
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 1e-6:
            return
        step = step or max(radius * 3, 0.05)
        n = max(1, round(L / step))
        u = d / L
        ref = np.array([0, 1.0, 0]) if abs(u[1]) < 0.9 else np.array([1.0, 0, 0])
        v = np.cross(u, ref)
        v /= np.linalg.norm(v)
        pos = a + ((np.arange(n) + 0.5) / n)[:, None] * d
        self.add(pos, (0.75 * L / n, radius, radius), rgb, alpha, quat_from_axes(u, v), noise)

    def wire(self, a, b, sag, radius, rgb, segs=24):
        """A conductor hanging between a and b with a parabolic sag (metres at mid-span)."""
        a, b = np.asarray(a, float), np.asarray(b, float)
        f = np.linspace(0, 1, segs + 1)
        pts = a + f[:, None] * (b - a) - np.outer(4 * sag * f * (1 - f), [0, 1, 0])
        for p, q in zip(pts[:-1], pts[1:]):
            self.rod(p, q, radius, rgb, step=0.25, noise=0.01)

    def write(self, path):
        pos, scale, rgb, alpha, rot = (np.concatenate(x) for x in zip(*self.parts))
        rot = rot / np.linalg.norm(rot, axis=1, keepdims=True)
        rec = np.zeros(len(pos), dtype=[("p", "<f4", 3), ("s", "<f4", 3), ("c", "u1", 4), ("r", "u1", 4)])
        rec["p"], rec["s"] = pos, scale
        rec["c"] = np.clip(np.concatenate([rgb, alpha[:, None]], 1) * 255, 0, 255).round()
        rec["r"] = np.clip(rot * 128 + 128, 0, 255).round()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rec.tobytes())
        return len(pos)


def quat_from_axes(u, v):
    """Rotation taking local x, y to u, v (z to their normal), as (w, x, y, z)."""
    n = np.cross(u, v)
    n /= np.linalg.norm(n)
    v = np.cross(n, u)
    m = np.stack([u, v, n], 1)
    w = math.sqrt(max(0.0, 1 + m[0, 0] + m[1, 1] + m[2, 2])) / 2
    if w > 1e-4:
        return np.array([w, (m[2, 1] - m[1, 2]) / (4 * w), (m[0, 2] - m[2, 0]) / (4 * w), (m[1, 0] - m[0, 1]) / (4 * w)])
    i = int(np.argmax(np.diag(m)))
    j, k = (i + 1) % 3, (i + 2) % 3
    q = np.zeros(4)
    q[i + 1] = math.sqrt(max(0.0, 1 + m[i, i] - m[j, j] - m[k, k])) / 2
    q[0] = (m[k, j] - m[j, k]) / (4 * q[i + 1])
    q[j + 1] = (m[j, i] + m[i, j]) / (4 * q[i + 1])
    q[k + 1] = (m[k, i] + m[i, k]) / (4 * q[i + 1])
    return q


# ---------- realism: weathering, value noise and soft sun shadows baked into the ground ----------
SUN = np.array([0.55, -0.42])        # ground offset of a shadow per metre of height: afternoon sun from the south-west
SHADOW_RES, SHADOW_BOX = 0.25, (-60.0, -70.0, 380.0, 320.0)     # metres per cell; x0, z0, x1, z1
CASTERS = []                          # (x, z, size x, size z, top height) footprints, filled while the site is built


def caster(x, z, sx, sz, top):
    CASTERS.append((x, z, sx, sz, top))


def _noise_grid(seed):
    return np.random.default_rng(seed).random((257, 257))


_GRIDS = {}


def vnoise(x, z, scale, seed=0):
    """Smooth value noise in [0, 1] over world x, z with feature size `scale` metres."""
    G = _GRIDS.setdefault(seed, _noise_grid(seed))
    fx, fz = np.asarray(x, float) / scale + 37.1 * seed, np.asarray(z, float) / scale + 11.3 * seed
    i0, j0 = np.floor(fx), np.floor(fz)
    u, v = fx - i0, fz - j0
    u, v = u * u * (3 - 2 * u), v * v * (3 - 2 * v)
    i, j = i0.astype(int) % 256, j0.astype(int) % 256
    return (G[i, j] * (1 - u) * (1 - v) + G[i + 1, j] * u * (1 - v) + G[i, j + 1] * (1 - u) * v + G[i + 1, j + 1] * u * v)


def fbm(x, z, scale, octaves=4, seed=0):
    out, amp, tot = 0.0, 1.0, 0.0
    for k in range(octaves):
        out = out + amp * vnoise(x, z, scale / 2 ** k, seed + k)
        tot += amp
        amp *= 0.5
    return out / tot


def weathered(col, amount, y0, h, seed):
    """color_fn for a wall: darker toward the ground (dust and contact shadow) plus faint vertical rain streaks."""
    rng = np.random.default_rng(seed)
    phase = rng.uniform(0, 100)

    def fn(s, t):
        up = np.clip((t + 0.5) * h / 0.6, 0, 1)                       # 0 at the ground, 1 above 60 cm
        streak = vnoise(s * 9 + phase, np.zeros_like(s) + phase, 1.0, 5) - 0.5
        k = (1 - amount * 0.7 * (1 - up)) * (1 + amount * 0.25 * streak) * (1 - amount * 0.12 * (0.5 - t))
        return np.clip(col[None, :] * k[:, None], 0, 1)
    return fn


def shadow_map():
    """Soft sun shadow (0 = lit, 1 = full shade) rasterised from CASTERS on a SHADOW_RES grid, as an image."""
    from PIL import Image, ImageDraw, ImageFilter
    x0, z0, x1, z1 = SHADOW_BOX
    W, H = int((x1 - x0) / SHADOW_RES), int((z1 - z0) / SHADOW_RES)
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    ao = Image.new("L", (W, H), 0)
    da = ImageDraw.Draw(ao)
    for x, z, sx, sz, top in CASTERS:
        if top <= 0.05:
            continue
        for f in np.linspace(0, 1, max(2, int(top * 3))):
            ox, oz = SUN * top * f
            d.rectangle([(x - sx / 2 + ox - x0) / SHADOW_RES, (z - sz / 2 + oz - z0) / SHADOW_RES,
                         (x + sx / 2 + ox - x0) / SHADOW_RES, (z + sz / 2 + oz - z0) / SHADOW_RES], fill=255)
        pad = 0.25 + 0.1 * min(top, 3)
        da.rectangle([(x - sx / 2 - pad - x0) / SHADOW_RES, (z - sz / 2 - pad - z0) / SHADOW_RES,
                      (x + sx / 2 + pad - x0) / SHADOW_RES, (z + sz / 2 + pad - z0) / SHADOW_RES], fill=150)
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    ao = ao.filter(ImageFilter.GaussianBlur(3))
    return np.maximum(np.asarray(im, float), np.asarray(ao, float)) / 255


def shade_at(sm, x, z):
    x0, z0, _, _ = SHADOW_BOX
    i = np.clip(((np.asarray(z) - z0) / SHADOW_RES).astype(int), 0, sm.shape[0] - 1)
    j = np.clip(((np.asarray(x) - x0) / SHADOW_RES).astype(int), 0, sm.shape[1] - 1)
    return sm[i, j]


FLAT = None


def ground_patch(g, x0, x1, z0, z1, step, color_at, y=0.0, alpha=1.0, thick=0.02, jitter=0.45, hole=None):
    """A horizontal patch of Gaussians whose colour is a function of world (x, z)."""
    global FLAT
    if FLAT is None:
        FLAT = quat_from_axes(np.array([1.0, 0, 0]), np.array([0, 0, -1.0]))
    nx, nz = max(1, round((x1 - x0) / step)), max(1, round((z1 - z0) / step))
    xs, zs = np.meshgrid(x0 + (np.arange(nx) + 0.5) * (x1 - x0) / nx, z0 + (np.arange(nz) + 0.5) * (z1 - z0) / nz, indexing="ij")
    xs = xs.ravel() + g.rng.uniform(-jitter, jitter, xs.size) * step
    zs = zs.ravel() + g.rng.uniform(-jitter, jitter, zs.size) * step
    if hole is not None:                                    # (x0, x1, z0, z1) left empty, where a finer patch lies
        keep = ~((xs > hole[0]) & (xs < hole[1]) & (zs > hole[2]) & (zs < hole[3]))
        xs, zs = xs[keep], zs[keep]
    pos = np.stack([xs, np.full(xs.size, y), zs], 1)
    sc = step * g.rng.uniform(0.6, 0.95, (xs.size, 1)) * np.array([[1, 1, 0]]) + np.array([[0, 0, thick]])
    g.add(pos, sc, color_at(xs, zs), alpha, FLAT, noise=0.02)


# ---------- photo textures: every surface below takes its colours from a real photo ----------
PHOTOS = REPO / "assets" / "photos"
SOURCES = OUT / "sources"
_TEX = {}


def tex(path, box=(0, 0, 1, 1), tile=(1, 1), gain=1.0, mirror=False, res=None):
    """color_fn for Splat.face: samples photo `path` cropped to `box` (fractions l, t, r, b), repeated `tile` times.
    `res` (w, h) first shrinks the crop, which low-passes textures sampled sparsely (ground, horizon)."""
    key = (path, box if res else None, res)
    if key not in _TEX:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        if res:
            W, H = im.size
            im = im.crop((int(box[0] * W), int(box[1] * H), int(box[2] * W), int(box[3] * H))).resize(res, Image.LANCZOS)
        _TEX[key] = np.asarray(im, float) / 255
    img = _TEX[key]
    h, w = img.shape[:2]
    l, t0, r, b = (0, 0, 1, 1) if res else box

    def fn(s, t):
        fu, fv = (s + 0.5) * tile[0], (0.5 - t) * tile[1]          # t = +0.5 is the top edge of the face
        if mirror:
            fu = np.where(np.floor(fu) % 2 == 1, 1 - (fu % 1), fu % 1)
        else:
            fu = fu % 1
        fv = fv % 1
        px = np.clip(((l + fu * (r - l)) * (w - 1)).astype(int), 0, w - 1)
        py = np.clip(((t0 + fv * (b - t0)) * (h - 1)).astype(int), 0, h - 1)
        return np.clip(img[py, px] * gain, 0, 1)
    return fn


CABINET = PHOTOS / "INV-3" / "healthy" / "01_ai_generated.jpg"      # the same unit the INV cameras photograph
CABINET_BOX = (0.25, 0.02, 0.755, 0.92)
PADMOUNT = PHOTOS / "XFMR-1" / "normal" / "01_pad_mounted_transformer_photo_2024.jpg"
PADMOUNT_BOX = (0.143, 0.14, 0.9, 0.76)
GRAVEL = PHOTOS / "XFMR-1" / "normal" / "03_pse_minipad_transformer_in_redondo_wa.jpg"
GRAVEL_BOX = (0.0, 0.45, 0.2, 1.0)
PANELS = SOURCES / "waipio.jpg"
PANELS_BOX = (0.0, 0.2, 0.3, 0.36)
HORIZON = SOURCES / "topaz_maint.jpg"
SOIL_BOX = (0.0, 0.86, 0.3, 1.0)
COMBINER = SOURCES / "combiner.jpg"
COMBINER_BOX = (0.16, 0.18, 0.36, 0.43)
SWITCHGEAR = SOURCES / "switchgear.jpg"             # Siemens MV panel front
CONTAINER = SOURCES / "container.jpg"               # BESS container long side
METAL_WALL = SOURCES / "metal_building.jpg"         # corrugated steel wall


# ---------- layout: where every asset sits (world x, y, z) ----------
def cb_x(n):
    return BLOCK_X[0] + (n - 0.5) * (BLOCK_X[1] - BLOCK_X[0]) / 6


INV_SIZE = (1.4, 2.5, 2.3)  # x (front to back), y, z: a two-door cabinet whose doors face west, like the INV-3 photos
ANCHORS = {f"INV-{i + 1}": (INV_X, 1.45, z) for i, z in enumerate(ROW_Z)}
ANCHORS |= {f"52-I{i + 1}": (BRK_X, 1.0, z) for i, z in enumerate(ROW_Z)}
ANCHORS |= {f"B{i + 1}": (sum(BLOCK_X) / 2, 1.2, z) for i, z in enumerate(ROW_Z)}
ANCHORS |= {f"CB-{b + 1}.{n}": (cb_x(n), 1.2, z - 14) for b, z in enumerate(ROW_Z) for n in range(1, 7)}
ANCHORS |= {"XFMR-1": (XFMR[0], 1.1, XFMR[1]), "52-M": (XFMR[0] - 14, 1.0, XFMR[1]), "POI-1": (POI[0], 1.2, POI[1]),
            "BESS-1": (INV_X, 1.3, BESS_Z), "52-B1": (BRK_X, 1.0, BESS_Z), "MET-1": (300, 2.0, 232)}
PANEL_ROWS = (-22.0, -17.0, -11.0, -6.0, -1.0, 4.0, 9.0, 14.0, 19.0)   # a lane at -14 for the combiner boxes
TILT = math.radians(25)


ROADS = [  # (x0, x1, z0, z1): compacted gravel access roads, wheel ruts along their long axis
    (133.0, 139.0, -4.0, 246.0),        # spine between the arrays and the inverter row
    (139.0, 300.0, 116.0, 121.0),       # spine to the substation gate
    (10.0, 318.0, 242.5, 247.5),        # perimeter road inside the south fence
    (148.0, 172.0, 247.5, 262.0),       # site entrance through the gate
]


def in_road(x, z):
    m = np.zeros(np.shape(x), bool)
    for x0, x1, z0, z1 in ROADS:
        m |= (x >= x0) & (x <= x1) & (z >= z0) & (z <= z1)
    return m


def ground(g):
    sm = shadow_map()
    soil = tex(HORIZON, SOIL_BOX, (1, 1), res=(1, 1))(np.zeros(1), np.zeros(1))[0]
    rock = tex(GRAVEL, GRAVEL_BOX, (1, 1), res=(1, 1), gain=1.15)(np.zeros(1), np.zeros(1))[0]
    pad = 0.55 * rock + 0.45 * soil

    def dirt(x, z):
        n = fbm(x, z, 40, 4, 1)
        fine = vnoise(x, z, 0.9, 9)
        col = soil[None] * (0.82 + 0.38 * n[:, None]) * (0.94 + 0.12 * fine[:, None])
        dry = fbm(x, z, 14, 3, 21)[:, None]                             # patches of dry grass
        col = col * (1 - 0.35 * np.clip(dry - 0.55, 0, 1)) + np.array([0.48, 0.44, 0.27]) * 0.35 * np.clip(dry - 0.55, 0, 1)
        return np.clip(col * (1 - 0.5 * shade_at(sm, x, z))[:, None], 0, 1)

    def site_ground(x, z):
        n = fbm(x, z, 25, 4, 3)
        speck = g.rng.random(x.size)                                    # individual stones
        col = pad[None] * (0.86 + 0.26 * n[:, None]) * (0.9 + 0.2 * speck[:, None])
        road = in_road(x, z)
        if road.any():
            rc = np.array([0.6, 0.56, 0.5]) * (0.9 + 0.2 * fbm(x[road], z[road], 6, 3, 4))[:, None]
            col[road] = rc
            for x0, x1, z0, z1 in ROADS:                                # two wheel ruts per road
                m = road & (x >= x0) & (x <= x1) & (z >= z0) & (z <= z1)
                along_x = (x1 - x0) > (z1 - z0)
                c = (z0 + z1) / 2 if along_x else (x0 + x1) / 2
                off = np.abs((z if along_x else x)[m] - c)
                rut = np.exp(-((off - 0.95) / 0.28) ** 2)
                col[m] *= (1 - 0.22 * rut)[:, None]
        col *= (1 - 0.52 * shade_at(sm, x, z))[:, None]
        return np.clip(col, 0, 1)

    # far plain out to the horizon ring, then the near soil, then the graded site inside the fence
    ground_patch(g, -440, 760, -475, 725, 6.0, dirt, y=-0.05, thick=0.02, jitter=0.2, hole=(-56, 376, -66, 316))
    for x0, x1, z0, z1 in ((-60, 380, -70, -8), (-60, 380, 250, 320), (-60, 2, -8, 250), (318, 380, -8, 250)):
        ground_patch(g, x0, x1, z0, z1, 1.1, dirt, y=-0.02, thick=0.04)
    ground_patch(g, 2, 318, -8, 250, 0.62, site_ground, y=0.0, thick=0.02)
    ground_patch(g, 148, 172, 250, 262, 0.42, site_ground, y=0.005, thick=0.02)
    # cable trenches: fresh backfill strips from each block to its inverter and along the collector bus
    for z in ROW_Z:
        ground_patch(g, BLOCK_X[1], INV_X - 1.5, z + 3.2, z + 3.8, 0.2, lambda x, z: np.tile([0.7, 0.67, 0.62], (x.size, 1)), y=0.012)
        ground_patch(g, INV_X + 1.5, BUS_X, z - 0.3, z + 0.3, 0.2, lambda x, z: np.tile([0.7, 0.67, 0.62], (x.size, 1)), y=0.012)
    ground_patch(g, BUS_X - 0.4, BUS_X + 0.4, 26, 226, 0.2, lambda x, z: np.tile([0.7, 0.67, 0.62], (x.size, 1)), y=0.012)
    # tufts of dry grass and weeds, thick outside the fence, sparse on the pad
    rng = g.rng
    for (x0, x1, z0, z1, n) in ((-60, 380, -70, -10, 3000), (-60, 380, 252, 320, 3000), (-60, 0, -10, 252, 1000),
                                (320, 380, -10, 252, 1000), (2, 318, -8, 250, 3000)):
        x, z = rng.uniform(x0, x1, n), rng.uniform(z0, z1, n)
        keep = ~in_road(x, z)
        x, z = x[keep], z[keep]
        k = rng.integers(5, 10, x.size)
        px = np.repeat(x, k) + rng.normal(0, 0.18, k.sum())
        pz = np.repeat(z, k) + rng.normal(0, 0.18, k.sum())
        py = rng.uniform(0.02, 0.32, k.sum()) ** 1.4
        col = np.array([0.52, 0.47, 0.3])[None] * rng.uniform(0.7, 1.15, (k.sum(), 1)) + np.array([-0.08, 0.02, -0.04]) * rng.random((k.sum(), 1))
        g.add(np.stack([px, py, pz], 1), (0.03, 0.12, 0.03), col, 0.85, noise=0.05)
    # horizon: the hills and sky behind the Topaz Solar Farm, on a ring of Gaussians 600 m out
    R, H, n_a, n_h = 600.0, 190.0, 1500, 60
    a, y = np.meshgrid(np.linspace(0, 2 * math.pi, n_a, endpoint=False), (np.arange(n_h) + 0.5) / n_h)
    a, y = a.ravel(), y.ravel()
    col = tex(HORIZON, (0.0, 0.0, 1.0, 0.36), (6, 1), mirror=True, res=(480, 70))(a / (2 * math.pi) - 0.5, y - 0.5)
    pos = np.stack([160 + R * np.cos(a), y * H - 20, 125 + R * np.sin(a)], 1)
    rot = np.array([quat_from_axes(np.array([-math.sin(t), 0, math.cos(t)]), np.array([0, 1.0, 0])) for t in a])
    g.add(pos, (R * 2 * math.pi / n_a * 0.8, H / n_h * 0.8, 0.5), col, 1.0, rot=rot, noise=0.0)


def pv_block(g, b):
    z0 = ROW_Z[b]
    x0, x1 = BLOCK_X[0] + 2, BLOCK_X[1] - 2
    length = x1 - x0
    depth = 2.3
    u = np.array([length, 0, 0])
    v = np.array([0, depth * math.sin(TILT), -depth * math.cos(TILT)])  # rises toward the north, faces south

    def cells(s, t):
        cx = (s + 0.5) * length / 1.05            # module columns
        cy = (t + 0.5) * 2                        # two modules up
        line = (np.abs(cx - np.round(cx)) < 0.035) | (np.abs(cy - np.round(cy)) < 0.03)
        grad = 0.75 + 0.45 * (t + 0.5)            # sky reflection brightens toward the top edge
        base = np.stack([0.07 * grad, 0.11 * grad, 0.23 * grad], 1)
        base *= (0.85 + 0.3 * vnoise(np.floor(cx) * 3.7 + 0.5, np.floor(cy) * 5.3 + b * 17 + 0.5, 1.0, 12))[:, None]   # module to module
        base[line] = (0.62, 0.64, 0.68)
        return base
    for dz in PANEL_ROWS:
        c = np.array([(x0 + x1) / 2, 1.3, z0 + dz])
        g.face(c, u, v, (0.1, 0.14, 0.28), 0.2, thick=0.015, jitter=0.15, noise=0.02,
               color_fn=lambda s, t: 0.4 * tex(PANELS, PANELS_BOX, (length / 9, 1), mirror=True)(s, t) + 0.6 * cells(s, t))
        g.face(c - np.array([0, 0.18, 0]), u * 0.98, v * 0.9, (0.3, 0.3, 0.32), 0.6, thick=0.015, noise=0.02)  # rear sheet
        for px in np.arange(x0 + 2, x1, 6):  # posts and torque tube
            g.box((px, 0.6, z0 + dz + 0.4), (0.1, 1.2, 0.1), (0.62, 0.62, 0.64), step=0.2, shade=True)
            g.box((px, 0.5, z0 + dz - 0.4), (0.1, 1.0, 0.1), (0.62, 0.62, 0.64), step=0.2, shade=True)
        g.cylinder((x0, 1.2, z0 + dz), 0.06, length, (0.6, 0.6, 0.62), step=0.35, axis="x")
        caster((x0 + x1) / 2, z0 + dz, length, depth * math.cos(TILT) * 0.9, 1.45)


def combiner(g, b, n):
    x, _, z = ANCHORS[f"CB-{b + 1}.{n}"]
    g.box((x, 0.55, z + 0.2), (0.12, 1.1, 0.12), (0.55, 0.55, 0.55), step=0.05)
    g.box((x, 1.25, z), (0.9, 0.75, 0.3), (0.82, 0.83, 0.82), step=0.06)
    g.face((x, 1.25, z - 0.153), (-0.86, 0, 0), (0, 0.71, 0), (0.8, 0.8, 0.8), 0.025, thick=0.005,
           color_fn=tex(COMBINER, COMBINER_BOX), overlap=1.3)                                                    # north face, from the photo
    for k in range(4):
        g.cylinder((x - 0.3 + 0.2 * k, 0.65, z), 0.025, 0.25, (0.08, 0.08, 0.08), step=0.025)       # DC glands underneath


def inverter(g, i):
    x, _, z = ANCHORS[f"INV-{i + 1}"]
    sx, sy, sz = INV_SIZE
    g.box((x, 0.1, z), (2.6, 0.2, 3.4), (0.7, 0.69, 0.66), step=0.12)              # concrete pad
    g.box((x, 0.2 + sy / 2, z), INV_SIZE, (0.78, 0.8, 0.8), step=0.05, skip=("bottom", "west"))
    # the doors, louvres and touchscreen come straight from the INV-3 cabinet photo
    g.face((x - sx / 2, 0.2 + sy / 2, z), (0, 0, sz), (0, sy, 0), (0.8, 0.8, 0.8), 0.02, thick=0.004, jitter=0.1, noise=0.01,
           color_fn=tex(CABINET, CABINET_BOX), overlap=1.3)
    g.face((x + sx / 2 + 0.004, 0.2 + sy / 2, z), (0, 0, -sz), (0, sy, 0), (0.8, 0.8, 0.8), 0.03, thick=0.004, noise=0.01,
           color_fn=tex(CABINET, CABINET_BOX, gain=0.95), overlap=1.3)                           # the rear doors look the same
    g.box((x, 0.2 + sy + 0.08, z), (sx + 0.1, 0.16, sz + 0.1), (0.7, 0.72, 0.72), step=0.05)  # roof cap and exhaust hoods


INV_SCREEN = (0.755, 0.30, 0.22, 0.13)   # screen centre (s from the left, t from the top) and size, as fractions of the door face
INV_LOUVRES = ((0.25, 0.745, 0.33, 0.24), (0.74, 0.745, 0.33, 0.24))


def on_inv_face(x, z, fs, ft):
    """World point on an inverter's west (door) face, from fractions of the face (left to right, top to bottom)."""
    sx, sy, sz = INV_SIZE
    return np.array([x - sx / 2 - 0.006, 0.2 + sy * (1 - ft), z - sz / 2 + sz * fs])


def breaker(g, name):
    x, _, z = ANCHORS[name]                                    # its pad comes with the switchgear lineup around it
    g.box((x, 1.1, z), (1.2, 1.8, 1.0), (0.72, 0.74, 0.74), step=0.06)
    g.face((x, 1.25, z - 0.506), (-0.3, 0, 0), (0, 0.5, 0), (0.3, 0.3, 0.3), 0.02, thick=0.004)  # handle well, north face


XFMR_SIZE = (2.2, 1.9, 2.0)


def transformer(g):
    """A pad-mounted transformer, skinned with the XFMR-1 camera photo, inside its own fenced yard."""
    x, _, z = ANCHORS["XFMR-1"]
    sx, sy, sz = XFMR_SIZE
    g.box((x, 0.1, z), (3.6, 0.2, 3.4), (0.72, 0.71, 0.68), step=0.1)
    g.box((x, 0.2 + sy / 2, z), XFMR_SIZE, (0.24, 0.33, 0.27), step=0.05, skip=("bottom", "west", "south"))
    skin = tex(PADMOUNT, PADMOUNT_BOX)
    g.face((x - sx / 2, 0.2 + sy / 2, z), (0, 0, sz), (0, sy, 0), (0.3, 0.38, 0.3), 0.02, thick=0.004, noise=0.01, color_fn=skin, overlap=1.3)
    g.face((x, 0.2 + sy / 2, z + sz / 2), (sx, 0, 0), (0, sy, 0), (0.3, 0.38, 0.3), 0.02, thick=0.004, noise=0.01, color_fn=skin, overlap=1.3)
    fence(g, x - 9, x + 9, z - 9, z + 9, h=2.4, gate=(z + 9, x - 1.5, x + 1.5))


def fence(g, x0, x1, z0, z1, h=2.2, gate=None):
    mesh = (0.62, 0.64, 0.66)
    for (a, b) in (((x0, z0), (x1, z0)), ((x1, z0), (x1, z1)), ((x1, z1), (x0, z1)), ((x0, z1), (x0, z0))):
        a, b = np.array(a, float), np.array(b, float)
        L = np.linalg.norm(b - a)
        for f in np.arange(0, 1.0001, 3 / L):
            p = a + (b - a) * f
            g.cylinder((p[0], 0, p[1]), 0.04, h, (0.5, 0.5, 0.52), step=0.12)
        mid = (a + b) / 2
        u = np.array([b[0] - a[0], 0, b[1] - a[1]])
        if gate and abs(a[1] - gate[0]) < 1e-6 and abs(b[1] - gate[0]) < 1e-6:
            continue
        g.face((mid[0], h / 2, mid[1]), u, (0, h, 0), mesh, 0.3, alpha=0.13, thick=0.01, noise=0.04)
        g.face((mid[0], h, mid[1]), u, (0, 0.06, 0), (0.5, 0.5, 0.52), 0.08, thick=0.02)  # top rail
        for k in range(3):                                                              # barbed wire strands above
            g.rod((a[0], h + 0.12 + 0.12 * k, a[1]), (b[0], h + 0.12 + 0.12 * k, b[1]), 0.008, (0.4, 0.4, 0.42), step=0.3, alpha=0.8)


# ---------- balance of plant: the equipment around the tagged assets, so the yard reads like a real substation and skids ----------
STEEL, GALV, CONCRETE = (0.55, 0.56, 0.57), (0.66, 0.67, 0.68), (0.72, 0.71, 0.68)
MV_GREEN, WARN = (0.29, 0.38, 0.31), (0.95, 0.75, 0.1)


def label(g, center, normal_axis, w, h, rgb, sign=1):
    """A small plate (nameplate, arc-flash label) on a wall facing +/- x or z."""
    c = np.asarray(center, float)
    if normal_axis == "x":
        g.face(c, (0, 0, -w * sign), (0, h, 0), rgb, min(w, h) / 3, thick=0.003, noise=0.01, overlap=1.2)
    else:
        g.face(c, (w * sign, 0, 0), (0, h, 0), rgb, min(w, h) / 3, thick=0.003, noise=0.01, overlap=1.2)


def bollard(g, x, z):
    g.cylinder((x, 0, z), 0.08, 1.1, WARN, step=0.05)
    caster(x, z, 0.16, 0.16, 1.1)


def inverter_skid(g, i):
    """The MV skid south of each inverter: step-up transformer with radiator fins, aux panel, conduits, bollards."""
    x, _, z = ANCHORS[f"INV-{i + 1}"]
    zc = z + 5.6
    g.box((x + 0.3, 0.12, zc), (4.2, 0.24, 3.6), CONCRETE, step=0.12, grime=0.1)
    # pad-mount MV transformer, doors facing west like the cabinet
    tx, tz = x - 0.1, zc
    g.box((tx, 0.24 + 0.9, tz), (1.8, 1.8, 2.0), MV_GREEN, step=0.05, grime=0.35)
    g.box((tx + 0.15, 0.24 + 1.83, tz), (1.6, 0.06, 1.9), [c * 1.1 for c in MV_GREEN], step=0.06, cast=False)
    for k in range(9):                                                              # radiator fins on the east side
        fz = tz - 0.8 + 0.2 * k
        g.box((tx + 1.05, 0.24 + 0.85, fz), (0.3, 1.3, 0.035), [c * 0.85 for c in MV_GREEN], step=0.04, grime=0.2, cast=False)
    caster(tx + 1.05, tz, 0.3, 1.8, 1.3)
    g.face((tx - 0.904, 0.24 + 0.9, tz), (0, 0, 0.02), (0, 1.6, 0), (0.12, 0.14, 0.12), 0.02, thick=0.003)   # door seam
    label(g, (tx - 0.906, 1.55, tz - 0.45), "x", 0.32, 0.22, WARN, sign=-1)
    label(g, (tx - 0.906, 1.55, tz + 0.45), "x", 0.25, 0.14, (0.92, 0.92, 0.9), sign=-1)
    # auxiliary / SCADA panel on a unistrut frame
    ax, az = x + 1.8, zc - 1.2
    for dz in (-0.35, 0.35):
        g.box((ax, 0.85, az + dz), (0.06, 1.7, 0.06), GALV, step=0.03, grime=0)
    g.box((ax, 1.35, az), (0.3, 0.7, 0.6), (0.85, 0.86, 0.86), step=0.04)
    label(g, (ax - 0.152, 1.5, az), "x", 0.2, 0.08, (0.15, 0.4, 0.75), sign=-1)
    # conduits from the inverter to the transformer and down to the trench
    for k in range(4):
        cz = z + 1.25 + 0.15 * k
        g.rod((x - 0.5 + 0.12 * k, 0.32, cz), (x - 0.5 + 0.12 * k, 0.32, zc - 1.0), 0.045, (0.35, 0.36, 0.36))
    for k in range(3):
        g.rod((x + 0.75 + 0.15 * k, 0.24, z - 1.2), (x + 0.75 + 0.15 * k, 1.0, z - 1.2), 0.04, (0.3, 0.3, 0.3))
    for bx, bz in ((x - 1.9, zc - 2.0), (x - 1.9, zc + 2.0), (x + 2.6, zc + 2.0), (x - 1.9, z - 1.8)):
        bollard(g, bx, bz)


def switchgear(g, name):
    """Turn a lone breaker into a three-section outdoor lineup; the middle section is the tagged breaker itself."""
    x, _, z = ANCHORS[name]
    g.box((x, 0.1, z), (4.4, 0.2, 2.4), CONCRETE, step=0.15, grime=0.1)
    for dx in (-1.25, 1.25):
        g.box((x + dx, 1.1, z), (1.2, 1.8, 1.0), (0.7, 0.72, 0.72), step=0.05)
        g.face((x + dx, 1.1, z - 0.504), (-1.16, 0, 0), (0, 1.76, 0), (0.7, 0.72, 0.72), 0.02, thick=0.003, noise=0.01,
               color_fn=tex(SWITCHGEAR, gain=1.05), overlap=1.3)                                     # panel front, from the photo
        label(g, (x + dx + 0.25, 1.35, z - 0.506), "z", 0.22, 0.14, WARN, sign=-1)
    g.box((x, 2.05, z - 0.05), (3.8, 0.1, 1.2), (0.62, 0.64, 0.64), step=0.06, cast=False)       # drip roof over the lineup
    caster(x, z, 3.8, 1.2, 2.1)
    label(g, (x - 0.2, 1.75, z - 0.506), "z", 0.3, 0.1, (0.95, 0.95, 0.93), sign=-1)             # breaker nameplate
    for k in range(3):                                                                            # cable risers out of the trench
        g.rod((x + 1.25 + 0.2 * (k - 1), 0.2, z + 0.55), (x + 1.25 + 0.2 * (k - 1), 2.0, z + 0.55), 0.05, (0.3, 0.3, 0.3))


def lattice(g, x, z, h, w=0.8):
    """A galvanised lattice column: four angle legs and zig-zag bracing."""
    for dx in (-w / 2, w / 2):
        for dz in (-w / 2, w / 2):
            g.rod((x + dx, 0, z + dz), (x + dx * 0.6, h, z + dz * 0.6), 0.05, GALV)
    n = int(h / 0.8)
    for k in range(n):
        y0, y1 = h * k / n, h * (k + 1) / n
        s0, s1 = 1 - 0.4 * k / n, 1 - 0.4 * (k + 1) / n
        for (ax, az), (bx, bz) in ((((-1, -1), (1, -1))), (((1, -1), (1, 1))), (((1, 1), (-1, 1))), (((-1, 1), (-1, -1)))):
            g.rod((x + ax * w / 2 * s0, y0, z + az * w / 2 * s0), (x + bx * w / 2 * s1, y1, z + bz * w / 2 * s1), 0.025, GALV)
    caster(x, z, w, w, h * 0.5)


def insulator(g, top, length=0.9, axis=(0, -1, 0)):
    """A string of porcelain discs hanging from `top`; returns the attachment point at its end."""
    top, d = np.asarray(top, float), np.asarray(axis, float)
    n = int(length / 0.15)
    for k in range(n):
        c = top + d * (0.075 + 0.15 * k)
        g.cylinder(c - np.array([0, 0.02, 0]), 0.13, 0.04, (0.55, 0.38, 0.28), step=0.04)
    return top + d * length


def substation(g):
    """The plant's collector substation around XFMR-1: dead-end structure, bus, arresters, line to the POI and the grid."""
    x, _, z = ANCHORS["XFMR-1"]
    # bright substation rock inside the yard
    ground_patch(g, x - 8.8, x + 8.8, z - 8.8, z + 8.8, 0.25,
                 lambda px, pz: np.clip(np.array([0.7, 0.69, 0.66])[None] * (0.82 + 0.3 * g.rng.random((px.size, 1))), 0, 1), y=0.008)
    # dead-end H-frame on the yard's east side
    sx = x + 6.5
    for cz in (z - 4.5, z + 4.5):
        lattice(g, sx, cz, 10.0)
    g.box((sx, 9.6, z), (0.5, 0.5, 9.8), GALV, step=0.08, grime=0, cast=False)
    caster(sx, z, 0.5, 9.8, 9.6)
    phases = [z - 2.5, z, z + 2.5]
    ends = [insulator(g, (sx, 9.35, pz), 1.0) for pz in phases]
    # riser pole beside the transformer with cable terminations and arresters
    rx, rz = x + 3.2, z - 3.6
    g.cylinder((rx, 0, rz), 0.17, 7.5, (0.45, 0.34, 0.24), step=0.08)
    caster(rx, rz, 0.34, 0.34, 7.5)
    g.box((rx, 7.2, rz), (0.15, 0.15, 3.0), (0.45, 0.34, 0.24), step=0.05, grime=0, cast=False)
    for k, pz in enumerate(phases):
        tz = rz - 1.2 + 1.2 * k
        g.cylinder((rx + 0.15, 7.3, tz), 0.07, 0.6, (0.6, 0.45, 0.35), step=0.03)          # termination
        g.cylinder((rx - 0.15, 6.6, tz), 0.05, 0.5, (0.5, 0.5, 0.52), step=0.03)            # arrester
        g.wire((rx + 0.15, 7.9, tz), ends[k], 0.35, 0.02, (0.25, 0.25, 0.27))
        g.rod((rx + 0.15, 7.3, tz), (rx + 0.15, 0.2, tz + 0.1), 0.04, (0.12, 0.12, 0.12))   # cable down the pole
    g.rod((x + 1.1, 1.2, z - 0.6), (rx, 0.25, rz), 0.06, (0.12, 0.12, 0.12))
    # lightning masts at two corners
    for mx, mz in ((x - 8, z - 8), (x + 8, z + 8)):
        g.rod((mx, 0, mz), (mx, 14, mz), 0.12, GALV, step=0.2)
        caster(mx, mz, 0.25, 0.25, 14)
    # shield wire and conductors east to the POI pole and on to the utility line
    px, _, pz = ANCHORS["POI-1"]
    pole_x = px + 1.2
    arms = [insulator(g, (pole_x, 8.6, pz + d), 0.6, (0, 1, 0)) for d in (-1.1, 0, 1.1)]
    for k in range(3):
        g.wire(ends[k], arms[k], 0.9, 0.022, (0.25, 0.25, 0.27))
    prev = arms
    for k, lx in enumerate((340, 400, 470, 560)):                                           # off-site poles leaving to the grid
        lz = pz + 6 * k
        g.cylinder((lx, 0, lz), 0.18, 10.5, (0.42, 0.32, 0.22), step=0.12)
        g.box((lx, 10.0, lz), (0.15, 0.18, 2.6), (0.42, 0.32, 0.22), step=0.08, grime=0)
        caster(lx, lz, 0.36, 0.36, 10.5)
        tops = [np.array([lx, 10.25, lz + d]) for d in (-1.1, 0, 1.1)]
        for a, b in zip(prev, tops):
            g.wire(a, b, 1.2, 0.025, (0.22, 0.22, 0.24), segs=30)
        prev = tops


def control_house(g):
    """O&M building, parking and a pickup inside the gate."""
    bx, bz = 112.0, 233.0
    g.box((bx, 0.1, bz), (13, 0.2, 6.5), CONCRETE, step=0.15, grime=0.1)
    g.box((bx, 0.2 + 1.5, bz), (12, 3.0, 5.0), (0.86, 0.85, 0.8), step=0.08, grime=0.3)
    g.face((bx, 1.7, bz + 2.505), (12, 0, 0), (0, 3.0, 0), (0.86, 0.85, 0.8), 0.06, thick=0.003, noise=0.01,
           color_fn=tex(METAL_WALL, tile=(4, 1), gain=1.1), overlap=1.3)                         # corrugated steel south wall
    g.box((bx, 3.3, bz), (12.6, 0.2, 5.6), (0.5, 0.52, 0.5), step=0.1, cast=False)
    caster(bx, bz, 12.6, 5.6, 3.4)
    g.face((bx - 3.0, 1.25, bz + 2.51), (1.0, 0, 0), (0, 2.1, 0), (0.45, 0.5, 0.55), 0.03, thick=0.004)    # door
    for wx in (bx + 0.5, bx + 3.5):
        g.face((wx, 1.9, bz + 2.51), (1.4, 0, 0), (0, 0.9, 0), (0.16, 0.22, 0.28), 0.03, thick=0.004)      # windows
    g.box((bx + 6.6, 0.7, bz + 0.8), (1.0, 1.0, 1.2), (0.8, 0.8, 0.78), step=0.05)                         # AC condenser
    label(g, (bx - 1.6, 2.4, bz + 2.512), "z", 1.2, 0.35, (0.12, 0.25, 0.45))
    pickup(g, 126.0, 236.0, (0.82, 0.83, 0.84))
    pickup(g, 121.0, 238.5, (0.18, 0.22, 0.3))


def pickup(g, x, z, rgb):
    """A pickup truck facing east."""
    g.box((x, 0.75, z), (5.4, 0.7, 1.9), rgb, step=0.06, grime=0.15)
    g.box((x + 0.4, 1.45, z), (2.2, 0.75, 1.8), rgb, step=0.06, grime=0.05)
    g.face((x + 1.53, 1.45, z), (0, 0, 1.6), (-0.25, 0.6, 0), (0.12, 0.15, 0.18), 0.03, thick=0.003)       # windscreen
    for sz in (-1, 1):
        g.face((x + 0.4, 1.5, z + sz * 0.905), (1.9 * sz, 0, 0), (0, 0.5, 0), (0.12, 0.15, 0.18), 0.03, thick=0.003)
    g.box((x - 1.8, 1.25, z), (1.7, 0.3, 1.8), [c * 0.8 for c in rgb], step=0.06, skip=("bottom", "top"), cast=False)
    for wx in (x - 1.7, x + 1.75):
        for sz in (-0.85, 0.85):
            g.cylinder((wx, 0.4, z + sz - 0.13), 0.4, 0.26, (0.08, 0.08, 0.08), step=0.05, axis="z")


def poles(g):
    """Light and security-camera poles along the roads and fence corners."""
    for px, pz in ((140.5, 20), (140.5, 95), (140.5, 170), (140.5, 236), (210, 123), (300, 240), (10, 240), (10, 0), (310, 0)):
        g.rod((px, 0, pz), (px, 7.5, pz), 0.07, GALV, step=0.15)
        g.rod((px, 7.5, pz), (px + 0.9, 7.6, pz), 0.04, GALV)
        g.box((px + 1.0, 7.5, pz), (0.45, 0.12, 0.25), (0.3, 0.3, 0.3), step=0.04, grime=0, cast=False)
        g.box((px + 0.15, 6.4, pz), (0.3, 0.2, 0.18), (0.92, 0.92, 0.9), step=0.03, grime=0, cast=False)   # camera housing
        caster(px, pz, 0.14, 0.14, 7.5)


def site(g):
    for i in range(4):
        pv_block(g, i)
        inverter(g, i)
        breaker(g, f"52-I{i + 1}")
        switchgear(g, f"52-I{i + 1}")
        inverter_skid(g, i)
        for n in range(1, 7):
            combiner(g, i, n)
    breaker(g, "52-M")
    switchgear(g, "52-M")
    breaker(g, "52-B1")
    switchgear(g, "52-B1")
    transformer(g)
    substation(g)
    control_house(g)
    poles(g)
    x, _, z = ANCHORS["POI-1"]
    g.box((x, 0.1, z), (3, 0.2, 3), (0.7, 0.69, 0.66), step=0.15)
    g.box((x, 1.1, z), (1.0, 1.8, 0.6), (0.75, 0.77, 0.76), step=0.06)
    g.cylinder((x + 1.2, 0, z), 0.15, 9.0, (0.45, 0.35, 0.25), step=0.08)        # utility pole
    g.box((x + 1.2, 8.6, z), (0.15, 0.15, 2.4), (0.45, 0.35, 0.25), step=0.06)
    x, _, z = ANCHORS["BESS-1"]
    for dz in (-1.6, 1.6):                                                          # two battery containers
        g.box((x - 2, 1.4, z + dz), (12.0, 2.6, 2.4), (0.9, 0.9, 0.88), step=0.12)
        for sgn in (-1, 1):                                                         # long sides, from the BESS photo
            g.face((x - 2, 1.4, z + dz + sgn * 1.206), (11.9 * sgn, 0, 0), (0, 2.55, 0), (0.9, 0.9, 0.88), 0.06, thick=0.004,
                   noise=0.01, color_fn=tex(CONTAINER, tile=(2, 1)), overlap=1.3)
        g.box((x + 4.4, 1.6, z + dz), (0.8, 1.4, 1.6), (0.82, 0.83, 0.82), step=0.05)                           # HVAC unit on the end
    x, _, z = ANCHORS["MET-1"]
    g.cylinder((x, 0, z), 0.05, 3.0, (0.6, 0.6, 0.6), step=0.04)
    g.face((x, 2.8, z), (0.6, 0, 0), (0, 0.3, -0.3), (0.1, 0.13, 0.25), 0.04, thick=0.005)
    fence(g, 2, 318, -8, 250, h=2.4, gate=(250, 150, 170))
    rng = np.random.default_rng(7)
    for _ in range(46):                                                             # trees outside the fence
        side = rng.integers(4)
        x = rng.uniform(-20, 340) if side < 2 else (rng.uniform(-28, -12) if side == 2 else rng.uniform(328, 345))
        z = (rng.uniform(-30, -16) if side == 0 else rng.uniform(258, 275)) if side < 2 else rng.uniform(-20, 270)
        h = rng.uniform(5, 10)
        g.cylinder((x, 0, z), 0.18, h * 0.45, (0.33, 0.26, 0.2), step=0.12)
        n = int(h * 160)
        d = rng.normal(0, 1, (n, 3))
        d /= np.linalg.norm(d, axis=1, keepdims=True)
        r = h * 0.32 * rng.uniform(0.55, 1, (n, 1))
        g.add(np.array([x, h * 0.65, z]) + d * r * np.array([1, 1.15, 1]), (0.45, 0.45, 0.45),
              np.array([0.2, 0.33, 0.16]) * rng.uniform(0.7, 1.2, (n, 1)), 0.9, noise=0.03)


# ---------- hidden visual states, painted onto one asset each ----------
def overlay(asset, state, seed):
    """The state's look as extra Gaussians just in front of the asset's surfaces, or None for a look the base already has."""
    g = Splat(seed)
    x, y, z = ANCHORS.get(asset, (0, 0, 0))
    rng = g.rng
    if asset.startswith("INV-"):
        fs, ft, w, h = INV_SCREEN
        c = on_inv_face(x, z, fs, ft) - np.array([0.006, 0, 0])
        u, v = np.array([0, 0, INV_SIZE[2] * w]), np.array([0, INV_SIZE[1] * h, 0])
        if state == "healthy":
            return None                                         # the photo skin already shows a lit screen
        if state == "display_off":
            g.face(c, u * 1.08, v * 1.1, (0.03, 0.03, 0.035), 0.01, thick=0.003, noise=0.005, overlap=1.5)
        elif state == "filter_clogged":
            for fs, ft, w, h in INV_LOUVRES:
                n = 2600
                p = on_inv_face(x, z, fs, ft) + np.stack([-rng.uniform(0.004, 0.03, n), rng.uniform(-h / 2, h / 2, n) * INV_SIZE[1],
                                                          rng.uniform(-w / 2, w / 2, n) * INV_SIZE[2]], 1)
                g.add(p, (0.006, 0.03, 0.03), (0.42, 0.36, 0.28), 0.93, noise=0.06)
        else:
            return None
    elif asset == "XFMR-1" and state == "oil_leak":
        sx, sy, sz = XFMR_SIZE
        n = 6000                                                                   # dark pool spreading off the pad, south-west corner
        r = np.sqrt(rng.uniform(0, 1, n)) * rng.uniform(0.8, 1.4, n)
        a = rng.uniform(0, 2 * math.pi, n)
        p = np.stack([x - sx / 2 - 0.2 + r * np.cos(a) * 1.3, np.full(n, 0.215), z + sz / 2 + 0.1 + r * np.sin(a)], 1)
        g.add(p, (0.06, 0.004, 0.06), (0.05, 0.04, 0.03), 0.96, noise=0.015)
        for k in range(6):                                                         # streaks down the west and south faces
            m = 500
            if k < 3:
                q = np.stack([np.full(m, x - sx / 2 - 0.01), rng.uniform(0.25, 1.9, m), z + 0.2 + 0.25 * k + rng.normal(0, 0.03, m)], 1)
            else:
                q = np.stack([x - 0.8 + 0.25 * (k - 3) + rng.normal(0, 0.03, m), rng.uniform(0.25, 1.9, m), np.full(m, z + sz / 2 + 0.01)], 1)
            g.add(q, (0.025, 0.05, 0.025), (0.07, 0.055, 0.03), 0.88, noise=0.02)
    elif asset.startswith("CB-") and state == "connector_burnt":
        n = 900
        p = np.stack([x + rng.normal(-0.15, 0.12, n), 1.0 + rng.uniform(0, 0.3, n) ** 1.5, np.full(n, z - 0.158)], 1)
        g.add(p, (0.02, 0.02, 0.003), (0.06, 0.05, 0.04), 0.95, noise=0.02)
        g.cylinder((x - 0.1, 0.62, z), 0.04, 0.12, (0.02, 0.02, 0.02), step=0.02)
    elif asset.startswith("B") and state in ("array_soiled", "array_overgrown"):
        z0 = ANCHORS[asset][2]
        x0, x1 = BLOCK_X[0] + 2, BLOCK_X[1] - 2
        depth = 2.3
        for dz in PANEL_ROWS:
            if state == "array_soiled":
                u = np.array([x1 - x0, 0, 0])
                v = np.array([0, depth * math.sin(TILT), -depth * math.cos(TILT)])
                lift = np.array([0, math.cos(TILT), math.sin(TILT)]) * 0.012
                g.face(np.array([(x0 + x1) / 2, 1.3, z0 + dz]) + lift, u, v, (0.6, 0.53, 0.42), 0.17, alpha=0.62, thick=0.004,
                       color_fn=lambda s, t: np.stack([0.6 + 0.08 * (t + 0.5), 0.53 + 0.06 * (t + 0.5), 0.42 + 0 * t], 1))
            else:
                n = 7000
                p = np.stack([rng.uniform(x0, x1, n), rng.uniform(0.05, 1.25, n) ** 1.3, z0 + dz + rng.uniform(-2.2, 2.2, n)], 1)
                g.add(p, (0.04, 0.22, 0.04), (0.26, 0.42, 0.14), 0.9, noise=0.07)
    elif asset.startswith("52-") and state.startswith("handle_"):
        fz = z - 0.515
        angle = {"handle_closed": 35, "handle_open": -35, "handle_tripped": 0}[state]
        a = math.radians(angle)
        g.face((x, 1.25 + 0.12 * math.sin(a), fz - 0.06), (-0.08, 0, 0), (0, 0.28 * math.cos(a), -0.1), (0.1, 0.1, 0.1), 0.012, thick=0.01)
        flag = {"handle_closed": (0.85, 0.1, 0.08), "handle_open": (0.1, 0.7, 0.2), "handle_tripped": (0.95, 0.7, 0.05)}[state]
        g.face((x + 0.4, 1.6, fz), (-0.18, 0, 0), (0, 0.12, 0), flag, 0.01, thick=0.003, noise=0.0)   # ON / OFF / TRIP flag
    else:
        return None
    return g if g.parts else None


def station_poses(viewpoints):
    """Camera pose per viewpoint: from its map_xy (or the wall's default placement) towards the asset it watches."""
    out = {}
    for vid, vp in sorted(viewpoints.items()):
        asset = vp["asset"]
        ax, ay, az = ANCHORS[asset]
        if vp.get("map_xy"):
            cx, cz = vp["map_xy"]
        else:
            cx, cz = default_station(asset)
        h = 7.0 if asset.startswith("B") and not asset.startswith("BESS") else 2.4
        dist = math.hypot(cx - ax, cz - az)
        if asset.startswith("B") and not asset.startswith("BESS"):
            ay = 0.8
        elif dist > 12:                                       # long throws: step in, like a zoomed PTZ camera
            k = 9 / dist
            cx, cz = ax + (cx - ax) * k, az + (cz - az) * k
        out[vid] = {"asset": asset, "position": [round(cx, 2), h, round(cz, 2)], "target": [round(ax, 2), round(ay, 2), round(az, 2)],
                    "fov": 38 if asset.startswith(("B", "XFMR")) and not asset.startswith("BESS") else 50}
    return out


def default_station(asset):
    """wall.js placement(): the station position when viewpoints.yaml gives no map_xy."""
    x, _, z = ANCHORS[asset]
    if asset.startswith("INV-") or asset in ("XFMR-1", "POI-1"):
        return x - 7, z + 4      # in front of the west (display) face
    if asset.startswith("52-"):
        return x - 2, z - 5
    if asset.startswith("CB-"):
        return x - 1.5, z - 3.5
    if asset.startswith("B"):
        return x - 20, z + 22
    return x + 6, z + 6


def load_viewpoints():
    vps, states = {}, {}
    for path in (REPO / "gridtwin" / "sim" / "viewpoints_extra.yaml", REPO / "assets" / "photos" / "viewpoints.yaml"):
        doc = yaml.safe_load(path.read_text()) or {}
        vps.update(doc.get("viewpoints") or {})
        states.update(doc.get("states") or {})
    return vps, states


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    g = Splat(a.seed)
    CASTERS.clear()
    site(g)
    ground(g)
    n = g.write(a.out / "plant.splat")
    vps, states = load_viewpoints()
    written = {}
    for asset, sts in sorted(states.items()):
        for st in sts:
            ov = overlay(asset, st, a.seed + len(written))
            if ov is not None:
                ov.write(a.out / "overlays" / asset / f"{st}.splat")
                written.setdefault(asset, []).append(st)
    scene = {"units": "m", "up": "y", "note": "world x = wall map x, world z = wall map y (south)",
             "anchors": {k: list(v) for k, v in sorted(ANCHORS.items())}, "stations": station_poses(vps), "gaussians": n,
             "sky": [round(float(c), 3) for c in tex(HORIZON, (0, 0, 1, 0.36), res=(8, 70))(np.zeros(1), np.full(1, 0.49))[0]]}
    (a.out / "scene.json").write_text(json.dumps(scene, indent=1) + "\n")
    print(f"plant.splat: {n} Gaussians ({(a.out / 'plant.splat').stat().st_size / 1e6:.1f} MB); "
          f"overlays: {sum(map(len, written.values()))}; stations: {len(scene['stations'])}")


if __name__ == "__main__":
    main()

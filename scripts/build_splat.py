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

    def box(self, center, size, rgb, step=0.15, shade=True, skip=("bottom",), alpha=1.0):
        cx, cy, cz = center
        sx, sy, sz = size
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
            self.face((cx + off[0], cy + off[1], cz + off[2]), u, v, np.asarray(rgb) * (k if shade else 1), step, alpha)

    def cylinder(self, base, radius, height, rgb, step=0.08, axis="y"):
        n_a = max(8, round(2 * math.pi * radius / step))
        n_h = max(1, round(height / step))
        a, h = np.meshgrid(np.linspace(0, 2 * math.pi, n_a, endpoint=False), (np.arange(n_h) + 0.5) / n_h * height)
        a, h = a.ravel(), h.ravel()
        ring = np.stack([radius * np.cos(a), h, radius * np.sin(a)], 1)
        if axis == "x":
            ring = ring[:, [1, 0, 2]]
        shade = 0.75 + 0.3 * np.clip(np.cos(a - 2.2), 0, 1)
        self.add(np.asarray(base) + ring, (step * 0.7, step * 0.7, step * 0.7), np.asarray(rgb)[None] * shade[:, None])

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
PANEL_ROWS = (-9.6, -4.8, 0.0, 4.8, 9.6)
TILT = math.radians(25)


def ground(g):
    # outside the fence: dry soil from the Topaz Solar Farm photo, coarse, in four strips so it never sits under the gravel
    for cx, cz, w, h in ((160, -29, 420, 42), (160, 271, 420, 42), (-24, 125, 52, 258), (344, 125, 52, 258)):
        g.face((cx, 0, cz), (w, 0, 0), (0, 0, -h), (0.55, 0.5, 0.4), 1.4, thick=0.05, jitter=0.45, noise=0.02,
               color_fn=tex(HORIZON, SOIL_BOX, (w / 12, h / 12), mirror=True, res=(24, 8)))
    # inside the fence: crushed-rock pad, tiled from the gravel around the PSE transformer photo
    g.face((160, 0.0, 121), (316, 0, 0), (0, 0, -258), (0.56, 0.54, 0.5), 0.75, thick=0.02, jitter=0.45, noise=0.025,
           color_fn=lambda s, t: 0.55 * tex(GRAVEL, GRAVEL_BOX, (1, 1), res=(1, 1), gain=1.15)(s, t)
           + 0.45 * tex(HORIZON, SOIL_BOX, (1, 1), res=(1, 1))(s, t))           # graded site: the photos' mean gravel and soil
    # access road and cable trenches
    g.face((160, 0.05, 245), (316, 0, 0), (0, 0, -5), (0.42, 0.4, 0.37), 0.6, thick=0.02)
    g.face((BUS_X, 0.06, 126), (0.8, 0, 0), (0, 0, -200), (0.66, 0.66, 0.64), 0.4, thick=0.02)
    for z in ROW_Z:
        g.face(((BLOCK_X[1] + BUS_X) / 2, 0.06, z + 3.5), (BUS_X - BLOCK_X[1], 0, 0), (0, 0, -0.6), (0.66, 0.66, 0.64), 0.4)
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
        base[line] = (0.62, 0.64, 0.68)
        return base
    for dz in PANEL_ROWS:
        c = np.array([(x0 + x1) / 2, 1.3, z0 + dz])
        g.face(c, u, v, (0.1, 0.14, 0.28), 0.17, thick=0.015, jitter=0.15, noise=0.02,
               color_fn=lambda s, t: 0.4 * tex(PANELS, PANELS_BOX, (length / 9, 1), mirror=True)(s, t) + 0.6 * cells(s, t))
        g.face(c - np.array([0, 0.18, 0]), u * 0.98, v * 0.9, (0.3, 0.3, 0.32), 0.6, thick=0.015, noise=0.02)  # rear sheet
        for px in np.arange(x0 + 2, x1, 6):  # posts and torque tube
            g.box((px, 0.6, z0 + dz + 0.4), (0.1, 1.2, 0.1), (0.62, 0.62, 0.64), step=0.1, shade=True)
            g.box((px, 0.5, z0 + dz - 0.4), (0.1, 1.0, 0.1), (0.62, 0.62, 0.64), step=0.1, shade=True)
        g.cylinder((x0, 1.2, z0 + dz), 0.06, length, (0.6, 0.6, 0.62), step=0.35, axis="x")


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
    x, _, z = ANCHORS[name]
    g.box((x, 0.1, z), (2.2, 0.2, 2.2), (0.7, 0.69, 0.66), step=0.15)
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
        g.face((mid[0], h / 2, mid[1]), u, (0, h, 0), mesh, 0.3, alpha=0.2, thick=0.01, noise=0.02)
        g.face((mid[0], h, mid[1]), u, (0, 0.06, 0), (0.5, 0.5, 0.52), 0.08, thick=0.02)  # top rail


def site(g):
    for i in range(4):
        pv_block(g, i)
        inverter(g, i)
        breaker(g, f"52-I{i + 1}")
        for n in range(1, 7):
            combiner(g, i, n)
    breaker(g, "52-M")
    breaker(g, "52-B1")
    transformer(g)
    x, _, z = ANCHORS["POI-1"]
    g.box((x, 0.1, z), (3, 0.2, 3), (0.7, 0.69, 0.66), step=0.15)
    g.box((x, 1.1, z), (1.0, 1.8, 0.6), (0.75, 0.77, 0.76), step=0.06)
    g.cylinder((x + 1.2, 0, z), 0.15, 9.0, (0.45, 0.35, 0.25), step=0.08)        # utility pole
    g.box((x + 1.2, 8.6, z), (0.15, 0.15, 2.4), (0.45, 0.35, 0.25), step=0.06)
    x, _, z = ANCHORS["BESS-1"]
    for dz in (-1.6, 1.6):                                                          # two battery containers
        g.box((x - 2, 1.4, z + dz), (12.0, 2.6, 2.4), (0.9, 0.9, 0.88), step=0.12)
        for k in range(5):
            g.face((x - 7 + 2.4 * k + 1.2, 1.4, z + dz + 1.206), (0.04, 0, 0), (0, 2.4, 0), (0.6, 0.6, 0.6), 0.02, thick=0.004)
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
    ground(g)
    site(g)
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

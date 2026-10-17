"""A synthetic plant room that reads like a scan, for working on viewer/twin.html before a real capture exists.

python -m scripts.twin_dev_scene writes assets/splats/_dev.ply and assets/splats/_dev.registration.json (both
gitignored); serve the repo with python -m gridtwin.serve and open /twin.html?scene=_dev. The room is a closed 17 x 15 m
electrical room under a 4.5 m ceiling: three inverter cabinets, the pad-mounted transformer, a battery enclosure, a
combiner box and a breaker panel, with ladder trays full of cable, conduits, pipes with flanges and a valve, wall panels,
a fire extinguisher, exit signs, drains, bollards, rubber mats and worn walkway paint, and the registration board on the
floor at the origin. It is meant to look like a phone or laser capture: the light of the ceiling panels is baked in
(Lambert per normal, soft shadows on the floor and walls, ambient occlusion at the junctions and round the equipment),
the surfaces carry concrete aggregate, tyre marks, grime and paint wear, the inverter and transformer fronts are real
photos, and the Gaussians get the jitter, softness and faint floaters a capture has. Station cameras for the real
viewpoint ids are written straight into the registration, from the known geometry, in the same schema
scripts/register_viewpoints.py writes, so the twin's pins and frustums can be checked without photos.

Built in metres with z up around the board, then moved into "splat" coordinates (y down, z forward, a non-metric scale)
the way a phone capture arrives, so the viewer's frame handling is exercised too.

Photo credits (Wikimedia Commons, rows of assets/photos/LICENSES.csv; cropped, rectified, resized and relit here, so the
generated _dev.ply is an adaptation and is shared under the same CC BY-SA 4.0 terms):
  INV-1 front: "Dornbirn-SMA Sunny Boy inverter SWR 3000-01ASD" by Asurnipal, CC BY-SA 4.0,
    https://commons.wikimedia.org/wiki/File:Dornbirn-SMA_Sunny_Boy_inverter_SWR_3000-01ASD.jpg
  INV-2 front: "Inverter SMA Sunny Boy SB 2100TL-01ASD" by Asurnipal, CC BY-SA 4.0,
    https://commons.wikimedia.org/wiki/File:Inverter_SMA_Sunny_Boy_SB_2100TL-01ASD.jpg
  INV-3 front: "Inverter SMA Sunny Boy SB 2800i-01ASD" by Asurnipal, CC BY-SA 4.0,
    https://commons.wikimedia.org/wiki/File:Inverter_SMA_Sunny_Boy_SB_2800i-01ASD.jpg
  XFMR-1 front: "Pad-mounted Transformer Photo 2024" by Simon-says-elkay, CC BY-SA 4.0,
    https://commons.wikimedia.org/wiki/File:Pad-mounted_Transformer_Photo_2024.jpg
"""
import argparse
import csv
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from gridtwin.splat.register import Board

REPO = Path(__file__).resolve().parents[1]
PHOTO_DIR = REPO / "assets" / "photos"
SH_C0 = 0.28209479177387814
S = 0.8                                                      # splat units per metre
R_S = np.array([[1.0, 0, 0], [0, 0, -1], [0, 1, 0]])         # board (z up) -> splat (y down)
T_S = np.array([0.3, 1.2, -0.5])
Q_S = np.array([np.sqrt(0.5), np.sqrt(0.5), 0.0, 0.0])       # R_S as a quaternion (w, x, y, z)
SIZE, F35 = (1600, 1200), 26.0
ROOM = (-8.5, 8.5, -10.0, 5.0, 4.5)                          # x0, x1, y0, y1, ceiling (m); the door is in the y0 wall
LIGHTS = [(x, y) for y in (-8.0, -5.0, -2.0, 1.2, 4.0) for x in (-6.4, -2.1, 2.1, 6.4)]  # 1.2 x 0.3 m panels along x
AMBIENT, DIRECT, BOUNCE, EXPOSURE = 0.3, 0.75, 0.16, 1.65     # the baked light, tuned against the previews
FLOOR_STEP, WALL_STEP, CEIL_STEP = 0.029, 0.038, 0.06        # Gaussian spacing (m)

# asset -> (lo, hi) box in metres; fronts face -y (towards the walkway) unless FRONT says otherwise
EQUIPMENT = {
    "INV-1": ((-4.6, 2.6, 0.0), (-3.2, 3.4, 2.1)),
    "INV-2": ((-0.7, 2.6, 0.0), (0.7, 3.4, 2.1)),
    "INV-3": ((3.2, 2.6, 0.0), (4.6, 3.4, 2.1)),
    "XFMR-1": ((5.8, -3.6, 0.1), (7.4, -1.6, 1.42)),         # pad-mounted, on a 10 cm concrete pad
    "BESS-1": ((-8.0, -3.5, 0.0), (-6.6, 0.5, 2.6)),
    "CB-1.5": ((8.2, 0.6, 1.1), (8.45, 1.2, 1.8)),           # on the right wall
    "52-I4": ((1.6, 4.5, 0.4), (2.6, 4.9, 2.2)),             # breaker panel against the back wall
}
FRONT = {"XFMR-1": "-x", "BESS-1": "+x", "CB-1.5": "-x"}
# viewpoint id -> (asset, how far the station camera stands in front of the asset, metres)
STATIONS = {"inv1_cabinet": ("INV-1", 2.8), "inv2_cabinet": ("INV-2", 3.0), "inv3_cabinet": ("INV-3", 2.6),
            "xfmr1_yard": ("XFMR-1", 3.4), "cb15_box": ("CB-1.5", 1.6), "brk_52i4": ("52-I4", 2.2)}
# inverter -> (photo under assets/photos, the unit's corners in it (tl, tr, br, bl) px, unit width m, width / height)
UNITS = {"INV-1": ("INV-1/healthy/01_dornbirn_sma_sunny_boy_inverter_swr_3000_01asd.jpg",
                   [(56, 160), (930, 160), (930, 660), (56, 660)], 1.0, 1.75),
         "INV-2": ("INV-1/healthy/02_inverter_sma_sunny_boy_sb_2100tl_01asd.jpg",
                   [(62, 114), (944, 114), (944, 626), (62, 626)], 1.0, 1.72),
         "INV-3": ("INV-1/healthy/03_inverter_sma_sunny_boy_sb_2800i_01asd.jpg",  # shot from above: the lid, rectified
                   [(105, 84), (796, 68), (986, 462), (113, 583)], 0.9, 1.35)}
XFMR_PHOTO = "XFMR-1/normal/01_pad_mounted_transformer_photo_2024.jpg"
PHOTOS = [u[0] for u in UNITS.values()] + [XFMR_PHOTO]

STEEL, GALV, GREY = (0.78, 0.79, 0.77), (0.6, 0.61, 0.6), (0.64, 0.66, 0.66)  # RAL 7035 cabinets, zinc, RAL 7032
CONCRETE, BLACK, YELLOW = (0.6, 0.59, 0.56), (0.055, 0.055, 0.06), (0.84, 0.66, 0.14)
WALL, DADO, SKIRT = (0.78, 0.78, 0.755), (0.6, 0.62, 0.62), (0.26, 0.26, 0.27)


# ---------- small maths ----------
def grid(o, a, b, step):
    """Points o + u*a_hat + v*b_hat on a step grid over the rectangle spanned by a and b, and their (u, v) in metres."""
    o, a, b = (np.asarray(x, float) for x in (o, a, b))
    la, lb = np.linalg.norm(a), np.linalg.norm(b)
    u, v = np.meshgrid(np.arange(min(step, la) / 2, la, step), np.arange(min(step, lb) / 2, lb, step))
    u, v = u.ravel(), v.ravel()
    return o + np.outer(u, a / la) + np.outer(v, b / lb), u, v


def smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def srgb_lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((np.maximum(c, 0) + 0.055) / 1.055) ** 2.4)


def lin_srgb(c):
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(np.maximum(c, 0), 1 / 2.4) - 0.055)


def qmul(a, b):
    """Hamilton product of (..., 4) quaternions (w, x, y, z)."""
    aw, ax, ay, az = np.moveaxis(np.asarray(a), -1, 0)
    bw, bx, by, bz = np.moveaxis(np.asarray(b), -1, 0)
    return np.stack([aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw], -1)


def qaxis(axis, angle):
    """Rotations by angle (N,) about unit axes (3,) or (N, 3)."""
    h = np.asarray(angle, float)[..., None] / 2
    return np.concatenate([np.cos(h), np.sin(h) * axis], -1)


def qmat(R):
    """One rotation matrix -> quaternion."""
    w = np.sqrt(max(0.0, 1 + R[0, 0] + R[1, 1] + R[2, 2])) / 2
    x = np.copysign(np.sqrt(max(0.0, 1 + R[0, 0] - R[1, 1] - R[2, 2])) / 2, R[2, 1] - R[1, 2])
    y = np.copysign(np.sqrt(max(0.0, 1 - R[0, 0] + R[1, 1] - R[2, 2])) / 2, R[0, 2] - R[2, 0])
    z = np.copysign(np.sqrt(max(0.0, 1 - R[0, 0] - R[1, 1] + R[2, 2])) / 2, R[1, 0] - R[0, 1])
    return np.array([w, x, y, z])


def tilt(rng, n, s=0.05):
    """Small random rotations, about s radians, so no two Gaussians sit at exactly the same angle."""
    ax = rng.normal(0, 1, (n, 3))
    return qaxis(ax / np.linalg.norm(ax, axis=1, keepdims=True), rng.normal(0, s, n))


def fbm(rng, shape, cell, octaves=4, gain=0.5, stretch=(1.0, 1.0)):
    """Zero-mean fractal value noise; cell is the coarsest feature in pixels, stretch scales it along (rows, cols)."""
    h, w = shape
    out, amp, tot = np.zeros(shape, np.float32), 1.0, 0.0
    for o in range(octaves):
        cy, cx = max(1.0, cell * stretch[0] / 2 ** o), max(1.0, cell * stretch[1] / 2 ** o)
        gh, gw = int(h / cy) + 3, int(w / cx) + 3
        g = rng.standard_normal((gh, gw)).astype(np.float32)
        out += amp * cv2.resize(g, (int(gw * cx), int(gh * cy)), interpolation=cv2.INTER_CUBIC)[:h, :w]
        tot += amp * amp
        amp *= gain
    return out / np.sqrt(tot)


def bilinear(img, x, y):
    """img sampled at fractional pixel (x, y) per point."""
    H, W = img.shape[:2]
    x, y = np.clip(x, 0, W - 1.001), np.clip(y, 0, H - 1.001)
    i, j = y.astype(np.int64), x.astype(np.int64)
    fy, fx = (y - i)[:, None], (x - j)[:, None]
    I = img.reshape(H, W, -1)
    out = (I[i, j] * (1 - fx) + I[i, j + 1] * fx) * (1 - fy) + (I[i + 1, j] * (1 - fx) + I[i + 1, j + 1] * fx) * fy
    return out if img.ndim == 3 else out[:, 0]


def box_dist(p, lo, hi):
    return np.linalg.norm(p - np.clip(p, lo, hi), axis=1)


def seg_hits(p, d, lo, hi):
    """Whether the segments p -> p + d pass through the box (lo, hi)."""
    inv = 1 / np.where(np.abs(d) < 1e-9, 1e-9, d)
    t1, t2 = (lo - p) * inv, (hi - p) * inv
    tmin, tmax = np.minimum(t1, t2).max(1), np.maximum(t1, t2).min(1)
    return (tmax > np.maximum(tmin, 1e-4)) & (tmin < 1)


# ---------- textures ----------
class Canvas:
    """A face texture painted in metres (u right, v up); calling it samples the colour at (u, v)."""

    def __init__(self, w, h, res, color=0.5, img=None):
        self.w, self.h = w, h
        if img is None:
            img = np.empty((max(2, int(round(h / res))), max(2, int(round(w / res))), 3), np.float32)
            img[:] = color
        self.img = img
        self.rx, self.ry = w / img.shape[1], h / img.shape[0]

    def __call__(self, u, v):
        return bilinear(self.img, u / self.rx - 0.5, (self.h - v) / self.ry - 0.5)

    def px(self, u, v):
        return int(round(u / self.rx)), int(round((self.h - v) / self.ry))

    def fill(self, u0, v0, u1, v1, color, alpha=1.0):
        (a, b), (c, d) = self.px(u0, v1), self.px(u1, v0)
        H, W = self.img.shape[:2]
        a, b, c, d = max(a, 0), max(b, 0), min(max(c, a + 1), W), min(max(d, b + 1), H)
        s = self.img[b:d, a:c]
        s[:] = s * (1 - alpha) + np.asarray(color, np.float32) * alpha

    def frame(self, u0, v0, u1, v1, color, t):
        for box in ((u0, v0, u1, v0 + t), (u0, v1 - t, u1, v1), (u0, v0, u0 + t, v1), (u1 - t, v0, u1, v1)):
            self.fill(*box, color)

    def blend(self, mask, color, alpha=1.0):
        m = (mask.astype(np.float32) / 255 if mask.dtype == np.uint8 else mask)[..., None] * alpha
        self.img[:] = self.img * (1 - m) + np.asarray(color, np.float32) * m

    def text(self, s, u0, v0, u1, v1, color, fill=0.62, font=cv2.FONT_HERSHEY_DUPLEX):
        """s centred in the box, as large as fits."""
        (a, b), (c, d) = self.px(u0, v1), self.px(u1, v0)
        (tw, th), _ = cv2.getTextSize(s, font, 1.0, 1)
        scale = min(0.9 * (c - a) / tw, fill * (d - b) / th)
        thick = max(1, int(round(scale * 1.8)))
        (tw, th), _ = cv2.getTextSize(s, font, scale, thick)
        m = np.zeros(self.img.shape[:2], np.uint8)
        cv2.putText(m, s, ((a + c - tw) // 2, (b + d + th) // 2), font, scale, 255, thick, cv2.LINE_AA)
        self.blend(m, color)

    def poly(self, pts, color, alpha=1.0):
        m = np.zeros(self.img.shape[:2], np.uint8)
        cv2.fillPoly(m, [np.array([self.px(u, v) for u, v in pts], np.int32)], 255, cv2.LINE_AA)
        self.blend(m, color, alpha)

    def circle(self, u, v, r, color, alpha=1.0):
        m = np.zeros(self.img.shape[:2], np.uint8)
        cv2.circle(m, self.px(u, v), max(1, int(round(r / self.rx))), 255, -1, cv2.LINE_AA)
        self.blend(m, color, alpha)

    def paste(self, src, u0, v0, u1, v1):
        (a, b), (c, d) = self.px(u0, v1), self.px(u1, v0)
        self.img[b:d, a:c] = cv2.resize(src, (c - a, d - b), interpolation=cv2.INTER_AREA)


def weather(cv, rng, dirt=1.0, streaks=1.0, mottle=1.0):
    """Mottled paint, kick dirt along the bottom and run-down streaks, as a real face collects them."""
    H, W = cv.img.shape[:2]
    r = cv.rx
    v = cv.h - (np.arange(H, dtype=np.float32)[:, None] + 0.5) * cv.ry
    m = 1 + mottle * (0.035 * fbm(rng, (H, W), 0.6 / r, 3) + 0.02 * fbm(rng, (H, W), 0.05 / r, 2))
    m = m * (1 - 0.22 * dirt * np.exp(-np.maximum(v, 0) / 0.2) * (0.7 + 0.5 * fbm(rng, (H, W), 0.15 / r, 3)))
    if streaks:
        m = m * (1 - 0.22 * streaks * np.clip(fbm(rng, (H, W), 0.25 / r, 3, stretch=(8, 0.3)) - 0.45, 0, 1))
    m = np.clip(m, 0.3, 1.3)
    cv.img *= m[..., None]
    cv.img[..., 2] *= 1 - 0.3 * np.clip(1 - m, 0, 1)  # grime is warm
    np.clip(cv.img, 0, 1, out=cv.img)
    return cv


def painted(rng, color, w, h, res=0.01, **k):
    return weather(Canvas(w, h, res, color), rng, **k)


def label(cv, text, u0, v0, w, h, bg=(0.86, 0.7, 0.2), fg=(0.05, 0.05, 0.05)):
    """An engraved asset plate with two screws."""
    e = min(w, h) * 0.07
    cv.fill(u0, v0, u0 + w, v0 + h, np.asarray(bg) * 0.6)
    cv.fill(u0 + e, v0 + e, u0 + w - e, v0 + h - e, bg)
    cv.text(text, u0 + 3.5 * e, v0 + e, u0 + w - 3.5 * e, v0 + h - e, fg)
    for su in (u0 + 1.9 * e, u0 + w - 1.9 * e):
        cv.circle(su, v0 + h / 2, e * 0.7, (0.5, 0.5, 0.48))


def hazard(cv, u, v, s):
    """The yellow electrical-hazard triangle, s wide, bottom-left corner at (u, v)."""
    cv.poly([(u, v), (u + s, v), (u + s / 2, v + 0.87 * s)], (0.06, 0.06, 0.06))
    k = 0.11 * s
    cv.poly([(u + 1.8 * k, v + k), (u + s - 1.8 * k, v + k), (u + s / 2, v + 0.87 * s - 2 * k)], (0.9, 0.72, 0.12))
    cv.poly([(u + 0.54 * s, v + 0.62 * s), (u + 0.4 * s, v + 0.36 * s), (u + 0.5 * s, v + 0.38 * s),
             (u + 0.46 * s, v + 0.16 * s), (u + 0.6 * s, v + 0.44 * s), (u + 0.5 * s, v + 0.42 * s)], (0.06, 0.06, 0.06))


def notice(cv, rng, u0, v0, w, h, head, head_bg, head_fg=(1, 1, 1), body=(0.93, 0.93, 0.9)):
    """A safety sticker: a coloured header word over a few lines of small print."""
    cv.fill(u0, v0, u0 + w, v0 + h, body)
    cv.fill(u0, v0 + 0.7 * h, u0 + w, v0 + h, head_bg)
    cv.text(head, u0, v0 + 0.7 * h, u0 + w, v0 + h, head_fg, fill=0.66)
    for k in range(4):
        y = v0 + h * (0.1 + 0.14 * k)
        cv.fill(u0 + 0.08 * w, y, u0 + w * rng.uniform(0.55, 0.92), y + 0.045 * h, (0.3, 0.3, 0.3))


def louvres(cv, u0, v0, u1, v1, base, pitch=0.03):
    cv.fill(u0, v0, u1, v1, np.asarray(base) * 0.8)
    for v in np.arange(v0 + 0.012, v1 - 0.012, pitch):
        cv.fill(u0 + 0.012, v, u1 - 0.012, v + 0.45 * pitch, np.asarray(base) * 0.42)
        cv.fill(u0 + 0.012, v + 0.45 * pitch, u1 - 0.012, v + 0.6 * pitch, np.minimum(np.asarray(base) * 1.08, 1))


def photo(rel, quad, w_px, h_px):
    """A quad of a licensed photo warped onto a w x h px rectangle, RGB 0..1, pre-blurred for the downscale."""
    img = cv2.imread(str(PHOTO_DIR / rel), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(PHOTO_DIR / rel)
    q = np.float32(quad)
    shrink = np.linalg.norm(q[1] - q[0]) / w_px
    if shrink > 1.2:
        img = cv2.GaussianBlur(img, (0, 0), 0.45 * shrink)
    M = cv2.getPerspectiveTransform(q, np.float32([[0, 0], [w_px, 0], [w_px, h_px], [0, h_px]]))
    out = cv2.warpPerspective(img, M, (w_px, h_px), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB).astype(np.float32) / 255


def credits():
    rows = {r["file"]: r for r in csv.DictReader((PHOTO_DIR / "LICENSES.csv").read_text().splitlines())}
    for rel in PHOTOS:
        r = rows.get(f"assets/photos/{rel}")
        yield f"{rel} by {r['author']}, {r['license']}, {r['source_url']}" if r else f"{rel}: not in LICENSES.csv"


# ---------- the scene ----------
class Scene:
    def __init__(self, seed=0):
        self.rng = np.random.default_rng(seed)
        self.parts = []   # per-Gaussian arrays, merged and lit by finish()
        self.boxes = []   # (lo, hi) of every solid, for occlusion
        self.casts = []   # the solids that cast shadows on the floor and walls
        self.feet = []    # the solids standing on the floor (no floor Gaussians under them)

    def solid(self, lo, hi, casts=True, feet=None):
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        self.boxes.append((lo, hi))
        i = len(self.boxes) - 1
        if casts:
            self.casts.append(i)
        if feet if feet is not None else lo[2] < 0.02:
            self.feet.append(i)
        return i

    def add(self, p, color, normal, sig, q, owner=-1, ao=1.0, gloss=0.0, emit=False, opacity=None):
        n = len(p)
        if not n:
            return
        f = lambda x, *k: np.broadcast_to(np.asarray(x, np.float32), (n, *k)).copy()
        self.parts.append({"p": np.asarray(p, np.float32), "c": np.clip(color, 0, 1).astype(np.float32),
                           "n": f(normal, 3), "sig": np.asarray(sig, np.float32), "q": np.asarray(q, np.float32),
                           "own": np.full(n, owner, np.int32), "ao": f(ao), "gloss": f(gloss), "emit": np.full(n, emit),
                           "opa": f(self.rng.uniform(0.9, 0.995, n) if opacity is None else opacity)})

    def rect(self, o, a, b, step, color, normal, owner=-1, keep=None, crisp=False, edge=0.0, gloss=0.0, emit=False):
        """Flat discs over o + u a_hat + v b_hat (0 <= u < |a|, 0 <= v < |b|) facing `normal` (along a board axis);
        color is an rgb or f(u, v) in metres. Jittered, so no grid shows, unless crisp."""
        rng = self.rng
        o, a, b, normal = (np.asarray(x, float) for x in (o, a, b, normal))
        la, lb = np.linalg.norm(a), np.linalg.norm(b)
        _, u, v = grid(o, a, b, step)
        if not crisp:
            u = np.clip(u + rng.uniform(-0.45, 0.45, len(u)) * step, 0, la)
            v = np.clip(v + rng.uniform(-0.45, 0.45, len(v)) * step, 0, lb)
        p = o + np.outer(u, a / la) + np.outer(v, b / lb)
        if keep is not None:
            m = keep(p)
            p, u, v = p[m], u[m], v[m]
        n = len(p)
        k = int(np.argmax(np.abs(normal)))
        if crisp:
            sig = np.full((n, 3), 0.62 * step)
            sig[:, k] = 0.002
            q = np.tile([1.0, 0, 0, 0], (n, 1))
        else:
            sig = 0.68 * step * rng.lognormal(0, 0.15, (n, 1)) * rng.uniform(0.82, 1.18, (n, 3))
            sig[:, k] = 0.004 * rng.lognormal(0, 0.3, n)
            ax = np.zeros(3)
            ax[k] = 1
            q = qmul(tilt(rng, n), qaxis(ax, rng.uniform(0, np.pi, n)))
            p = p + normal * rng.normal(0, 0.0015, (n, 1))  # a capture's surface is never quite flat
        col = color(u, v) if callable(color) else np.tile(np.asarray(color, float), (n, 1))
        ao = 1 - edge * np.exp(-np.minimum(np.minimum(u, la - u), np.minimum(v, lb - v)) / 0.012) if edge else 1.0
        self.add(p, col, normal, sig, q, owner, ao, gloss(u, v) if callable(gloss) else gloss, emit,
                 0.995 if crisp else None)

    def box(self, lo, hi, step, faces=None, color=GREY, skip=(), casts=True, edge=0.3, owner=None, feet=None):
        """A solid box: its faces from faces[name] (an rgb, a Canvas or f(u, v)) or else a weathered `color`; u runs left
        to right seen from outside and v up (on +z u is along x and v along y). step is metres or {face: metres, "*": m}.
        A -z face is drawn only when faces names one."""
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        if owner is None:
            owner = self.solid(lo, hi, casts, feet)
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        F = {"-y": ((x0, y0, z0), (x1 - x0, 0, 0), (0, 0, z1 - z0), (0, -1, 0)),
             "+y": ((x1, y1, z0), (x0 - x1, 0, 0), (0, 0, z1 - z0), (0, 1, 0)),
             "-x": ((x0, y1, z0), (0, y0 - y1, 0), (0, 0, z1 - z0), (-1, 0, 0)),
             "+x": ((x1, y0, z0), (0, y1 - y0, 0), (0, 0, z1 - z0), (1, 0, 0)),
             "+z": ((x0, y0, z1), (x1 - x0, 0, 0), (0, y1 - y0, 0), (0, 0, 1)),
             "-z": ((x0, y1, z0), (x1 - x0, 0, 0), (0, y0 - y1, 0), (0, 0, -1))}
        faces = faces or {}
        for name, (o, a, b, n) in F.items():
            if name in skip or (name == "-z" and name not in faces):
                continue
            st = step.get(name, step["*"]) if isinstance(step, dict) else step
            col = faces.get(name, color)
            if not callable(col):
                la, lb = np.linalg.norm(a), np.linalg.norm(b)
                col = painted(self.rng, col, la, lb, max(st, 0.008), dirt=0.0 if name[1] == "z" else 1.0)
            self.rect(o, a, b, st, col, n, owner, edge=edge)
        return owner

    def cyl(self, a, b, r, color, step, owner=-1, caps=(False, False), gloss=0.0):
        """A tube of radius r from a to b with discs tangent to it; color is an rgb or f(t metres, angle)."""
        rng = self.rng
        a, b = np.asarray(a, float), np.asarray(b, float)
        axis = b - a
        L = np.linalg.norm(axis)
        axis /= L
        side = np.cross(axis, [0, 0, 1.0] if abs(axis[2]) < 0.9 else [1.0, 0, 0])
        side /= np.linalg.norm(side)
        up = np.cross(axis, side)
        m = max(6, int(np.ceil(2 * np.pi * r / step)))
        t, ang = np.meshgrid(np.arange(min(step, L) / 2, L, step), np.arange(m) * 2 * np.pi / m)
        t, ang = t.ravel(), ang.ravel()
        t = np.clip(t + rng.uniform(-0.4, 0.4, len(t)) * step, 0, L)
        ang = ang + rng.uniform(-0.4, 0.4, len(ang)) * 2 * np.pi / m
        radial = np.outer(np.cos(ang), side) + np.outer(np.sin(ang), up)
        p = a + np.outer(t, axis) + r * radial
        q = qmul(tilt(rng, len(p), 0.04), qmul(qaxis(axis, ang), qmat(np.stack([axis, -up, side], 1))[None]))
        n = len(p)
        sig = np.stack([np.full(n, 0.68 * step), np.full(n, 0.68 * 2 * np.pi * r / m), np.full(n, 0.003)], 1)
        sig *= rng.lognormal(0, 0.12, (n, 1))
        col = color(t, ang) if callable(color) else np.tile(np.asarray(color, float), (n, 1))
        self.add(p, col, radial, sig, q, owner, 1.0, gloss)
        cap = color(np.zeros(1), np.zeros(1))[0] if callable(color) else color
        for end, d, on in ((a, -axis, caps[0]), (b, axis, caps[1])):
            if on:
                self.disk(end, d, 0, r, cap, step, owner)

    def disk(self, c, axis, r0, r1, color, step, owner=-1):
        """An annulus r0..r1 round c facing axis; color is an rgb or f(radius, angle)."""
        rng = self.rng
        c, axis = np.asarray(c, float), np.asarray(axis, float) / np.linalg.norm(axis)
        side = np.cross(axis, [0, 0, 1.0] if abs(axis[2]) < 0.9 else [1.0, 0, 0])
        side /= np.linalg.norm(side)
        up = np.cross(axis, side)
        rings = np.arange(r0 + step / 2, r1, step) if r1 - r0 > step else np.array([(r0 + r1) / 2])
        counts = [max(6, int(np.ceil(2 * np.pi * x / step))) for x in rings]
        rr = np.repeat(rings, counts)
        ang = np.concatenate([np.arange(c) * 2 * np.pi / c for c in counts]) + rng.uniform(0, 2 * np.pi)
        p = c + rr[:, None] * (np.outer(np.cos(ang), side) + np.outer(np.sin(ang), up))
        n = len(p)
        q = qmul(qaxis(axis, rng.uniform(0, np.pi, n)), qmat(np.stack([side, up, axis], 1))[None])
        sig = np.stack([np.full(n, 0.68 * step), np.full(n, 0.68 * step), np.full(n, 0.003)], 1)
        col = color(rr, ang) if callable(color) else np.tile(np.asarray(color, float), (n, 1))
        self.add(p, col, axis, sig, q, owner)

    def tube(self, pts, r, color, step, owner=-1):
        for a, b in zip(pts[:-1], pts[1:]):
            self.cyl(a, b, r, color, step, owner)

    def blobs(self, p, sigma, color, normal=(0, 0, 1.0), emit=False, opacity=None):
        p = np.atleast_2d(np.asarray(p, float))
        self.add(p, np.tile(np.asarray(color, float), (len(p), 1)), normal, np.full((len(p), 3), sigma),
                 np.tile([1.0, 0, 0, 0], (len(p), 1)), emit=emit, opacity=opacity)


# ---------- surfaces ----------
FLOOR_RES = 0.015
MATS = [(-4.75, 1.98, -3.05, 2.56), (-0.85, 1.98, 0.85, 2.56), (3.05, 1.98, 4.75, 2.56), (1.4, 3.62, 2.8, 4.45),
        (-6.55, -3.3, -5.95, 0.3)]                     # rubber mats in front of the switchgear: x0, y0, x1, y1
DRAINS = [(-6.1, -6.7), (7.6, -5.2), (0.7, -6.9), (-1.2, 4.3)]
PAD = ((5.65, -3.75), (7.55, -1.45))                   # the transformer's concrete pad


def floor_maps(rng, feet):
    """The concrete floor's albedo (sRGB 0..1) and gloss; pixel (row, col) covers (y0 + row res, x0 + col res)."""
    x0, x1, y0, y1, _ = ROOM
    r = FLOOR_RES
    H, W = int(round((y1 - y0) / r)), int(round((x1 - x0) / r))
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    x, y = x0 + (x + 0.5) * r, y0 + (y + 0.5) * r
    nz = lambda cell, o=4, **k: fbm(rng, (H, W), cell / r, o, **k)
    lum = 1 + 0.11 * nz(3.0) + 0.06 * nz(0.5) + 0.05 * nz(0.06, 2)
    lum += 0.035 * smooth(0.2, 0.6, nz(1.2, 3))                                       # trowel and sealer patches
    sp = rng.random((H, W), dtype=np.float32)
    lum += 0.11 * (sp < 0.02) - 0.08 * (sp > 0.985)                                    # exposed aggregate
    dj = np.minimum(np.abs((x - x0 + 2.125) % 4.25 - 2.125), np.abs((y - y0 + 2.5) % 5.0 - 2.5))
    lum *= 1 - 0.3 * (dj < 0.6 * r) - 0.05 * np.exp(-dj / 0.04)                         # saw-cut joints
    marks = np.zeros((H, W), np.uint8)
    for _ in range(9):                                                                 # hairline cracks
        p, a, pts = rng.uniform((x0 + 1, y0 + 1), (x1 - 1, y1 - 1)), rng.uniform(0, 2 * np.pi), []
        for _ in range(int(rng.integers(12, 45))):
            pts.append(p)
            a += rng.normal(0, 0.4)
            p = p + 0.05 * np.array([np.cos(a), np.sin(a)])
        cv2.polylines(marks, [((np.array(pts) - (x0, y0)) / r).astype(np.int32)], False, 255, 1, cv2.LINE_AA)
    lum *= 1 - 0.28 * marks / 255
    traffic = np.maximum(np.exp(-((y + 1.0) / 1.5) ** 2) * smooth(7.8, 6.5, np.abs(x)),
                         np.exp(-((x + 2.0) / 0.9) ** 2) * smooth(-2.0, -3.5, y))
    lum += 0.04 * traffic                                                              # polished by feet and trolleys
    marks[:] = 0
    s, t = np.linspace(-7.0, 6.8, 300), np.linspace(0, 1, 200)[:, None]
    for off in (-0.27, 0.27):                                                          # pallet-truck wheel tracks
        lane = np.stack([s, -1.0 + off + 0.22 * np.sin(s / 2.6 + 1) + 0.08 * np.sin(s / 0.9)], 1)
        b0, b1, b2 = np.array([-2.0 + off, -9.7]), np.array([-2.0 + off, -1.0 - off]), np.array([3.0, -1.0 - off])
        for path in (lane, (1 - t) ** 2 * b0 + 2 * (1 - t) * t * b1 + t ** 2 * b2):
            cv2.polylines(marks, [((path - (x0, y0)) / r).astype(np.int32)], False, 255, 3, cv2.LINE_AA)
    lum *= 1 - 0.13 * marks / 255 * smooth(-0.3, 0.5, nz(0.4, 3))
    marks[:] = 0
    cand = rng.uniform((x0 + 0.5, y0 + 0.5), (x1 - 0.5, y1 - 0.5), (3000, 2))
    tr = np.maximum(np.exp(-((cand[:, 1] + 1.0) / 1.5) ** 2), np.exp(-((cand[:, 0] + 2.0) / 0.9) ** 2))
    for cx, cy in cand[rng.random(3000) < 0.06 + tr][:130]:                             # black rubber scuffs
        cv2.ellipse(marks, (int((cx - x0) / r), int((cy - y0) / r)), (int(rng.uniform(6, 25)),) * 2,
                    float(rng.uniform(0, 360)), 0, float(rng.uniform(15, 70)), int(rng.integers(70, 230)),
                    int(rng.integers(1, 3)), cv2.LINE_AA)
    lum *= 1 - 0.35 * marks / 255
    dw = np.minimum(np.minimum(x - x0, x1 - x), np.minimum(y - y0, y1 - y))
    dn = 0.6 + 0.4 * nz(0.2, 2)
    dirt = np.exp(-dw / 0.35) * (0.65 + 0.35 * nz(0.3, 3))                             # grime along the walls
    for lo, hi in feet:                                                                # and round every base
        d = np.hypot(np.maximum(np.maximum(lo[0] - x, x - hi[0]), 0), np.maximum(np.maximum(lo[1] - y, y - hi[1]), 0))
        dirt = np.maximum(dirt, 0.7 * np.exp(-d / 0.18) * dn)
    sn = nz(0.25, 3)
    for cx, cy, rad, k in ((7.7, -1.0, 0.8, 0.22), (-6.1, -6.7, 0.45, 0.2), (0.7, -6.9, 0.9, 0.08),
                           (-3.8, 1.0, 0.7, 0.07), (5.0, -6.0, 1.3, 0.06), (-1.2, 4.3, 0.5, 0.15)):  # oil, water stains
        lum *= 1 - k * smooth(0.0, 0.25, 1 - np.hypot(x - cx, y - cy) / rad + 0.3 * sn)
    col = np.array([0.4, 0.4, 0.385], np.float32) * lum[..., None]
    col *= 1 - 0.3 * dirt[..., None] * np.array([1.0, 1.05, 1.15], np.float32)         # grime is darker and warm
    col *= 1 + 0.025 * nz(4.0, 2)[..., None] * np.array([1, 0, -1], np.float32)
    gloss = (0.75 + 0.35 * traffic) * (1 - 0.6 * dirt)
    ed = 0.005 * nz(0.03, 2)
    line = lambda d, half: smooth(half + 0.003, half - 0.003, d + ed)                  # ragged paint edges
    yel = np.maximum.reduce([line(np.abs(y - 1.9), 0.05) * (np.abs(x) < 7.6),
                             line(np.abs(y + 3.9), 0.05) * (np.abs(x) < 5.4) * ((x < -3.05) | (x > -0.95)),
                             line(np.abs(x + 3.0), 0.05) * (y < -3.85), line(np.abs(x + 1.0), 0.05) * (y < -3.85)])
    wht = line(np.abs(y + 1.0), 0.04) * ((x + 10) % 1.0 < 0.55) * (np.abs(x) < 5.4)
    (px0, py0), (px1, py1) = PAD
    zone = (x > 5.15) & (x < 8.05) & (y > -4.25) & (y < -0.95) & ~((x > px0) & (x < px1) & (y > py0) & (y < py1))
    stripe = ((x + y) % 0.25) < 0.125                                                  # keep-clear hatching
    yel = np.maximum(yel, zone * stripe)
    wear = np.clip(0.95 + 0.5 * nz(0.08, 3) - 0.55 * traffic - 0.3 * zone, 0, 1) * (nz(0.02, 1) > -1.1)
    for paint, mask in ((YELLOW, yel), ((0.82, 0.82, 0.8), wht), ((0.08, 0.08, 0.08), zone * ~stripe)):
        a = (mask * wear)[..., None]
        col = col * (1 - a) + np.asarray(paint, np.float32) * (0.7 + 0.3 * lum[..., None]) * (1 - 0.3 * dirt[..., None]) * a
        gloss = gloss * (1 - a[..., 0]) + 0.55 * a[..., 0]
    m = (x > 0.85) & (x < 1.45) & (y > -3.85) & (y < 2.5)                             # chequer-plate trench cover
    e = np.maximum(np.minimum(np.minimum(x - 0.85, 1.45 - x), np.minimum(y + 3.85, 2.5 - y)), 0)
    plate = 0.4 * (1 + 0.06 * nz(0.4, 2) + 0.05 * (((x + y) / 0.03) % 1 < 0.35)) * (1 - 0.35 * ((y + 3.85) % 1.0 < r))
    plate *= 1 - 0.3 * np.exp(-e / 0.015)
    col[m] = plate[m][:, None] * np.array([1.0, 0.99, 0.95]) * (1 - 0.2 * dirt[m][:, None])
    gloss[m] = 1.0
    for mx0, my0, mx1, my1 in MATS:                                                    # black rubber mats
        m = (x > mx0) & (x < mx1) & (y > my0) & (y < my1)
        e = np.maximum(np.minimum(np.minimum(x - mx0, mx1 - x), np.minimum(y - my0, my1 - y)), 0)
        mc = 0.075 * (1 + 0.2 * nz(0.15, 2)) + 0.05 * smooth(0.025, 0.0, e) + 0.04 * smooth(0.0, 0.8, nz(0.3, 3))
        col[m] = mc[m][:, None] * np.array([1.0, 1.0, 1.04])
        gloss[m] = 0.12
    for cx, cy in DRAINS:                                                              # floor drain grates
        m = (np.abs(x - cx) < 0.15) & (np.abs(y - cy) < 0.15)
        inner = (np.abs(x - cx) < 0.12) & (np.abs(y - cy) < 0.12)
        slot = ((y - cy + 0.12) % 0.03) < 0.015
        col[m] = (0.3, 0.29, 0.27)
        col[inner & slot] = (0.035, 0.035, 0.035)
        col[inner & ~slot] = (0.24, 0.21, 0.18)
        gloss[m] = 0.5
    return np.clip(col, 0, 1).astype(np.float32), np.clip(gloss, 0, 1.2).astype(np.float32)


def wall_map(rng, length, height, res=0.02):
    """Painted block wall: grey dado and dark skirting, joints, patch repairs, streaks, splash dirt and scuffs."""
    H, W = int(round(height / res)), int(round(length / res))
    v = height - (np.arange(H, dtype=np.float32)[:, None] + 0.5) * res
    u = (np.arange(W, dtype=np.float32)[None, :] + 0.5) * res
    nz = lambda cell, o=4, **k: fbm(rng, (H, W), cell / res, o, **k)
    row = np.floor(v / 0.2).astype(int)
    off = (row % 2) * 0.2
    col_i = np.floor((u + off) / 0.4).astype(int)
    tone = rng.normal(0, 0.012, (row.max() + 2, col_i.max() + 2)).astype(np.float32)
    lum = 1 + tone[row, col_i] + 0.022 * nz(1.0, 3) + 0.014 * nz(0.04, 2)
    joint = (np.abs((v + 0.1) % 0.2 - 0.1) < 0.55 * res) | (np.abs((u + off + 0.2) % 0.4 - 0.2) < 0.55 * res)
    lum *= (1 - 0.045 * joint) * (0.93 + 0.07 * smooth(0.0, 3.5, v))                 # and a gentle vertical gradient
    base = np.where((v < 0.12)[..., None], SKIRT, np.where((v < 1.15)[..., None], DADO, WALL)).astype(np.float32)
    col = base * lum[..., None]
    for _ in range(int(rng.integers(2, 5))):                                           # patch repairs, repainted
        a0, b0 = rng.uniform(0, length - 1.5), rng.uniform(1.3, height - 1.2)
        m = (u > a0) & (u < a0 + rng.uniform(0.4, 1.4)) & (v > b0) & (v < b0 + rng.uniform(0.3, 1.0))
        col *= np.where(m[..., None], np.float32(rng.uniform(0.96, 1.03)), np.float32(1))
    scuff = np.zeros((H, W), np.uint8)
    for _ in range(int(length * 2.5)):
        a0, b0 = rng.uniform(0, length), rng.uniform(0.15, 0.95)
        cv2.line(scuff, (int(a0 / res), int((height - b0) / res)),
                 (int((a0 + rng.uniform(0.05, 0.5)) / res), int((height - b0 - rng.uniform(-0.03, 0.03)) / res)),
                 int(rng.integers(60, 200)), 1, cv2.LINE_AA)
    streak = np.clip(nz(0.25, 3, stretch=(10, 0.3)) - 0.6, 0, 1) * smooth(0.2, 0.9, nz(2.5, 2)) * smooth(1.0, 3.8, v)
    splash = np.exp(-v / 0.3) * (0.6 + 0.4 * nz(0.15, 3))
    stain = smooth(0.3, 0.7, nz(0.8, 3)) * smooth(height - 1.2, height, v)
    blotch = smooth(0.1, 0.9, nz(0.6, 3)) * (0.5 + 0.5 * smooth(0.0, 2.0, v))
    dirt = np.clip(0.3 * streak + 0.25 * splash + 0.12 * stain + 0.06 * blotch, 0, 0.6)
    col *= 1 - dirt[..., None] * np.array([1.0, 1.06, 1.18], np.float32)
    col *= 1 - 0.3 * scuff[..., None] / 255
    return np.clip(col, 0, 1)


def ceiling_map(rng, res=0.04):
    x0, x1, y0, y1, _ = ROOM
    H, W = int(round((y1 - y0) / res)), int(round((x1 - x0) / res))
    y = y0 + (np.arange(H, dtype=np.float32)[:, None] + 0.5) * res
    nz = lambda cell, o=4: fbm(rng, (H, W), cell / res, o)
    lum = (1 + 0.03 * nz(1.5, 3) + 0.015 * nz(0.12, 2)) * (1 + 0.012 * np.sign(np.sin(2 * np.pi * y / 0.6)))
    lum *= 1 - 0.1 * smooth(0.45, 0.85, nz(1.0, 3))                                   # old water stains
    return np.clip(np.array([0.74, 0.74, 0.73], np.float32) * lum[..., None], 0, 1)


# ---------- equipment ----------
def cabinet_front(rng, asset, w, h):
    """Steel door of an inverter cabinet: gap, hinges, handle, louvres, vent, plate, stickers and an e-stop."""
    cv = Canvas(w, h, 0.004, STEEL)
    dark = (0.24, 0.25, 0.26)
    cv.frame(0.022, 0.06, w - 0.022, h - 0.02, dark, 0.004)
    for v in (0.35, h - 0.45):
        cv.fill(0.006, v, 0.022, v + 0.12, (0.42, 0.43, 0.43))
    cv.fill(w - 0.1, 0.9, w - 0.065, 1.12, (0.16, 0.16, 0.17))
    cv.circle(w - 0.083, 1.01, 0.028, (0.28, 0.28, 0.29))
    louvres(cv, 0.2, 0.12, w - 0.2, 0.52, STEEL)
    cv.fill(0.3, h - 0.21, w - 0.3, h - 0.09, (0.5, 0.51, 0.51))  # mesh vent, seen as a soft grey at any distance
    cv.frame(0.3, h - 0.21, w - 0.3, h - 0.09, (0.36, 0.37, 0.37), 0.008)
    label(cv, asset, w / 2 - 0.22, 1.62, 0.44, 0.13)
    hazard(cv, 0.13, 1.62, 0.15)
    cv.fill(w - 0.28, 1.62, w - 0.13, 1.76, (0.86, 0.7, 0.14))
    cv.circle(w - 0.205, 1.69, 0.045, (0.5, 0.06, 0.05))
    cv.circle(w - 0.21, 1.695, 0.03, (0.72, 0.12, 0.09))
    cv.fill(w - 0.38, 0.6, w - 0.12, 0.74, (0.72, 0.73, 0.72))
    for k in range(5):
        cv.fill(w - 0.36, 0.615 + 0.024 * k, w - 0.36 + rng.uniform(0.1, 0.22), 0.623 + 0.024 * k, (0.3, 0.3, 0.3))
    weather(cv, rng, dirt=0.8, streaks=0.5, mottle=0.5)
    m = np.zeros(cv.img.shape[:2], np.float32)                                       # hands round the handle
    cv2.circle(m, cv.px(w - 0.085, 1.0), int(0.1 / cv.rx), 1.0, -1)
    cv.blend(cv2.GaussianBlur(m, (0, 0), 0.04 / cv.rx), (0.32, 0.31, 0.3), 0.18)
    return cv


def inverter(sc, asset, lo, hi):
    """An inverter cabinet on its plinth, its photographed power unit standing proud of the door."""
    rng = sc.rng
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    w, _, h = hi - lo
    cx = (lo[0] + hi[0]) / 2
    sc.box((lo[0] - 0.06, lo[1] - 0.06, 0), (hi[0] + 0.06, hi[1] + 0.06, 0.07), 0.02, color=CONCRETE, edge=0.2)
    sc.box(lo, hi, {"-y": 0.012, "+y": 0.035, "*": 0.024}, {"-y": cabinet_front(rng, asset, w, h)}, color=STEEL)
    rel, quad, uw, aspect = UNITS[asset]
    uh, zc, depth = uw / aspect, 1.24, 0.15
    img = photo(rel, quad, int(round(uw / 0.003)), int(round(uh / 0.003)))
    ring = np.concatenate([img[:6].reshape(-1, 3), img[-6:].reshape(-1, 3), img[:, :6].reshape(-1, 3),
                           img[:, -6:].reshape(-1, 3)])
    unit = weather(Canvas(uw, uh, 0.003, img=img), rng, dirt=0.25, streaks=0.2, mottle=0.4)
    sc.box((cx - uw / 2, lo[1] - depth, zc - uh / 2), (cx + uw / 2, lo[1], zc + uh / 2), {"-y": 0.008, "*": 0.014},
           {"-y": unit}, color=np.median(ring, 0) * 0.9, skip=("+y",))
    for k in (-0.16, 0.0, 0.16):  # cables from the unit's underside, bending into glands on the door
        b = zc - uh / 2
        sc.tube([(cx + k, lo[1] - 0.06, b), (cx + k, lo[1] - 0.05, b - 0.07), (cx + k, lo[1] - 0.004, b - 0.11)],
                0.007, (0.1, 0.1, 0.11), 0.006)
        sc.blobs((cx + k, lo[1] - 0.006, b - 0.11), 0.011, (0.2, 0.2, 0.21), normal=(0, -1.0, 0))


def transformer(sc, lo, hi):
    """The pad-mounted transformer: its doors and base are the photo, on a concrete pad with bollards."""
    rng = sc.rng
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    w, h = hi[1] - lo[1], hi[2] - lo[2]
    (px0, py0), (px1, py1) = PAD
    sc.box((px0, py0, 0), (px1, py1, lo[2]), 0.02, color=CONCRETE, edge=0.25)
    cv = Canvas(w, h, 0.004, 0.3)
    base_h, lid_h = 0.34, 0.05
    doors = photo(XFMR_PHOTO, [(147, 192), (912, 192), (912, 548), (147, 548)], cv.img.shape[1],
                  int(round((h - base_h - lid_h) / cv.ry)))
    cv.paste(doors, 0, base_h, w, h - lid_h)
    cv.paste(photo(XFMR_PHOTO, [(181, 548), (866, 548), (866, 678), (181, 678)], cv.img.shape[1],
                   int(round(base_h / cv.ry))), 0, 0, w, base_h)
    green = np.median(doors[doors.shape[0] // 2:, : doors.shape[1] // 3].reshape(-1, 3), 0)
    cv.fill(0, h - lid_h, w, h, green * 0.85)
    label(cv, "XFMR-1", 0.06, 1.07, 0.46, 0.14)  # over the photo's hand-written number
    weather(cv, rng, dirt=0.6, streaks=0.2, mottle=0.4)
    sc.box(lo, hi, {"-x": 0.01, "*": 0.024}, {"-x": cv}, color=green)
    sc.box(lo - (0.025, 0.025, 0) + (0, 0, h - 0.05), hi + (0.025, 0.025, 0.015), 0.02, color=green * 1.08,
           casts=False, feet=False)  # the lid's lip
    for bx, by in ((lo[0] - 0.55, lo[1] - 0.35), (lo[0] - 0.55, hi[1] + 0.35), (hi[0] + 0.42, lo[1] - 0.35),
                   (hi[0] + 0.42, hi[1] + 0.35)):
        bollard(sc, bx, by)


def bollard(sc, x, y, r=0.07, h=1.0):
    own = sc.solid((x - r, y - r, 0), (x + r, y + r, h))
    band = lambda t, a: np.where(((t > 0.74) & (t < 0.8)) | ((t > 0.86) & (t < 0.92)), 0.07, 1.0)[:, None] * \
        np.asarray(YELLOW) * (0.95 + 0.05 * np.cos(a))[:, None]
    sc.cyl((x, y, 0), (x, y, h), r, band, 0.018, own, caps=(False, True))


def battery(sc, lo, hi):
    """The battery enclosure: five white doors with louvres, handles, status windows and hazard stickers."""
    rng = sc.rng
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    w, h = hi[1] - lo[1], hi[2] - lo[2]
    white = (0.85, 0.86, 0.85)
    cv = Canvas(w, h, 0.006, white)
    cv.fill(0, h - 0.15, w, h, (0.22, 0.23, 0.25))
    cv.fill(0, 0, w, 0.08, (0.2, 0.2, 0.21))
    for k in range(5):
        u0 = k * 0.8
        cv.frame(u0, 0.08, u0 + 0.8, h - 0.15, (0.35, 0.36, 0.37), 0.006)
        louvres(cv, u0 + 0.15, 0.16, u0 + 0.65, 0.46, white)
        louvres(cv, u0 + 0.15, 1.95, u0 + 0.65, 2.3, white)
        cv.fill(u0 + 0.69, 0.95, u0 + 0.72, 1.35, (0.62, 0.63, 0.64))
        cv.fill(u0 + 0.3, 1.55, u0 + 0.5, 1.63, (0.08, 0.1, 0.12))
        cv.circle(u0 + 0.47, 1.59, 0.008, (0.3, 0.85, 0.4))
        hazard(cv, u0 + 0.08, 1.72, 0.12)
    label(cv, "BESS-1", 0.25, 1.75, 0.42, 0.13)
    notice(cv, rng, 0.98, 1.4, 0.34, 0.2, "DANGER", (0.75, 0.1, 0.08))
    notice(cv, rng, 1.78, 1.4, 0.34, 0.2, "LI-ION", (0.1, 0.1, 0.1))
    weather(cv, rng, dirt=1.0, streaks=0.8)
    sc.box(lo, hi, {"+x": 0.015, "-x": 0.04, "*": 0.03}, {"+x": cv}, color=white)


def breaker(sc, asset, lo, hi):
    """The 52-I4 breaker panel on a painted backboard, its operating handle proud of the door."""
    rng = sc.rng
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    w, h = hi[0] - lo[0], hi[2] - lo[2]
    y1 = ROOM[3]
    sc.rect((lo[0] - 0.12, y1 - 0.018, lo[2] - 0.12), (w + 0.24, 0, 0), (0, 0, h + 0.24), 0.02,
            painted(rng, (0.56, 0.56, 0.53), w + 0.24, h + 0.24, 0.01), (0, -1, 0), edge=0.25)
    cv = Canvas(w, h, 0.004, GREY)
    cv.frame(0.02, 0.02, w - 0.02, h - 0.02, (0.3, 0.31, 0.32), 0.004)
    cv.fill(0.22, 0.68, 0.42, 1.02, (0.12, 0.12, 0.13))
    cv.fill(0.56, 0.92, 0.74, 1.04, (0.1, 0.1, 0.1))
    cv.fill(0.58, 0.94, 0.65, 1.02, (0.15, 0.62, 0.25))
    cv.fill(0.67, 0.94, 0.72, 1.02, (0.5, 0.08, 0.06))
    cv.fill(0.44, 0.74, 0.5, 0.84, (0.85, 0.68, 0.1))
    cv.fill(w - 0.07, 0.8, w - 0.045, 1.0, (0.2, 0.2, 0.21))
    label(cv, asset, w / 2 - 0.2, h - 0.2, 0.4, 0.12)
    notice(cv, rng, 0.5, 1.15, 0.42, 0.3, "WARNING", (0.9, 0.45, 0.08), (0.05, 0.05, 0.05))
    hazard(cv, 0.1, 1.2, 0.18)
    weather(cv, rng, dirt=0.8, streaks=0.5)
    sc.box(lo, hi, {"-y": 0.01, "*": 0.018}, {"-y": cv}, color=GREY)
    sc.box((lo[0] + 0.28, lo[1] - 0.07, lo[2] + 0.72), (lo[0] + 0.36, lo[1], lo[2] + 0.98), 0.008, color=BLACK,
           casts=False)
    for x in (lo[0] + 0.25, hi[0] - 0.25):  # conduits up to the tray and down to the floor
        sc.cyl((x, 4.7, hi[2]), (x, 4.7, 3.15), 0.028, GALV, 0.015)
        sc.cyl((x, 4.75, 0.0), (x, 4.75, lo[2]), 0.028, GALV, 0.015)


def combiner(sc, asset, lo, hi):
    rng = sc.rng
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    w, h = hi[1] - lo[1], hi[2] - lo[2]
    cv = Canvas(w, h, 0.003, (0.74, 0.75, 0.73))
    cv.frame(0.015, 0.015, w - 0.015, h - 0.015, (0.45, 0.46, 0.46), 0.003)
    for v in (0.15, h - 0.15):
        cv.fill(w - 0.05, v - 0.03, w - 0.025, v + 0.03, (0.18, 0.18, 0.19))
    label(cv, asset, w / 2 - 0.17, h - 0.15, 0.34, 0.1)
    notice(cv, rng, w / 2 - 0.13, 0.25, 0.26, 0.16, "DC 1000V", (0.85, 0.65, 0.1), (0.05, 0.05, 0.05))
    weather(cv, rng, dirt=0.5, streaks=0.6)
    sc.box(lo, hi, {"-x": 0.006, "*": 0.012}, {"-x": cv}, color=(0.74, 0.75, 0.73))
    for z in (lo[2] + 0.1, hi[2] - 0.1):  # mounting rails
        sc.box((hi[0], lo[1] - 0.05, z - 0.02), (ROOM[1], hi[1] + 0.05, z + 0.02), 0.01, color=GALV, casts=False)
    for y in (lo[1] + 0.15, hi[1] - 0.15):  # conduits down the wall to the floor and up to the tray
        sc.cyl((8.44, y, 0.0), (8.44, y, lo[2]), 0.022, GALV, 0.014)
    sc.cyl((8.44, (lo[1] + hi[1]) / 2, hi[2]), (8.44, (lo[1] + hi[1]) / 2, 3.1), 0.022, GALV, 0.014)


def tray(sc, a, b, width, z, cables=6, rail=0.1, step=0.026):
    """A ladder cable tray from a to b (x, y) with its bottom at z, cable bundles on the rungs, rods to the ceiling."""
    rng = sc.rng
    a, b = np.array([*a, z], float), np.array([*b, z], float)
    d = b - a
    L = np.linalg.norm(d)
    d /= L
    side = np.array([-d[1], d[0], 0.0])
    k = int(np.argmax(np.abs(side)))
    lo, hi = np.minimum(a, b) - np.abs(side) * width / 2, np.maximum(a, b) + np.abs(side) * width / 2 + (0, 0, rail)
    own = sc.solid(lo, hi, feet=False)
    zinc = lambda u, v: np.asarray(GALV) * (1 + 0.06 * np.sin(u * 37.0) * np.cos(v * 91.0))[:, None]
    for s in (-1, 1):
        o = a + side * s * width / 2
        sc.rect(o, d * L, (0, 0, rail), step, zinc, side * s, own)
        sc.rect(o + (0, 0, rail), d * L, -side * s * 0.02, step, zinc, (0, 0, 1), own)
    for t in np.arange(0.15, L, 0.3):
        c = a + d * t
        sc.rect(c - side * width / 2 - d * 0.015, side * width, d * 0.03, 0.015, GALV, (0, 0, -1), own)
        for sd in (-1, 1):
            sc.rect(c - side * width / 2 + d * 0.015 * sd, side * width, (0, 0, 0.02), 0.015, GALV, d * sd, own)
    for j in range(cables):
        r = rng.uniform(0.012, 0.022)
        off = (j + 0.5) / cables - 0.5
        col = (0.45, 0.1, 0.08) if j == 2 else (0.15, 0.16, 0.18) if j % 3 == 1 else BLACK
        ts = np.linspace(0, L, max(2, int(L / 0.8)) + 1)
        wob = rng.uniform(0, 6.3)
        pts = [a + d * t + side * (off * (width - 0.06) + 0.012 * np.sin(t * 1.3 + wob)) + (0, 0, 0.02 + r) for t in ts]
        sc.tube(pts, r, col, 0.022, own)
    for t in np.arange(0.5, L, 3.0):  # threaded rods, dull with dust
        for s in (-1, 1):
            c = a + d * t + side * s * (width / 2 + 0.015)
            sc.cyl(c, (c[0], c[1], ROOM[4]), 0.006, (0.4, 0.4, 0.39), 0.012)


def pipe_run(sc, a, b, r, color, flanges=(), step=0.026):
    """A painted pipe with flange pairs and their bolts at the given distances along it."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = (b - a) / np.linalg.norm(b - a)
    shade = lambda t, ang: np.asarray(color) * (0.97 + 0.06 * np.sin(t * 0.7 + ang))[:, None]
    sc.cyl(a, b, r, shade, step)
    side = np.cross(d, [0, 0, 1.0] if abs(d[2]) < 0.9 else [1.0, 0, 0])
    side /= np.linalg.norm(side)
    up = np.cross(d, side)
    for t in flanges:
        c = a + d * t
        sc.cyl(c - d * 0.03, c + d * 0.03, r + 0.045, np.asarray(color) * 0.9, 0.015, caps=(True, True))
        ang = np.arange(8) * np.pi / 4
        ring = c + (r + 0.03) * (np.outer(np.cos(ang), side) + np.outer(np.sin(ang), up))
        sc.blobs(np.concatenate([ring - d * 0.04, ring + d * 0.04]), 0.008, (0.25, 0.24, 0.22))


def valve(sc, c, r, wheel=(0.66, 0.1, 0.08)):
    """A gate valve in a vertical pipe at c: body, bonnet, stem and a red handwheel facing -y."""
    c = np.asarray(c, float)
    sc.cyl(c - (0, 0, 0.13), c + (0, 0, 0.13), r + 0.035, (0.32, 0.33, 0.33), 0.012, caps=(True, True))
    sc.cyl(c, c - (0, 0.24, 0), 0.03, (0.32, 0.33, 0.33), 0.01, caps=(False, True))
    sc.cyl(c - (0, 0.24, 0), c - (0, 0.34, 0), 0.012, (0.6, 0.6, 0.6), 0.008)
    ang = np.linspace(0, 2 * np.pi, 70, endpoint=False)
    hub = c - (0, 0.33, 0)
    rim = hub + 0.15 * np.stack([np.cos(ang), np.zeros_like(ang), np.sin(ang)], 1)
    spokes = np.concatenate([hub + np.outer(np.linspace(0.02, 0.14, 8), (np.cos(t), 0, np.sin(t))) for t in
                             np.arange(4) * np.pi / 2 + 0.4])
    sc.blobs(np.concatenate([rim, spokes]), 0.011, wheel, normal=(0, -1.0, 0))


def wall_box(sc, wall, s0, z0, w, h, depth, front, color=GREY, step=0.008):
    """A box mounted on a wall: s0 is where its left edge starts along the wall, seen from inside the room."""
    x0, x1, y0, y1, _ = ROOM
    lo, hi, face = {"left": ((x0, s0, z0), (x0 + depth, s0 + w, z0 + h), "+x"),
                    "right": ((x1 - depth, -s0 - w, z0), (x1, -s0, z0 + h), "-x"),
                    "back": ((s0, y1 - depth, z0), (s0 + w, y1, z0 + h), "-y"),
                    "front": ((-s0 - w, y0, z0), (-s0, y0 + depth, z0 + h), "+y")}[wall]
    return sc.box(lo, hi, {face: step, "*": max(step, 0.014)}, {face: front} if front is not None else None,
                  color=color, feet=False)


def panel_canvas(rng, w, h, color, text, plate=(0.93, 0.93, 0.9), fg=(0.05, 0.05, 0.05)):
    cv = Canvas(w, h, 0.004, color)
    cv.frame(0.012, 0.012, w - 0.012, h - 0.012, np.asarray(color) * 0.55, 0.003)
    label(cv, text, w / 2 - min(0.16, 0.4 * w), h - 0.1, min(0.32, 0.8 * w), 0.07, plate, fg)
    cv.fill(w - 0.05, h / 2 - 0.04, w - 0.035, h / 2 + 0.04, (0.18, 0.18, 0.19))
    return weather(cv, rng, dirt=0.3, streaks=0.5)


def sign(sc, wall, s0, z0, w, h, cv, emit=False, proud=0.004):
    """A flat sign on a wall (front wall: s0 measured like wall_box)."""
    x0, x1, y0, y1, _ = ROOM
    o, a, n = {"left": ((x0 + proud, s0, z0), (0, w, 0), (1, 0, 0)), "right": ((x1 - proud, -s0, z0), (0, -w, 0), (-1, 0, 0)),
               "back": ((s0, y1 - proud, z0), (w, 0, 0), (0, -1, 0)), "front": ((-s0, y0 + proud, z0), (-w, 0, 0), (0, 1, 0))}[wall]
    sc.rect(o, a, (0, 0, h), min(0.008, w / 20), cv, n, emit=emit)


def exit_sign(sc, c, facing):
    """A lit green exit sign centred at c, facing +-y (both ways when facing is 0)."""
    cv = Canvas(0.36, 0.15, 0.002, (0.08, 0.62, 0.3))
    cv.text("EXIT", 0.05, 0.02, 0.27, 0.13, (0.95, 1.0, 0.95), fill=0.7)
    cv.poly([(0.29, 0.045), (0.34, 0.075), (0.29, 0.105)], (0.95, 1.0, 0.95))
    c = np.asarray(c, float)
    own = sc.solid(c - (0.18, 0.025, 0.075), c + (0.18, 0.025, 0.075), casts=False, feet=False)
    for s in ((1, -1) if facing == 0 else (facing,)):
        sc.rect(c + (0.18 * s, 0.026 * s, -0.075), (-0.36 * s, 0, 0), (0, 0, 0.15), 0.006, cv, (0, s, 0), own, emit=True)
    sc.box(c - (0.19, 0.024, 0.085), c + (0.19, 0.024, 0.085), 0.01, color=(0.85, 0.85, 0.83), owner=own,
           skip=("-y", "+y"))


def extinguisher(sc, x, y, z0=0.5):
    """A red extinguisher on a front-wall bracket, with its sign above."""
    rng = sc.rng
    r, c = 0.085, (x, y + 0.11)
    own = sc.solid((x - r, y + 0.02, z0), (x + r, y + 0.2, z0 + 0.62), feet=False)
    body = lambda t, a: np.where(((t > 0.3) & (t < 0.42) & (np.sin(a) > 0.2))[:, None], (0.92, 0.92, 0.88),
                                 (0.68, 0.08, 0.06))
    sc.cyl((*c, z0), (*c, z0 + 0.5), r, body, 0.01, own, caps=(True, True))
    sc.cyl((*c, z0 + 0.5), (*c, z0 + 0.56), 0.022, (0.15, 0.15, 0.15), 0.008)
    sc.box((x - 0.07, c[1] - 0.012, z0 + 0.56), (x + 0.06, c[1] + 0.012, z0 + 0.6), 0.006, color=(0.12, 0.12, 0.12),
           owner=own)
    sc.tube([(x + 0.03, c[1] - 0.02, z0 + 0.55), (x + 0.1, c[1] - 0.05, z0 + 0.5), (x + 0.1, c[1] - 0.06, z0 + 0.15)],
            0.009, BLACK, 0.008, own)
    sc.box((x - 0.05, y, z0 + 0.3), (x + 0.05, y + 0.03, z0 + 0.36), 0.008, color=(0.2, 0.2, 0.2), owner=own)
    cv = Canvas(0.2, 0.2, 0.003, (0.72, 0.1, 0.08))
    cv.fill(0.075, 0.03, 0.125, 0.14, (0.95, 0.95, 0.95))
    cv.fill(0.085, 0.14, 0.115, 0.165, (0.95, 0.95, 0.95))
    cv.poly([(0.115, 0.155), (0.16, 0.17), (0.16, 0.155)], (0.95, 0.95, 0.95))
    sign(sc, "front", -x - 0.1, z0 + 0.8, 0.2, 0.2, weather(cv, rng, dirt=0, streaks=0.2, mottle=0.3))


def door_canvas(rng, w, h):
    cv = Canvas(w, h, 0.005, (0.3, 0.31, 0.32))
    leaf = (0.52, 0.55, 0.57)
    for u0 in (0.06, w / 2 + 0.005):
        u1 = u0 + w / 2 - 0.065
        cv.fill(u0, 0.0, u1, h - 0.06, leaf)
        cv.fill(u0 + 0.12, 1.25, u1 - 0.12, 1.85, (0.12, 0.14, 0.16))
        cv.fill(u0 + 0.14, 1.6, u1 - 0.3, 1.82, (0.22, 0.25, 0.28))
        cv.fill(u0, 0.0, u1, 0.25, (0.6, 0.61, 0.6))
        cv.fill(u0 + 0.04, 0.98, u1 - 0.04, 1.03, (0.68, 0.69, 0.69))
        for v in (0.3, 1.1, 1.95):
            cv.fill(u0 - 0.008 if u0 < w / 2 else u1 - 0.004, v, u0 + 0.004 if u0 < w / 2 else u1 + 0.008, v + 0.1,
                    (0.62, 0.62, 0.6))
    notice(cv, rng, w / 2 + 0.25, 1.92, 0.3, 0.14, "KEEP SHUT", (0.12, 0.3, 0.62))
    return weather(cv, rng, dirt=1.2, streaks=0.4)


def shutter_canvas(rng, w, h):
    cv = Canvas(w, h, 0.01, GALV)
    v = h - (np.arange(cv.img.shape[0])[:, None] + 0.5) * cv.ry
    ph = (v % 0.075) / 0.075
    cv.img *= (0.78 + 0.3 * np.sin(np.pi * ph) ** 0.6 - 0.25 * (ph < 0.08))[..., None]
    cv.fill(0, 0, w, 0.08, (0.3, 0.31, 0.32))
    cv.frame(0, 0, w, h, (0.32, 0.33, 0.34), 0.06)
    return weather(cv, rng, dirt=1.4, streaks=1.0)


def shelving(sc, x0, y0, x1, y1, z1=2.0):
    """Boltless steel shelving with spare-part boxes and bins."""
    rng = sc.rng
    sc.solid((x0, y0, 0), (x1, y1, z1), feet=False)
    blue = (0.2, 0.3, 0.5)
    for y in (y0, (y0 + y1) / 2, y1 - 0.04):
        for x in (x0, x1 - 0.04):
            sc.box((x, y, 0), (x + 0.04, y + 0.04, z1), 0.015, color=blue)
    for z in (0.12, 0.75, 1.38, z1 - 0.03):
        sc.box((x0, y0, z), (x1, y1, z + 0.03), 0.03, color=GALV, faces={"-z": GALV}, casts=False, feet=False)
        y = y0 + 0.05
        while z < z1 - 0.1 and y < y1 - 0.25:
            bw, bd, bh = rng.uniform(0.25, 0.6), rng.uniform(0.35, 0.55), rng.uniform(0.15, 0.45)
            kind = rng.integers(0, 4)
            col = [(0.56, 0.43, 0.29), (0.56, 0.43, 0.29), (0.2, 0.35, 0.62), (0.4, 0.42, 0.42)][kind]
            front = None
            if kind < 2:
                cv = painted(rng, col, bw, bh, 0.006, dirt=0.2)
                cv.fill(bw * 0.2, bh * 0.45, bw * 0.6, bh * 0.75, (0.92, 0.92, 0.9))
                cv.fill(0, bh - 0.02, bw, bh, np.asarray(col) * 0.75)
                front = {"+x": cv}
            sc.box((x1 - 0.02 - bd, y, z + 0.03), (x1 - 0.02, y + bw, z + 0.03 + bh), 0.02, front, color=col,
                   casts=False, feet=False)
            y += bw + rng.uniform(0.02, 0.12)


def drum(sc, x, y, R=0.6, w=0.62):
    """A wooden cable drum on its rim, axis along x, wound with black cable."""
    rng = sc.rng
    own = sc.solid((x - w / 2, y - R, 0), (x + w / 2, y + R, 2 * R))
    c = np.array([x, y, R])
    wood = lambda rr, a: np.asarray((0.5, 0.37, 0.24)) * (0.85 + 0.15 * np.sin(rr * 60 + np.cos(a) * 3))[:, None]
    for s in (-1, 1):
        sc.disk(c + (s * w / 2, 0, 0), (s, 0, 0), 0, R, wood, 0.018, own)
        sc.disk(c + (s * (w / 2 - 0.04), 0, 0), (-s, 0, 0), 0.43, R, wood, 0.02, own)
        sc.cyl(c + (s * w / 2, 0, 0), c + (s * (w / 2 - 0.04), 0, 0), R, wood, 0.018, own)
    sc.cyl(c - (w / 2 - 0.04, 0, 0), c + (w / 2 - 0.04, 0, 0), 0.43,
           lambda t, a: np.full((len(t), 3), 0.07) * (0.8 + 0.4 * (np.sin(t * 160) > 0))[:, None], 0.014, own)
    sc.blobs(c + (w / 2 + 0.01, 0, 0), 0.03, (0.12, 0.12, 0.12))


def pallet(sc, x, y):
    rng = sc.rng
    sc.box((x - 0.6, y - 0.4, 0), (x + 0.6, y + 0.4, 0.14), 0.02, color=(0.55, 0.45, 0.32))
    cv = painted(rng, (0.82, 0.83, 0.82), 1.1, 0.9, 0.01, dirt=0.3, streaks=0.2)
    cv.img *= (1 + 0.08 * fbm(rng, cv.img.shape[:2], 6, 3, stretch=(3, 0.5)))[..., None]  # shrink-wrap sheen
    sc.box((x - 0.55, y - 0.37, 0.14), (x + 0.55, y + 0.37, 1.04), 0.018, {"+y": cv, "-y": cv}, color=(0.8, 0.81, 0.8))


# ---------- assembly ----------
def build(board: Board) -> Scene:
    sc = Scene()
    rng = sc.rng
    x0, x1, y0, y1, h = ROOM
    for asset, (lo, hi) in EQUIPMENT.items():
        {"INV": lambda: inverter(sc, asset, lo, hi), "XFM": lambda: transformer(sc, lo, hi),
         "BES": lambda: battery(sc, lo, hi), "CB-": lambda: combiner(sc, asset, lo, hi),
         "52-": lambda: breaker(sc, asset, lo, hi)}[asset[:3]]()
    shelving(sc, x0 + 0.05, -9.6, x0 + 0.65, -6.6)
    drum(sc, 2.8, -7.6)
    pallet(sc, 5.6, -8.9)
    bollard(sc, -6.0, -3.9)
    bollard(sc, -6.0, 0.9)

    # floor, with a hole where the printed board lies at the origin and none under anything standing on it
    feet = [sc.boxes[i] for i in sc.feet]
    alb, gl = floor_maps(rng, feet)
    w_mm, h_mm = board.size_mm
    m = (w_mm / 2 + 20) / 1000, (h_mm / 2 + 20) / 1000

    def keep(p):
        ok = ~((np.abs(p[:, 0]) < m[0]) & (np.abs(p[:, 1]) < m[1]))
        for lo, hi in feet:
            ok &= ~((p[:, 0] > lo[0] + 0.01) & (p[:, 0] < hi[0] - 0.01) & (p[:, 1] > lo[1] + 0.01) & (p[:, 1] < hi[1] - 0.01))
        return ok
    r = FLOOR_RES
    sc.rect((x0, y0, 0), (x1 - x0, 0, 0), (0, y1 - y0, 0), FLOOR_STEP, lambda u, v: bilinear(alb, u / r - 0.5, v / r - 0.5),
            (0, 0, 1), keep=keep, gloss=lambda u, v: bilinear(gl, u / r - 0.5, v / r - 0.5))
    px = 5
    tex = board.image(px, 12.0)
    q, _, _ = grid((-m[0], -m[1], 0.0005), (2 * m[0], 0, 0), (0, 2 * m[1], 0), 0.002)
    col = ((q[:, 0] * 1000 + w_mm / 2 + 12) * px).astype(int)
    row = ((-q[:, 1] * 1000 + h_mm / 2 + 12) * px).astype(int)
    on = (col >= 0) & (col < tex.shape[1]) & (row >= 0) & (row < tex.shape[0])
    c = bilinear(alb, (q[:, 0] - x0) / r - 0.5, (q[:, 1] - y0) / r - 0.5)
    c[on] = (tex[row[on], col[on]][:, None] / 255.0) * 0.9 + 0.03  # paper white, toner black
    sig = np.full((len(q), 3), 0.62 * 0.002)
    sig[:, 2] = 0.001
    sc.add(q, c, (0, 0, 1), sig, np.tile([1.0, 0, 0, 0], (len(q), 1)), opacity=0.995)

    # walls (u left to right seen from inside), ceiling with its beams and light panels
    for o, a, n in (((x0, y1, 0), (x1 - x0, 0, 0), (0, -1, 0)), ((x1, y0, 0), (x0 - x1, 0, 0), (0, 1, 0)),
                    ((x0, y0, 0), (0, y1 - y0, 0), (1, 0, 0)), ((x1, y1, 0), (0, y0 - y1, 0), (-1, 0, 0))):
        L = np.linalg.norm(a)
        sc.rect(o, a, (0, 0, h), WALL_STEP, Canvas(L, h, 0, img=wall_map(rng, L, h)), n)
    ceil = ceiling_map(rng)
    sc.rect((x0, y0, h), (x1 - x0, 0, 0), (0, y1 - y0, 0), CEIL_STEP,
            lambda u, v: bilinear(ceil, u / 0.04 - 0.5, v / 0.04 - 0.5), (0, 0, -1))
    for bx in (-4.3, 4.3):
        sc.box((bx - 0.175, y0, h - 0.42), (bx + 0.175, y1, h), 0.04, {"-z": (0.76, 0.76, 0.75)}, color=(0.76, 0.76, 0.75),
               skip=("-y", "+y", "+z"), casts=False, feet=False)
    for lx, ly in LIGHTS:
        sc.rect((lx - 0.62, ly - 0.17, h - 0.02), (1.24, 0, 0), (0, 0.34, 0), 0.03, (0.55, 0.56, 0.56), (0, 0, -1))
        sc.rect((lx - 0.6, ly - 0.15, h - 0.03), (1.2, 0, 0), (0, 0.3, 0), 0.02, (1.0, 0.99, 0.96), (0, 0, -1), emit=True)

    # services: trays, conduits, pipes, sprinklers
    tray(sc, (-5.6, 4.55), (x1 - 0.05, 4.55), 0.45, 3.15)  # starts past the riser at x = -6
    tray(sc, (-5.0, 3.0), (5.0, 3.0), 0.4, 3.0, cables=6)
    tray(sc, (8.2, -4.5), (8.2, 4.3), 0.4, 3.1, cables=5)
    for asset in ("INV-1", "INV-2", "INV-3"):
        cx = (EQUIPMENT[asset][0][0] + EQUIPMENT[asset][1][0]) / 2
        for dx in (-0.3, 0.3):
            sc.cyl((cx + dx, 3.0, 2.1), (cx + dx, 3.0, 3.0), 0.03, GALV, 0.015, caps=(True, False))
    pipe_run(sc, (x0, 4.72, 3.6), (x1, 4.72, 3.6), 0.1, YELLOW, flanges=(3.4, 6.8, 10.2, 13.6))
    pipe_run(sc, (x0, 4.8, 3.95), (x1, 4.8, 3.95), 0.075, (0.25, 0.47, 0.32), flanges=(1.7, 5.1, 8.5, 11.9, 15.3))
    pipe_run(sc, (-6.0, 4.72, 0.0), (-6.0, 4.72, 3.6), 0.1, YELLOW, flanges=(0.08, 1.12, 1.48))
    valve(sc, (-6.0, 4.72, 1.3), 0.1)
    for x in np.arange(x0 + 1.0, x1, 2.0):  # pipe brackets
        sc.box((x - 0.03, 4.6, 3.47), (x + 0.03, y1, 3.5), 0.01, color=GALV, casts=False, feet=False)
        sc.box((x - 0.03, 4.7, 3.85), (x + 0.03, y1, 3.875), 0.01, color=GALV, casts=False, feet=False)
    red = (0.62, 0.12, 0.09)
    sc.cyl((x0, -1.0, 4.25), (x1, -1.0, 4.25), 0.03, red, 0.025)
    for x in np.arange(x0 + 2.5, x1, 3.0):
        sc.cyl((x, -1.0, 4.25), (x, -1.0, 4.13), 0.012, red, 0.008)
        sc.disk((x, -1.0, 4.12), (0, 0, -1), 0, 0.022, (0.7, 0.55, 0.25), 0.006)
        sc.cyl((x - 0.4, -1.0, 4.28), (x - 0.4, -1.0, h), 0.006, (0.4, 0.4, 0.39), 0.012)
    for z in (2.75, 2.82, 2.89):  # conduit bank along the right wall
        sc.cyl((x1 - 0.05, y0 + 0.5, z), (x1 - 0.05, 4.6, z), 0.022, GALV, 0.015)

    # wall panels, signs, door and shutter
    wall_box(sc, "left", 2.0, 1.1, 0.6, 0.8, 0.14, panel_canvas(rng, 0.6, 0.8, GREY, "DB-L1"))
    fa = panel_canvas(rng, 0.4, 0.5, (0.62, 0.1, 0.08), "FIRE ALARM", (0.95, 0.95, 0.92))
    fa.fill(0.1, 0.18, 0.3, 0.26, (0.08, 0.1, 0.1))
    fa.circle(0.27, 0.22, 0.008, (0.3, 0.9, 0.4))
    wall_box(sc, "left", -6.2, 1.3, 0.4, 0.5, 0.1, fa, color=(0.62, 0.1, 0.08))
    rtu = panel_canvas(rng, 0.6, 0.7, GREY, "RTU-1")
    rtu.fill(0.1, 0.3, 0.4, 0.42, (0.1, 0.11, 0.12))
    for k in range(5):
        rtu.circle(0.14 + 0.06 * k, 0.36, 0.008, (0.3, 0.9, 0.4) if k != 3 else (0.95, 0.6, 0.1))
    wall_box(sc, "back", 6.0, 1.2, 0.6, 0.7, 0.25, rtu)
    for wall, s0, z0 in (("back", -2.3, 2.45), ("back", 5.5, 2.45), ("back", -7.4, 1.4), ("right", 2.4, 2.3),
                         ("left", -8.7, 2.3), ("front", 4.6, 2.4)):
        wall_box(sc, wall, s0, z0, 0.25, 0.25, 0.12, None, color=(0.72, 0.73, 0.71))
    ac = Canvas(1.0, 0.32, 0.004, (0.88, 0.88, 0.86))
    ac.fill(0.08, 0.02, 0.92, 0.07, (0.15, 0.16, 0.17))
    ac.fill(0.84, 0.2, 0.9, 0.23, (0.3, 0.6, 0.9))
    wall_box(sc, "left", -2.0, 3.05, 1.0, 0.32, 0.25, weather(ac, rng, dirt=0.2, streaks=0.4), color=(0.88, 0.88, 0.86))
    for dy in (0.0, 0.07):
        sc.cyl((x0 + 0.08, -1.08 + dy, 3.37), (x0 + 0.08, -1.08 + dy, h), 0.025, (0.9, 0.9, 0.88), 0.014)
    sc.rect((-1.0, y0 + 0.02, 0), (-2.0, 0, 0), (0, 0, 2.25), 0.012, door_canvas(rng, 2.0, 2.25), (0, 1, 0), edge=0.2)
    exit_sign(sc, (-2.0, y0 + 0.03, 2.43), 1)
    exit_sign(sc, (-2.0, -4.6, 2.65), 0)
    for dx in (-0.12, 0.12):
        sc.cyl((-2.0 + dx, -4.6, 2.73), (-2.0 + dx, -4.6, h), 0.004, GALV, 0.01)
    extinguisher(sc, -3.6, y0)
    first_aid = Canvas(0.35, 0.35, 0.004, (0.1, 0.48, 0.26))
    first_aid.fill(0.145, 0.07, 0.205, 0.28, (0.96, 0.96, 0.94))
    first_aid.fill(0.07, 0.145, 0.28, 0.205, (0.96, 0.96, 0.94))
    wall_box(sc, "front", -0.65, 1.3, 0.35, 0.35, 0.12, weather(first_aid, rng, dirt=0.1, streaks=0.3),
             color=(0.1, 0.48, 0.26))
    ppe = Canvas(0.42, 0.3, 0.003, (0.95, 0.95, 0.93))
    for k in range(3):
        ppe.circle(0.08 + 0.13 * k, 0.17, 0.05, (0.1, 0.32, 0.65))
        ppe.circle(0.08 + 0.13 * k, 0.17, 0.025, (0.95, 0.95, 0.93))
    ppe.fill(0.04, 0.04, 0.38, 0.07, (0.3, 0.3, 0.3))
    sign(sc, "front", 0.4, 1.45, 0.42, 0.3, weather(ppe, rng, dirt=0, streaks=0.3, mottle=0.4))
    wall_box(sc, "front", 0.75, 1.15, 0.08, 0.12, 0.02, None, color=(0.9, 0.9, 0.88))
    sc.rect((7.5, y0 + 0.04, 0), (-4.0, 0, 0), (0, 0, 3.4), 0.022, shutter_canvas(rng, 4.0, 3.4), (0, 1, 0), edge=0.2)
    for gx in (3.42, 7.5):
        sc.box((gx, y0, 0), (gx + 0.08, y0 + 0.1, 3.4), 0.02, color=(0.35, 0.36, 0.37), feet=False)
    sc.box((3.4, y0, 3.4), (7.6, y0 + 0.38, 3.8), 0.03, {"-z": (0.6, 0.61, 0.6)}, color=(0.62, 0.63, 0.62), feet=False)
    return sc


# ---------- baked light ----------
def light_samples():
    return np.array([(x + dx, y, ROOM[4] - 0.04) for x, y in LIGHTS for dx in (-0.3, 0.3)], np.float32)


def direct(p, n, L):
    """Irradiance from the downward-facing panels at points p with normals n, without shadows (unnormalised)."""
    E = np.zeros(len(p), np.float32)
    for s in range(0, len(p), 250000):
        pp, nn = p[s:s + 250000], n[s:s + 250000]
        e = np.zeros(len(pp), np.float32)
        for l in L:
            d = l - pp
            r2 = np.einsum("ij,ij->i", d, d)
            r = np.sqrt(r2)
            e += np.maximum(np.einsum("ij,ij->i", d, nn) / r, 0) * np.maximum(d[:, 2] / r, 0) / np.maximum(r2, 0.5)
        E[s:s + 250000] = e
    return E


def room_ao(p, n):
    """Occlusion by the floor, walls and ceiling: dark at every junction, more where a surface faces the plane."""
    x0, x1, y0, y1, h = ROOM
    ao = np.ones(len(p), np.float32)
    for d, facing, k, r in ((p[:, 2], n[:, 2], 0.42, 0.16), (h - p[:, 2], -n[:, 2], 0.3, 0.35),
                            (p[:, 0] - x0, n[:, 0], 0.36, 0.28), (x1 - p[:, 0], -n[:, 0], 0.36, 0.28),
                            (p[:, 1] - y0, n[:, 1], 0.36, 0.28), (y1 - p[:, 1], -n[:, 1], 0.36, 0.28)):
        own = (d < 0.03) & (facing > 0.5)
        w = np.clip(0.8 - 0.6 * facing, 0, 1.3)
        ao *= np.where(own, 1, 1 - k * w * np.exp(-np.maximum(d, 0) / r))
    return ao


def box_ao(p, n, owner, boxes):
    """Contact shadow and broader occlusion round every solid, on everything but the solid itself."""
    ao = np.ones(len(p), np.float32)
    order = np.argsort(p[:, 0], kind="stable")
    xs = p[order, 0]
    for i, (lo, hi) in enumerate(boxes):
        reach = 0.25 * np.sort(hi - lo)[1] + 0.05
        pad = 3 * reach
        sel = order[np.searchsorted(xs, lo[0] - pad):np.searchsorted(xs, hi[0] + pad)]
        q = p[sel]
        sel = sel[np.all((q[:, 1:] > lo[1:] - pad) & (q[:, 1:] < hi[1:] + pad), axis=1) & (owner[sel] != i)]
        if not len(sel):
            continue
        g = np.clip(p[sel], lo, hi) - p[sel]
        d = np.linalg.norm(g, axis=1)
        w = np.clip(0.75 + 0.6 * np.einsum("ij,ij->i", n[sel], g / np.maximum(d, 1e-6)[:, None]), 0, 1.3)
        ao[sel] *= 1 - w * (0.45 * np.exp(-d / 0.04) + 0.22 * np.exp(-d / reach))
    return np.clip(ao, 0.12, 1)


def shadows(sc, p, n, L, res=0.06):
    """Soft cast shadows of the solids on the floor and the walls, from light maps (shadowed / unshadowed light)."""
    x0, x1, y0, y1, h = ROOM
    vis = np.ones(len(p), np.float32)
    planes = (((x0, y0, 0), (1, 0, 0), (0, 1, 0), x1 - x0, y1 - y0, (0, 0, 1), (p[:, 2] < 0.03) & (n[:, 2] > 0.5)),
              ((x0, y1, 0), (1, 0, 0), (0, 0, 1), x1 - x0, h, (0, -1, 0), (y1 - p[:, 1] < 0.06) & (n[:, 1] < -0.5)),
              ((x0, y0, 0), (1, 0, 0), (0, 0, 1), x1 - x0, h, (0, 1, 0), (p[:, 1] - y0 < 0.06) & (n[:, 1] > 0.5)),
              ((x0, y0, 0), (0, 1, 0), (0, 0, 1), y1 - y0, h, (1, 0, 0), (p[:, 0] - x0 < 0.06) & (n[:, 0] > 0.5)),
              ((x1, y0, 0), (0, 1, 0), (0, 0, 1), y1 - y0, h, (-1, 0, 0), (x1 - p[:, 0] < 0.06) & (n[:, 0] < -0.5)))
    for o, a, b, la, lb, nn, on in planes:
        o, a, b, nn = (np.array(x, np.float32) for x in (o, a, b, nn))
        u, v = np.meshgrid(np.arange(res / 2, la, res, dtype=np.float32), np.arange(res / 2, lb, res, dtype=np.float32))
        cells = o + u.reshape(-1, 1) * a + v.reshape(-1, 1) * b + 0.01 * nn
        near = [(sc.boxes[i], np.flatnonzero(box_dist(cells, *sc.boxes[i]) < 3.0)) for i in sc.casts]
        near = [(bx, idx) for bx, idx in near if len(idx)]
        tot, lit = np.zeros(len(cells), np.float32), np.zeros(len(cells), np.float32)
        for l in L:
            d = l - cells
            r = np.linalg.norm(d, axis=1)
            c = np.maximum(d @ nn / r, 0) * np.maximum(d[:, 2] / r, 0) / np.maximum(r * r, 0.5)
            hit = np.zeros(len(cells), bool)
            for (lo, hi), idx in near:
                hit[idx] |= seg_hits(cells[idx], d[idx], lo, hi)
            tot += c
            lit += c * ~hit
        ratio = cv2.GaussianBlur((lit / np.maximum(tot, 1e-9)).reshape(u.shape).astype(np.float32), (0, 0), 1.8)
        q = p[on] - o
        vis[on] = bilinear(ratio, q @ a / res - 0.5, q @ b / res - 0.5)
    return vis


def finish(sc):
    """Merges the parts, bakes the light into their colours and adds the floaters and glare a capture has.
    Returns centres, colours (sRGB 0..1), per-axis sigmas, board-frame quaternions and opacities."""
    rng = sc.rng
    P, C, Nn, SIG, Q, OWN, AO, GL, EM, OPA = (np.concatenate([part[k] for part in sc.parts])
                                              for k in ("p", "c", "n", "sig", "q", "own", "ao", "gloss", "emit", "opa"))
    x0, x1, y0, y1, h = ROOM
    L = light_samples()
    A = AO * room_ao(P, Nn) * box_ao(P, Nn, OWN, sc.boxes)
    fx, fy = np.meshgrid(np.arange(x0 + 0.5, x1, 0.5), np.arange(y0 + 0.5, y1, 0.5))
    fp = np.stack([fx.ravel(), fy.ravel(), np.zeros(fx.size)], 1).astype(np.float32)
    k = 1 / np.median(direct(fp, np.tile(np.float32([0, 0, 1]), (len(fp), 1)), L))
    Ed = np.minimum(direct(P, Nn, L) * k, 3.0) * shadows(sc, P, Nn, L)
    E = AMBIENT * A + DIRECT * Ed * (0.55 + 0.45 * A) + BOUNCE * A * np.maximum(-Nn[:, 2], 0)
    lin = srgb_lin(C) * E[:, None]
    lx, ly = np.array(LIGHTS, np.float32).T
    fl = np.flatnonzero((P[:, 2] < 0.03) & (Nn[:, 2] > 0.5))
    sheen = np.zeros(len(fl), np.float32)                     # glossy concrete: a soft smear of each panel under it
    ce = np.flatnonzero((P[:, 2] > h - 0.45) & (Nn[:, 2] < -0.5) & ~EM)
    bloom = np.zeros(len(ce), np.float32)                     # and the glow round each panel on the ceiling
    for cx, cy in zip(lx, ly):
        sheen += np.exp(-((P[fl, 0] - cx) ** 2 + (P[fl, 1] - cy) ** 2) / (2 * 0.9 ** 2))
        dx, dy = np.maximum(np.abs(P[ce, 0] - cx) - 0.6, 0), np.maximum(np.abs(P[ce, 1] - cy) - 0.15, 0)
        bloom += np.exp(-(dx ** 2 + dy ** 2) / (2 * 0.3 ** 2))
    lin[fl] += (0.1 * GL[fl] * sheen * A[fl])[:, None] * np.float32([1.0, 1.0, 0.97])
    lin[ce] += (0.22 * bloom)[:, None] * np.float32([1.0, 1.0, 0.97])
    out = lin_srgb(1 - np.exp(-EXPOSURE * lin))
    luma = out @ np.float32([0.2126, 0.7152, 0.0722])
    out = luma[:, None] + 0.86 * (out - luma[:, None])                                 # a phone camera's muted colour
    out = 0.015 + 0.985 * out * np.float32([0.985, 1.0, 1.012])                        # cool panel light, lifted blacks
    out = out * (1 + rng.normal(0, 0.012, (len(out), 1))) + rng.normal(0, 0.004, out.shape)
    out[EM] = C[EM]
    out = np.clip(out, 0, 1).astype(np.float32)
    # floaters: faint blobs just off the surfaces, and a few needles, as reconstruction leaves them
    src = rng.choice(np.flatnonzero(~EM), len(P) // 70)
    nf = len(src)
    fp = P[src] + Nn[src] * rng.exponential(0.05, (nf, 1)) + rng.normal(0, 0.025, (nf, 3))
    fs = rng.uniform(0.008, 0.045, (nf, 1)) * rng.uniform(0.6, 1.4, (nf, 3))
    fs[rng.random(nf) < 0.12, 0] *= 4
    fq = rng.normal(0, 1, (nf, 4))
    fq /= np.linalg.norm(fq, axis=1, keepdims=True)
    fc = np.clip(out[src] * rng.uniform(0.85, 1.1, (nf, 1)), 0, 1)
    fo = rng.uniform(0.03, 0.22, nf)
    # glare under each panel, the way a phone's flare gets baked into a capture
    gp = np.array([(cx + dx, cy, h - 0.12) for cx, cy in LIGHTS for dx in (-0.35, 0, 0.35)])
    gs = np.tile([0.4, 0.18, 0.05], (len(gp), 1))
    gq = np.tile([1.0, 0, 0, 0], (len(gp), 1))
    gc = np.tile([1.0, 1.0, 0.97], (len(gp), 1))
    go = np.full(len(gp), 0.07)
    return (np.concatenate([P, fp, gp]), np.concatenate([out, fc, gc]), np.concatenate([SIG, fs, gs]),
            np.concatenate([Q, fq, gq]), np.concatenate([OPA, fo, go]))


# ---------- registration ----------
def front_centre(asset):
    """The middle of the asset's front face at about chest height, and the face's outward normal (metres, z up)."""
    lo, hi = (np.array(x, float) for x in EQUIPMENT[asset])
    front = FRONT.get(asset, "-y")
    axis = {"x": 0, "y": 1}[front[1]]
    c = (lo + hi) / 2
    c[axis] = lo[axis] if front[0] == "-" else hi[axis]
    c[2] = min(1.2, (lo[2] + hi[2]) / 2) if hi[2] - lo[2] > 1 else (lo[2] + hi[2]) / 2
    normal = np.zeros(3)
    normal[axis] = -1.0 if front[0] == "-" else 1.0
    return c, normal


def look_at(position, target, up=(0, 0, 1.0)):
    """Board metres -> OpenCV camera (x right, y down, z forward)."""
    f = np.asarray(target, float) - position
    f /= np.linalg.norm(f)
    right = np.cross(f, up)
    right /= np.linalg.norm(right)
    R = np.stack([right, np.cross(f, right), f])
    return R, -R @ position


def to_splat(p):
    return S * np.asarray(p) @ R_S.T + T_S


def stations(stamp):
    """Registration views in the schema of scripts/register_viewpoints.write_registration, from the known geometry."""
    W, H = SIZE
    f = F35 * np.hypot(W, H) / np.hypot(36, 24)
    K = np.array([[f, 0, (W - 1) / 2], [0, f, (H - 1) / 2], [0, 0, 1.0]])
    views = {}
    for vid, (asset, dist) in STATIONS.items():
        target, normal = front_centre(asset)
        position = target + normal * dist
        position[2] = 1.6 if target[2] < 1.4 else target[2] + 0.2
        R_cb, t_cb = look_at(position, target)
        R = R_cb @ R_S.T              # x_cam = R_cb x_board + t_cb with x_board = R_S^T (x_splat - T_S) / S,
        t = S * t_cb - R @ T_S        # and camera units scaled to splat units
        views[vid] = {"photo": None, "focal_35mm": F35, "registered_at": stamp, "K": K.tolist(), "R": R.tolist(),
                      "t": t.tolist(), "size": [W, H], "rms_px": 0.0, "tags": [0, 1, 2, 3],
                      "position": to_splat(position).round(6).tolist(), "target": to_splat(target).round(6).tolist(),
                      "up": (R.T @ [0, -1.0, 0]).round(6).tolist(),
                      "fov_deg": round(float(np.degrees(2 * np.arctan(H / 2 / f))), 2)}
    return views


def write_ply(path: Path, centers, colors, sigmas, quats, opacity) -> None:
    """Binary little-endian 3DGS PLY: each Gaussian's board-frame rotation carried into splat axes by R_S."""
    names = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
             "rot_0", "rot_1", "rot_2", "rot_3"]
    v = np.zeros(len(centers), [(n, "<f4") for n in names])
    v["x"], v["y"], v["z"] = to_splat(centers.astype(float)).T
    for i, ch in enumerate((colors - 0.5).T / SH_C0):
        v[f"f_dc_{i}"] = ch
    o = np.clip(opacity, 1e-3, 0.999)
    v["opacity"] = np.log(o / (1 - o))
    for i in range(3):
        v[f"scale_{i}"] = np.log(np.maximum(sigmas[:, i], 1e-4) * S)
    q = qmul(Q_S, quats)
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    for i in range(4):
        v[f"rot_{i}"] = q[:, i]
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {len(v)}",
              *[f"property float {n}" for n in names], "end_header"]
    with open(path, "wb") as fh:
        fh.write(("\n".join(header) + "\n").encode())
        fh.write(v.tobytes())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPO / "assets" / "splats" / "_dev"), help="path without extension")
    out = Path(ap.parse_args(argv).out)
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    board, ply = Board(), out.with_suffix(".ply")
    sc = build(board)
    centers, colors, sigmas, quats, opacity = finish(sc)
    write_ply(ply, centers, colors, sigmas, quats, opacity)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    reg = {"splat": str(ply.relative_to(REPO)) if ply.is_relative_to(REPO) else str(ply),
           "splat_sha256": hashlib.sha256(ply.read_bytes()).hexdigest(), "board": board.as_dict(),
           "board_to_splat": {"scale": S, "R": R_S.tolist(), "t": T_S.tolist(), "rms_mm": 0.0, "tags": [0, 1, 2, 3],
                              "source": "synthetic dev scene (scripts/twin_dev_scene.py)"},
           "views": stations(stamp)}
    out.with_name(out.name + ".registration.json").write_text(json.dumps(reg, indent=1) + "\n")
    print(f"wrote {ply} ({len(centers):,} Gaussians, {ply.stat().st_size / 1e6:.0f} MB) and "
          f"{out.name}.registration.json with {len(reg['views'])} stations in {time.time() - t0:.0f} s; "
          f"open /twin.html?scene={out.name}")
    print("photo credits (CC BY-SA needs them wherever the scene is shown):")
    for line in credits():
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Build the s06 case pack: the sensor, camera and infrared data an agent gets to find what is wrong at the plant.

Use case: scenario s06_stale_scada at 11:00, the moment the agent gets its prompt. INV-3's comms card died at 09:30, the
inverter safe-stopped at 0 kW, and SCADA kept showing its last reading with no alarm. The pack has three sources:

- sensors: every SCADA minute from 09:01 to 11:00 as the dashboard saw it (gridtwin.sim, seed 6, no model calls);
- camera: one fixed display-camera frame per inverter at 11:00 (AI-generated stand-ins from scripts/gen_s06_case_photos.py,
  falling back to the photo bank), with each lit display showing that unit's real sim output;
- infrared: one fixed thermal-camera frame per inverter at 11:00, modelled on that same camera frame from the sim's true
  heatsink and ambient temperatures, with a radiometric matrix in deg C. (scripts/render_splat_views.py only recolours
  splat renders; this is the radiometric, sim-driven version.)

agent/ is everything the agent may see (no verdict anywhere in it); answer_key/ holds the hidden truth and the finding.
agent/objects.json indexes each inverter's data so the twin can label the object and pull its data.

Run: python -m scripts.make_s06_case [out_dir]   (default assets/demo/s06_case; reads <out_dir>/_src for the photos)
"""
import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from gridtwin.sim import episode
from gridtwin.sim.camera import REPO
from gridtwin.sim.plant import BLOCK_FOR, BREAKER_FOR, CFG, INV_CFG, INVERTERS, Plant

SCENARIO = "s06_stale_scada"
OUT = REPO / "assets" / "demo" / "s06_case"
FRAME = (1024, 768)          # camera frame; the thermal camera shares its view
IR = (320, 240)              # radiometric resolution, like a 320x240 LWIR core
IR_VIEW = (640, 480)         # rendered thermal image
SPAN = (20.0, 50.0)          # locked palette span, the same for every unit so frames compare
EMISSIVITY, REFLECTED_C, DISTANCE_M = 0.95, 20.0, 4.0
CAPTURED = {"camera": "2026-07-14T11:00:04", "infrared": "2026-07-14T11:00:20"}
CANON_PHOTO = REPO / "assets" / "photos" / "INV-3" / "healthy" / "01_ai_generated.jpg"
BANK = {"lit": CANON_PHOTO, "dark": REPO / "assets" / "photos" / "INV-3" / "display_off" / "01_ai_generated.jpg"}
# Cabinet layout in the canonical photo (1024x768 px): x0, y0, x1, y1. Mapped onto each unit's frame, then refined.
CANON = {
    "cabinet": (258, 40, 775, 660), "roof": (258, 40, 775, 64),
    "hood_l": (288, 14, 506, 46), "hood_r": (517, 14, 741, 46),
    "grille_l": (306, 22, 484, 44), "grille_r": (548, 24, 724, 46),
    "louvre_ul": (300, 100, 490, 303), "louvre_ll": (300, 445, 488, 612), "louvre_lr": (550, 445, 732, 612),
    "bezel": (580, 176, 708, 274), "handles": (490, 340, 550, 408), "plinth": (258, 658, 772, 702),
}
SLATTED = ("louvre_ul", "louvre_ll", "louvre_lr", "grille_l", "grille_r")
IRONBOW = [(0.00, (0, 0, 10)), (0.10, (25, 0, 70)), (0.25, (85, 0, 140)), (0.40, (150, 10, 150)), (0.52, (205, 40, 90)),
           (0.64, (235, 85, 20)), (0.76, (250, 140, 0)), (0.88, (255, 200, 30)), (0.96, (255, 240, 130)),
           (1.00, (255, 255, 235))]


# ---------------------------------------------------------------- sensors (the sim)

def simulate():
    """Run s06 to 11:00 and record every minute as SCADA reported it and as it really was."""
    minutes, tick = [], Plant.tick
    rng, held, gust = np.random.default_rng(66), {}, [0.0]
    dc_v_offset = dict(zip(INVERTERS, (-3.1, 1.8, -0.6, 2.7)))  # each unit's MPPT sits at its own voltage

    def reported(sim, i):
        """What the SCADA tag shows: the sim's report with sensor noise when fresh, the held value when not."""
        s = sim.scada[i]
        if sim.last_update[i] == sim.t or i not in held:
            held[i] = {"ac_kw": round(s["ac_kw"] * (1 + rng.normal(0, 0.001)), 2),
                       "dc_kw": round(s["dc_kw"] * (1 + rng.normal(0, 0.001)), 2),
                       "dc_v": round(s["dc_v"] + dc_v_offset[i] + rng.normal(0, 0.6), 1),
                       "heatsink_c": round(s["heatsink_c"] + rng.normal(0, 0.12), 1),
                       "fan_rpm": int(round(s["fan_rpm"] + rng.normal(0, 10))) if s["fan_rpm"] else 0,
                       "status": s["status"]}
        return held[i]

    def rec(sim):
        tick(sim)
        gust[0] = 0.85 * gust[0] + rng.normal(0, 0.35)
        row = {"t": sim.now_str(), "poi_export_kw": round(sim.export_kw * (1 + rng.normal(0, 0.0006)), 2),
               "poa_wm2": round(sim.met["poa_wm2"] + rng.normal(0, 1.5), 1),
               "ambient_c": round(sim.met["ambient_c"] + rng.normal(0, 0.04), 2),
               "wind_ms": round(max(0.3, sim.met["wind_ms"] + gust[0]), 1)}
        truth = {"t": row["t"]}
        for i in INVERTERS:
            v, rep = sim.inv[i], reported(sim, i)
            for k in ("ac_kw", "dc_kw", "dc_v", "heatsink_c", "fan_rpm", "status"):
                row[f"{i}_{k}"] = rep[k]
            row[f"{i}_last_update"] = sim.last_update[i].strftime("%Y-%m-%dT%H:%M")
            truth.update({f"{i}_true_ac_kw": v["ac_kw"], f"{i}_true_status": v["status"],
                          f"{i}_true_heatsink_c": round(v["heatsink_c"], 2), f"{i}_comms_ok": v["comms_ok"]})
        minutes.append((row, truth))

    Plant.tick = rec
    try:
        with tempfile.TemporaryDirectory() as tmp:
            sim, tb, _log, sc = episode.start(SCENARIO, run_id="case", runs_dir=Path(tmp))
            alarms = {"as_of": sim.now_str(), "active": tb.call("list_alarms", {})["alarms"],
                      "since_0900": tb.call("list_alarms", {"active_only": False})["alarms"]}
            now = {"t": sim.now_str(), "ambient_c": sim.met["ambient_c"], "poa_wm2": sim.met["poa_wm2"],
                   "inv": {i: dict(sim.inv[i]) for i in INVERTERS}}
    finally:
        Plant.tick = tick
    return sc, minutes, alarms, now


# ---------------------------------------------------------------- camera

def _font(size, mono=False):
    for path in (("/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc") if mono else
                 ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc")):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def camera_frame(unit, running, src_dir, allow_bank=False):
    """The unit's display-camera frame at 1024x768: the generated photo, or the photo bank only when allow_bank."""
    for ext in (".jpg", ".png"):
        p = Path(src_dir) / f"{unit}{ext}"
        if p.exists():
            im = Image.open(p).convert("RGB")
            meta = Path(src_dir) / f"{unit}.json"
            prov = json.loads(meta.read_text()) if meta.exists() else {"source": str(p)}
            break
    else:
        if not allow_bank:
            raise FileNotFoundError(f"no generated photo for {unit} in {src_dir} (run scripts.gen_s06_case_photos, "
                                    "or pass allow_bank=True for photo-bank stand-ins)")
        p = BANK["lit" if running else "dark"]
        im, prov = Image.open(p).convert("RGB"), {"source": str(p.relative_to(REPO)), "model": None, "prompt": None}
    w, h = im.size
    if abs(w / h - 4 / 3) > 0.01:  # centre-crop to the camera's 4:3
        nw = round(h * 4 / 3) if w / h > 4 / 3 else w
        nh = h if w / h > 4 / 3 else round(w * 3 / 4)
        im = im.crop(((w - nw) // 2, (h - nh) // 2, (w - nw) // 2 + nw, (h - nh) // 2 + nh))
    return im.resize(FRAME, Image.LANCZOS), prov


def _shadow_text(d, xy, text, font, anchor="la"):
    x, y = xy
    d.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0), anchor=anchor)
    d.text((x, y), text, font=font, fill=(255, 255, 255), anchor=anchor)


def stamp_camera(im, unit):
    """On-screen display like a site camera: camera id top left, time top right."""
    im = im.copy()
    im = im.convert("RGBA")
    over = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d, f = ImageDraw.Draw(over), _font(19, mono=True)
    left, right = f"CAM-{BLOCK_FOR[unit]}-DSP  {unit} display", CAPTURED["camera"].replace("T", "  ")
    for text, x, anchor in ((left, 16, "la"), (right, FRAME[0] - 16, "ra")):
        x0, y0, x1, y1 = d.textbbox((x, 14), text, font=f, anchor=anchor)
        d.rectangle((x0 - 8, y0 - 6, x1 + 8, y1 + 6), fill=(0, 0, 0, 130))  # readable over sky and grey roof alike
    im.alpha_composite(over)
    d = ImageDraw.Draw(im)
    _shadow_text(d, (16, 14), left, f)
    _shadow_text(d, (FRAME[0] - 16, 14), right, f, anchor="ra")
    return im.convert("RGB")


# ---------------------------------------------------------------- geometry

def _rect_pts(r):
    x0, y0, x1, y1 = r
    return np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])


def _bbox(pts):
    return (int(pts[:, 0].min()), int(pts[:, 1].min()), int(pts[:, 0].max()), int(pts[:, 1].max()))


def _largest_bbox(mask):
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if n <= 1:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = stats[k, :4]
    return int(x), int(y), int(x + w), int(y + h)


def _refine(gray, rect, kind):
    """Snap a mapped rectangle onto the real feature: slat texture for louvres and grilles, the black bezel for the display."""
    x0, y0, x1, y1 = rect
    pad = 18 if kind == "grille" else 40
    wx0, wy0 = max(0, x0 - pad), max(0, y0 - pad)
    wx1, wy1 = min(FRAME[0], x1 + pad), min(FRAME[1], y1 + pad)
    win = gray[wy0:wy1, wx0:wx1].astype(np.float32)
    if kind == "bezel":
        mask = win < 70
    else:
        sy = np.abs(cv2.Sobel(win, cv2.CV_32F, 0, 1, ksize=3)) - 0.5 * np.abs(cv2.Sobel(win, cv2.CV_32F, 1, 0, ksize=3))
        score = cv2.boxFilter(np.clip(sy, 0, None), -1, (9, 9) if kind == "grille" else (21, 21))
        mask = score > 0.45 * score.max()
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    bb = _largest_bbox(mask)
    if bb is None:
        return rect
    got = (bb[0] + wx0, bb[1] + wy0, bb[2] + wx0, bb[3] + wy0)
    area = lambda r: max(1, (r[2] - r[0]) * (r[3] - r[1]))
    return got if 0.6 < area(got) / area(rect) < 1.5 else rect


def geometry(im):
    """Each cabinet part's rectangle in this frame: the canonical layout mapped by SIFT + affine, then refined."""
    canon = np.asarray(Image.open(CANON_PHOTO).convert("L"))
    gray = np.asarray(im.convert("L"))
    mask = np.zeros_like(canon)
    mask[10:705, 250:780] = 255
    cv2.setRNGSeed(6)  # RANSAC samples with OpenCV's RNG; a fixed seed keeps the pack byte-stable
    sift = cv2.SIFT_create(6000)
    k1, d1 = sift.detectAndCompute(canon, mask)
    k2, d2 = sift.detectAndCompute(gray, None)
    good = [a for a, b in cv2.BFMatcher().knnMatch(d1, d2, k=2) if a.distance < 0.75 * b.distance]
    src = np.float32([k1[a.queryIdx].pt for a in good])
    dst = np.float32([k2[a.trainIdx].pt for a in good])
    A, inliers = cv2.estimateAffine2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=4.0)
    if A is None or int(inliers.sum()) < 10:
        raise RuntimeError(f"cabinet registration failed ({len(good)} matches)")
    geo = {k: _bbox(cv2.transform(_rect_pts(r).reshape(-1, 1, 2), A).reshape(-1, 2)) for k, r in CANON.items()}
    for k in SLATTED:
        geo[k] = _refine(gray, geo[k], "grille" if k.startswith("grille") else "louvre")
    geo["bezel"] = _refine(gray, geo["bezel"], "bezel")
    bx0, by0, bx1, by1 = geo["bezel"]
    ix, iy = round(0.08 * (bx1 - bx0)), round(0.1 * (by1 - by0))
    geo["screen"] = (bx0 + ix, by0 + iy, bx1 - ix, by1 - iy)
    clamp = lambda r: (max(0, r[0]), max(0, r[1]), min(FRAME[0], r[2]), min(FRAME[1], r[3]))  # hoods can sit past the top edge
    return {k: clamp(r) for k, r in geo.items()}


# ---------------------------------------------------------------- infrared

def _fill(T, r, value):
    x0, y0, x1, y1 = r
    T[max(0, y0):y1, max(0, x0):x1] = value


def temperature_field(im, geo, ambient, heatsink, running, seed):
    """Apparent surface temperature (deg C) per frame pixel at 11:00, returned at the radiometric resolution.

    Background: sky by colour (cold), vegetation near ambient, PV modules warm, every other sunlit surface heated by
    how dark it is (albedo), more so for ground than for upright surfaces. Cabinet: skin, roof, exhaust grilles and
    louvres heated by the inverter's own losses, read from its true heatsink temperature, so a unit that is not running
    sits at what the sun alone gives it. The display glass reflects the sky unless its backlight warms it."""
    rgb = np.asarray(im).astype(np.float32) / 255.0
    H, W = FRAME[1], FRAME[0]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    yy = np.repeat(np.arange(H, dtype=np.float32)[:, None], W, 1) / H
    cab = np.zeros((H, W), bool)
    for k in ("cabinet", "hood_l", "hood_r", "plinth"):
        x0, y0, x1, y1 = geo[k]
        cab[y0:y1, x0:x1] = True

    sat = rgb.max(-1) - rgb.min(-1)
    modules = (b > r + 0.12) & (lum < 0.5)                            # dark blue PV glass, not sky seen through the fence
    cand = (((b > r + 0.06) & (b >= g)) | ((sat < 0.10) & (lum > 0.82))) & (yy < 0.5) & ~cab & ~modules
    _n, lab = cv2.connectedComponents(cand.astype(np.uint8), connectivity=4)
    top = set(np.unique(lab[0])) - {0}
    sky = np.isin(lab, list(top)) if top else np.zeros_like(cand)
    cx0, cy0, cx1, _ = geo["cabinet"]                                 # lifting eyes and roof hardware between the hoods
    band = np.zeros((H, W), bool)
    band[min(geo["hood_l"][1], geo["hood_r"][1]):cy0, cx0:cx1] = True
    cab |= band & ~sky
    veg = (((g > r + 0.04) & (g > b + 0.02)) | ((g >= r + 0.01) & (g > b + 0.01) & (sat > 0.04) & (lum < 0.55))) & ~sky & ~cab
    pv = (b > r + 0.12) & (lum < 0.62) & ~sky & ~cab & (yy > 0.15)

    orient = np.clip((yy - 0.38) / 0.17, 0, 1) * 0.5 + 0.85        # upright 0.85 .. ground 1.35
    lum_s = cv2.GaussianBlur(lum, (0, 0), 2.5)                       # surfaces, not single stones
    lit = np.clip((lum_s - 0.05) / 0.30, 0.15, 1.0)                  # the darkest pixels are shadow, not black paint
    T = ambient + 22.0 * (1.0 - lum_s) * orient * lit                # sunlit surfaces by albedo
    T[veg] = ambient - 1.5 + 3.0 * (1 - lum[veg])
    T[pv] = ambient + 15.0 + 4.0 * (1 - lum[pv])
    rng = np.random.default_rng(seed)
    T[sky] = rng.uniform(-3.0, 1.0) + 16.0 * np.clip((lum[sky] - 0.6) / 0.4, 0, 1)  # clear sky about 0 C, cloud warmer

    E = max(0.0, heatsink - ambient)
    x0, y0, x1, y1 = geo["cabinet"]
    grad = np.clip(1 - (yy * H - y0) / max(1, (y1 - y0)), 0, 1)
    blotch = cv2.resize(rng.normal(0, 1, (6, 8)).astype(np.float32), (W, H), interpolation=cv2.INTER_CUBIC)
    side = (np.arange(W, dtype=np.float32)[None, :] - x0) / max(1, (x1 - x0))  # sun from the left: left door a touch warmer
    skin = ambient + 5.0 + 0.25 * E + 0.15 * E * grad + 0.35 * blotch + 0.8 * (0.5 - side)
    T[cab] = skin[cab]
    j = lambda: rng.normal(0, 0.25)                                   # unit-to-unit spread in paint, fit and airflow
    # Exhaust air leaves below the heatsink temperature, so grilles and louvres stay under it; sunlit roof and skin
    # can sit above it from solar gain alone.
    _fill(T, geo["roof"], ambient + 6.5 + 0.25 * E + j())
    for k in ("hood_l", "hood_r"):
        _fill(T, geo[k], ambient + 4.5 + 0.45 * E + j())
    for k in ("grille_l", "grille_r"):
        _fill(T, geo[k], ambient + 1.0 + 0.85 * E + j())
    _fill(T, geo["louvre_ul"], ambient + 1.0 + 0.75 * E + j())
    for k in ("louvre_ll", "louvre_lr"):
        _fill(T, geo[k], ambient + 1.0 + 0.3 * E + j())
    _fill(T, geo["bezel"], ambient + 5.5 + 0.3 * E + j())
    _fill(T, geo["screen"], (ambient + 3.0 + 0.45 * E if running else ambient + 5.5) + j())  # dark glass still takes sun
    _fill(T, geo["handles"], ambient + 7.0 + j())                     # black plastic in sun, recessed in the door
    _fill(T, geo["plinth"], ambient + 3.5 + 0.2 * E + j())
    glass = np.zeros((H, W), bool)                                     # LWIR cannot see what an LCD shows
    bx0, by0, bx1, by1 = geo["bezel"]
    glass[by0:by1, bx0:bx1] = True

    gray = lum * 255
    hp = gray - cv2.GaussianBlur(gray, (0, 0), 6)
    T = T + np.where(cab, 0.004, -0.015) * hp * (~sky) * (~glass)    # dark stones run hotter; faint skin texture
    hot = np.where(cab, np.clip(T - (ambient + 6.0), 0, None), 0).astype(np.float32)
    T = T + 0.15 * cv2.GaussianBlur(hot, (0, 0), 14)                 # warm air spreads from the vents
    if running:                                                        # exhaust can never be hotter than the heatsink
        for k in ("grille_l", "grille_r", "louvre_ul", "louvre_ll", "louvre_lr"):
            x0, y0, x1, y1 = geo[k]
            np.minimum(T[y0:y1, x0:x1], heatsink - 0.3, out=T[y0:y1, x0:x1])
    T = cv2.GaussianBlur(T.astype(np.float32), (0, 0), 2.2)          # optics of a 320x240 LWIR lens
    rad = cv2.resize(T, IR, interpolation=cv2.INTER_AREA) + rng.normal(0, 0.05, (IR[1], IR[0]))  # NETD about 50 mK
    return np.round(rad, 2)


def _lut():
    xs = np.linspace(0, 1, 256)
    pos = [p for p, _ in IRONBOW]
    return np.stack([np.interp(xs, pos, [c[ch] for _, c in IRONBOW]) for ch in range(3)], -1).astype(np.float32)


def _spot(rad, xy_frame, k=1):
    x, y = round(xy_frame[0] * IR[0] / FRAME[0]), round(xy_frame[1] * IR[1] / FRAME[1])
    return float(np.round(rad[max(0, y - k):y + k + 1, max(0, x - k):x + k + 1].mean(), 1)), [x, y]


def ir_readings(rad, geo):
    c = lambda r: ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
    sp1, sp1_xy = _spot(rad, c(geo["louvre_ul"]))
    sp2, sp2_xy = _spot(rad, c(geo["screen"]))
    x0, y0, x1, y1 = geo["cabinet"]                                   # the cabinet body, roof to the top of the plinth
    s = IR[0] / FRAME[0]
    box = [round(x0 * s) + 1, round(y0 * s) + 1, round(x1 * s) - 1, round(y1 * s) - 1]
    sub = rad[box[1]:box[3], box[0]:box[2]]
    return {
        "Sp1": {"label": "upper louvre, left door", "xy": sp1_xy, "temp_c": sp1},
        "Sp2": {"label": "display", "xy": sp2_xy, "temp_c": sp2},
        "Bx1": {"label": "cabinet", "box": box, "box_convention": "x0, y0, x1, y1 in the 320x240 grid; x1 and y1 exclusive", "max_c": round(float(sub.max()), 1), "min_c": round(float(sub.min()), 1),
                "mean_c": round(float(sub.mean()), 1)},
    }


def render_ir(rad, im, readings, unit):
    """False-colour LWIR frame with MSX-style edge detail, spot meters, a box and the locked span."""
    W, H = IR_VIEW
    T = cv2.resize(rad.astype(np.float32), IR_VIEW, interpolation=cv2.INTER_CUBIC)
    norm = np.clip((T - SPAN[0]) / (SPAN[1] - SPAN[0]), 0, 1)
    out = _lut()[(norm * 255).astype(np.uint8)]
    lum = cv2.resize(np.asarray(im.convert("L")).astype(np.float32), IR_VIEW, interpolation=cv2.INTER_AREA)
    edges = np.clip(lum - cv2.GaussianBlur(lum, (0, 0), 1.6), -40, 40)
    out = np.clip(out + 0.9 * edges[..., None], 0, 255).astype(np.uint8)
    img = Image.fromarray(out).convert("RGBA")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    fm, fs, ft = _font(15, mono=True), _font(12, mono=True), _font(15)
    k = W / IR[0]

    def cross(xy, name):
        x, y = xy[0] * k, xy[1] * k
        for dx, dy in ((1, 1), (0, 0)):
            col = (0, 0, 0, 200) if dx else (255, 255, 255, 255)
            d.ellipse((x - 7 + dx, y - 7 + dy, x + 7 + dx, y + 7 + dy), outline=col, width=1)
            d.line((x - 12 + dx, y + dy, x - 4 + dx, y + dy), fill=col, width=1)
            d.line((x + 4 + dx, y + dy, x + 12 + dx, y + dy), fill=col, width=1)
            d.line((x + dx, y - 12 + dy, x + dx, y - 4 + dy), fill=col, width=1)
            d.line((x + dx, y + 4 + dy, x + dx, y + 12 + dy), fill=col, width=1)
            d.text((x + 10 + dx, y - 22 + dy), name, font=fs, fill=col)

    bx = [v * k for v in readings["Bx1"]["box"]]
    d.rectangle(bx, outline=(255, 255, 255, 230), width=1)
    d.text((bx[0] + 4, bx[3] - 17), "Bx1", font=fs, fill=(255, 255, 255, 255))
    cross(readings["Sp1"]["xy"], "Sp1")
    cross(readings["Sp2"]["xy"], "Sp2")

    lines = [("Sp1", f"{readings['Sp1']['temp_c']:5.1f} °C"), ("Sp2", f"{readings['Sp2']['temp_c']:5.1f} °C"),
             ("Bx1 max", f"{readings['Bx1']['max_c']:5.1f} °C")]
    d.rounded_rectangle((8, 8, 200, 8 + 12 + 20 * len(lines)), 6, fill=(0, 0, 0, 150))
    for j, (a, v) in enumerate(lines):
        d.text((18, 15 + 20 * j), a, font=fm, fill=(255, 255, 255, 255))
        d.text((190, 15 + 20 * j), v, font=fm, fill=(255, 255, 255, 255), anchor="ra")
    tag = f"IR-{BLOCK_FOR[unit]}-01  {unit}"
    tw = d.textlength(tag, font=ft)
    d.rounded_rectangle((W - 20 - tw - 16, 8, W - 12, 34), 6, fill=(0, 0, 0, 150))
    d.text((W - 20, 14), tag, font=ft, fill=(255, 255, 255, 255), anchor="ra")

    sx0, sy0, sy1 = W - 34, 70, H - 70                              # scale bar
    bar = _lut()[np.linspace(255, 0, sy1 - sy0).astype(int)].astype(np.uint8)
    d.rounded_rectangle((sx0 - 28, sy0 - 26, W - 12, sy1 + 26), 6, fill=(0, 0, 0, 150))
    img.alpha_composite(over)
    img.paste(Image.fromarray(np.repeat(bar[:, None, :], 14, 1)), (sx0, sy0))
    d2 = ImageDraw.Draw(img)
    d2.text((sx0 + 7, sy0 - 6), f"{SPAN[1]:.0f}", font=fs, fill=(255, 255, 255), anchor="md")
    d2.text((sx0 + 7, sy1 + 6), f"{SPAN[0]:.0f}", font=fs, fill=(255, 255, 255), anchor="ma")
    d2.text((sx0 - 21, (sy0 + sy1) / 2), "°C", font=fs, fill=(255, 255, 255), anchor="mm")

    foot = f"{CAPTURED['infrared'].replace('T', '  ')}    ε {EMISSIVITY:.2f}   Trefl {REFLECTED_C:.0f} °C   d {DISTANCE_M:.0f} m"
    over2 = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d3 = ImageDraw.Draw(over2)
    fw = d3.textlength(foot, font=fs)
    d3.rounded_rectangle((8, H - 32, 24 + fw, H - 8), 6, fill=(0, 0, 0, 150))
    d3.text((16, H - 26), foot, font=fs, fill=(255, 255, 255, 255))
    img.alpha_composite(over2)
    return img.convert("RGB")


# ---------------------------------------------------------------- pack

PROMPT = """# Task

It is 11:00 on 14 July 2026 at GridTwin Demo Solar, a 5 MWac solar plant.
The plant has four 1250 kW central inverters, INV-1 to INV-4, one per block (B1 to B4), and it exports through the revenue meter POI-1.
The plant dashboard looks fine, but the owner says this morning's revenue is down.
Energy sells at $45/MWh.

Find out why.

Use the data in this folder (DATA.md describes every file): the SCADA telemetry, the fixed display camera at each inverter, and the fixed thermal camera at each inverter.
Report:

1. the asset at fault and what is wrong with it;
2. the evidence from each source (sensors, camera, infrared), with the numbers;
3. what it is costing, and the work order you would send (see work_orders.json).
"""

DATA = """# Data in this folder

All times are plant local time on 2026-07-14.

## sensors/

`scada_1min.csv` holds SCADA as the plant dashboard received it, one row per minute from 09:01 to 11:00.

| Column | Unit | Meaning |
|---|---|---|
| `t` | | minute |
| `poi_export_kw` | kW | revenue meter POI-1, power exported to the grid |
| `poa_wm2` | W/m2 | plane-of-array irradiance, met station |
| `ambient_c` | C | air temperature, met station |
| `wind_ms` | m/s | wind speed, met station |
| `INV-n_ac_kw` | kW | inverter AC output |
| `INV-n_dc_kw` | kW | inverter DC input |
| `INV-n_dc_v` | V | inverter DC bus voltage |
| `INV-n_heatsink_c` | C | inverter heatsink temperature |
| `INV-n_fan_rpm` | rpm | inverter cooling fan speed |
| `INV-n_status` | | the inverter's reported state, for example running, derated or tripped |
| `INV-n_last_update` | | when SCADA last received a report from the inverter |

`alarms.json` lists the active SCADA alarms at 11:00 and every alarm raised since 09:00.

## camera/

One frame per inverter from the fixed camera facing its cabinet door and display, taken at 11:00:04.
`index.json` gives each frame's camera id, asset and capture time.

## infrared/

One frame per inverter from the fixed thermal camera, taken at 11:00:20.
The thermal camera is a dual-sensor unit mounted with the display camera, and its frames are registered to the display camera's view.

- `INV-n_ir.png`: false-colour image (ironbow), rendered at 640x480 from the 320x240 radiometric grid, with visible-light edges blended in (MSX). The span is locked at 20 to 50 C for every frame, so frames compare directly. Sp1 is a spot meter on the upper louvre of the left door, Sp2 a spot meter on the display, Bx1 a box over the cabinet body.
- `INV-n_ir.csv`: the radiometric data, 240 rows by 320 columns of apparent temperature in C (row 0 is the top of the image), at emissivity 0.95, reflected temperature 20 C, distance 4 m.
- `index.json`: each frame's settings and its Sp1, Sp2 and Bx1 readings. Positions are in the 320x240 grid; Bx1's box is x0, y0, x1, y1 with x1 and y1 exclusive.

## objects.json

One entry per inverter with its block, breaker, rating, latest SCADA reading and the paths of its camera frame, thermal frame and radiometric data.
Paths in objects.json and the index files start with `agent/`, the name of this folder.

## work_orders.json

The work orders the O&M contractor offers, with how long each takes, what it costs and whether the equipment must be de-energized first.
"""


def _counterfactual_loss(minutes):
    """kWh INV-3 would have made since its card died: the mean of the other three units each minute."""
    lost = 0.0
    for _row, truth in minutes[:-1]:  # each row is the minute starting at t, so 11:00 itself is not yet lost
        if not truth["INV-3_comms_ok"] and truth["INV-3_true_ac_kw"] == 0:
            lost += np.mean([truth[f"{i}_true_ac_kw"] for i in INVERTERS if i != "INV-3"]) / 60
    return lost


def build(out=OUT, src_dir=None, allow_bank=False):
    out = Path(out)
    src_dir = Path(src_dir) if src_dir else OUT / "_src"
    sc, minutes, alarms, now = simulate()
    for sub in ("agent", "answer_key"):
        shutil.rmtree(out / sub, ignore_errors=True)
    agent, key = out / "agent", out / "answer_key"
    for p in (agent / "sensors", agent / "camera", agent / "infrared", key):
        p.mkdir(parents=True, exist_ok=True)

    (agent / "PROMPT.md").write_text(PROMPT)
    (agent / "DATA.md").write_text(DATA)
    catalogue = {k: {"minutes": m, "cost_usd": c, "needs_deenergized": d} for k, (m, c, d) in CFG["work_orders"].items()}
    (agent / "work_orders.json").write_text(json.dumps(catalogue, indent=2) + "\n")
    rows, truths = [m[0] for m in minutes], [m[1] for m in minutes]
    with open(agent / "sensors" / "scada_1min.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(key / "hidden_truth.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(truths[0]))
        w.writeheader()
        w.writerows(truths)
    (agent / "sensors" / "alarms.json").write_text(json.dumps(alarms, indent=2) + "\n")

    last = rows[-1]
    objects, cam_index, ir_index, prov, frames = {}, [], [], {}, {}
    for n, unit in enumerate(INVERTERS):
        inv = now["inv"][unit]
        running = inv["status"] == "running" and inv["ac_kw"] > 0
        im, prov[unit] = camera_frame(unit, running, src_dir, allow_bank)
        geo = geometry(im)
        rad = temperature_field(im, geo, now["ambient_c"], inv["heatsink_c"], running, seed=600 + n)
        readings = ir_readings(rad, geo)
        cam_rel, ir_rel, rad_rel = (f"agent/camera/{unit}_display.jpg", f"agent/infrared/{unit}_ir.png",
                                    f"agent/infrared/{unit}_ir.csv")
        stamp_camera(im, unit).save(out / cam_rel, "JPEG", quality=90)
        ir_img = render_ir(rad, im, readings, unit)
        ir_img.save(out / ir_rel)
        np.savetxt(out / rad_rel, rad, fmt="%.2f", delimiter=",")
        frames[unit] = (out / cam_rel, ir_img)
        cam = {"asset": unit, "camera_id": f"CAM-{BLOCK_FOR[unit]}-DSP", "captured": CAPTURED["camera"],
               "file": cam_rel, "resolution": list(FRAME)}
        ir = {"asset": unit, "camera_id": f"IR-{BLOCK_FOR[unit]}-01", "captured": CAPTURED["infrared"], "file": ir_rel,
              "radiometric": rad_rel, "radiometric_size": list(IR), "image_size": list(IR_VIEW), "span_c": list(SPAN),
              "palette": "ironbow",
              "emissivity": EMISSIVITY, "reflected_temp_c": REFLECTED_C, "distance_m": DISTANCE_M, "readings": readings}
        cam_index.append(cam)
        ir_index.append(ir)
        objects[unit] = {
            "kind": "central_inverter", "label": f"{unit} central inverter", "block": BLOCK_FOR[unit],
            "breaker": BREAKER_FOR[unit], "rating_kw": INV_CFG["rating_kwac"],
            "scada_latest": {"t": last["t"], **{k: last[f"{unit}_{k}"] for k in
                             ("ac_kw", "dc_kw", "dc_v", "heatsink_c", "fan_rpm", "status", "last_update")}},
            "sensors": {"file": "agent/sensors/scada_1min.csv",
                        "columns": [c for c in rows[0] if c.startswith(f"{unit}_")]},
            "camera": {k: cam[k] for k in ("file", "camera_id", "captured")},
            "infrared": {k: ir[k] for k in ("file", "radiometric", "camera_id", "captured", "span_c", "readings")},
        }
    (agent / "camera" / "index.json").write_text(json.dumps(cam_index, indent=2) + "\n")
    (agent / "infrared" / "index.json").write_text(json.dumps(ir_index, indent=2) + "\n")
    plant = {"plant": CFG["plant"]["name"], "as_of": last["t"], "meter": {"id": "POI-1", "export_kw": last["poi_export_kw"]},
             "objects": objects}
    (agent / "objects.json").write_text(json.dumps(plant, indent=2) + "\n")

    dash = sum(last[f"{i}_ac_kw"] for i in INVERTERS)
    lost_kwh = _counterfactual_loss(minutes)
    usd = CFG["plant"]["revenue_usd_per_mwh"]
    first_dead = next(t["t"] for t in truths if not t["INV-3_comms_ok"])
    ir3, ir_ok = objects["INV-3"]["infrared"]["readings"], [objects[i]["infrared"]["readings"] for i in INVERTERS if i != "INV-3"]
    finding = {
        "scenario": SCENARIO, "goal_in_sim": sc["goal"], "as_of": last["t"],
        "failed_asset": "INV-3", "object_label": "INV-3 central inverter", "block": "B3", "breaker": "52-I3",
        "failure": (f"INV-3's comms card failed at {first_dead[11:]}. The inverter safe-stopped at 0 kW, but SCADA still shows "
                    f"its last report ({last['INV-3_ac_kw']} kW, {last['INV-3_status']}) with last_update stuck at "
                    f"{last['INV-3_last_update'][11:]}, and no alarm was raised."),
        "failed_to_be": "a running central inverter exporting about 1.2 MW, like INV-1, INV-2 and INV-4",
        "evidence": {
            "sensors": (f"Dashboard sum {dash:.0f} kW vs revenue meter {last['poi_export_kw']:.0f} kW at 11:00 "
                        f"(gap {dash - last['poi_export_kw']:.0f} kW, about one inverter). INV-3's ac_kw, dc_kw, heatsink_c and "
                        f"fan_rpm have not changed since {last['INV-3_last_update'][11:]}; the other units update every minute. "
                        f"No alarms."),
            "camera": "INV-3's display is dark; INV-1, INV-2 and INV-4 are lit and show their output.",
            "infrared": (f"INV-3's upper louvre reads {ir3['Sp1']['temp_c']} C and its cabinet max {ir3['Bx1']['max_c']} C at "
                         f"{now['ambient_c']} C ambient, vs {min(r['Sp1']['temp_c'] for r in ir_ok)} to "
                         f"{max(r['Sp1']['temp_c'] for r in ir_ok)} C and {min(r['Bx1']['max_c'] for r in ir_ok)} to "
                         f"{max(r['Bx1']['max_c'] for r in ir_ok)} C on the running units: it is not making heat, so it is "
                         f"not converting power, though SCADA reports a {last['INV-3_heatsink_c']} C heatsink."),
        },
        "true_state_at_1100": {"INV-3_ac_kw": now["inv"]["INV-3"]["ac_kw"], "INV-3_status": now["inv"]["INV-3"]["status"],
                               "INV-3_heatsink_c": round(now["inv"]["INV-3"]["heatsink_c"], 2), "ambient_c": now["ambient_c"]},
        "cost_so_far": {"lost_mwh": round(lost_kwh / 1000, 3), "lost_usd": round(lost_kwh / 1000 * usd, 2),
                        "window": f"{first_dead[11:]} to 11:00", "basis": "mean of INV-1, INV-2 and INV-4 each minute",
                        "rate_usd_per_mwh": usd,
                        "accept_lost_mwh": [1.45, 1.75],  # dashboard minus meter, peer mean, inverter or meter side
                        "loss_rate_kw_at_1100": round(np.mean([now["inv"][i]["ac_kw"] for i in INVERTERS if i != "INV-3"]))},
        "fix": {"work_order": "reseat_comms_card", "asset": "INV-3", "needs_deenergized": True, "isolate": "open 52-I3",
                "expected_minutes": CFG["work_orders"]["reseat_comms_card"][0],
                "expected_cost_usd": CFG["work_orders"]["reseat_comms_card"][1]},
    }
    (key / "finding.json").write_text(json.dumps(finding, indent=2) + "\n")
    (key / "provenance.json").write_text(json.dumps({
        "sensors": f"gridtwin.sim scenario {SCENARIO}, seed {sc.get('seed')}, no model calls",
        "camera": {u: {k: prov[u].get(k) for k in ("source", "model", "prompt")} for u in INVERTERS},
        "infrared": "modelled by scripts/make_s06_case.py from each camera frame and the sim's true heatsink and ambient "
                    "temperatures at 11:00; not a real thermal camera",
    }, indent=2) + "\n")
    _overview(out / "overview.png", frames, objects, last)
    _readme_case(out / "README.md", finding, objects, last)
    return finding


def _readme_case(path, finding, objects, last):
    """Fill README.md's case section from this build, so its numbers can't drift from the data."""
    if not path.exists():
        return
    o, ok = objects, [u for u in INVERTERS if u != "INV-3"]
    sp = lambda u: o[u]["infrared"]["readings"]["Sp1"]["temp_c"]
    bx = lambda u: o[u]["infrared"]["readings"]["Bx1"]["max_c"]
    s3, c = o["INV-3"]["scada_latest"], finding["cost_so_far"]
    dash = sum(last[f"{i}_ac_kw"] for i in INVERTERS)
    lines = [
        f"- {finding['failure'].split('. ')[0][-5:]}: INV-3's comms card dies, and the inverter safe-stops at 0 kW.",
        f"  SCADA keeps showing its last report ({s3['ac_kw']} kW, {s3['status']}, heatsink {s3['heatsink_c']} C, fan "
        f"{s3['fan_rpm']} rpm) with `last_update` stuck at {s3['last_update'][11:]}, and no alarm is raised.",
        f"- 11:00: the dashboard adds up to {dash:,.0f} kW, while the revenue meter POI-1 reads {last['poi_export_kw']:,.0f} kW, "
        f"a gap of about one inverter.",
        f"- Camera at 11:00: INV-3's display is dark, while {', '.join(ok[:-1])} and {ok[-1]} are lit and show their output.",
        f"- Infrared at 11:00 (ambient {last['ambient_c']} C): INV-3's upper louvre reads {sp('INV-3')} C and its cabinet max "
        f"is {bx('INV-3')} C.",
        f"  On the running units, the louvre reads {min(map(sp, ok))} to {max(map(sp, ok))} C and the cabinet max "
        f"{min(map(bx, ok))} to {max(map(bx, ok))} C.",
        "  INV-3 is making no heat, so it is converting no power, whatever SCADA says.",
        f"- Energy lost from {c['window']} is {c['lost_mwh']:.2f} MWh (${c['lost_usd']:.2f} at ${c['rate_usd_per_mwh']}/MWh), "
        f"counting INV-3 as the {c['basis']}; graders accept {c['accept_lost_mwh'][0]} to {c['accept_lost_mwh'][1]} MWh.",
        f"- The fix is to open 52-I3 and reseat INV-3's comms card (work order `{finding['fix']['work_order']}`, "
        f"{finding['fix']['expected_minutes']} minutes, ${finding['fix']['expected_cost_usd']}).",
    ]
    text = path.read_text()
    a, b = text.index("<!-- case:start -->"), text.index("<!-- case:end -->")
    path.write_text(text[:a] + "<!-- case:start -->\n" + "\n".join(lines) + "\n" + text[b:])


def _overview(path, frames, objects, last):
    """Camera row over infrared row, one column per inverter, for people (not the agent)."""
    cw, ch, pad, head, foot = 480, 360, 16, 124, 44
    W = 4 * cw + 5 * pad
    H = head + 2 * ch + pad + 30 + foot
    sheet = Image.new("RGB", (W, H), (248, 248, 246))
    d = ImageDraw.Draw(sheet)
    ft, fh, fs = _font(26), _font(17), _font(15)
    d.text((pad, 18), "GridTwin Demo Solar at 11:00: what the agent sees for each inverter", font=ft, fill=(20, 20, 20))
    meter = (f"Dashboard sum {sum(last[f'{i}_ac_kw'] for i in INVERTERS):,.0f} kW   ·   revenue meter POI-1 "
             f"{last['poi_export_kw']:,.0f} kW   ·   ambient {last['ambient_c']} °C")
    d.text((pad, 60), meter, font=fh, fill=(70, 70, 70))
    for k, unit in enumerate(INVERTERS):
        x = pad + k * (cw + pad)
        s = objects[unit]["scada_latest"]
        r = objects[unit]["infrared"]["readings"]
        cap = f"{unit}   SCADA {s['ac_kw']:,.0f} kW, {s['status']}, last update {s['last_update'][11:]}"
        d.text((x, head - 12), cap, font=fs, fill=(30, 30, 30), anchor="ls")
        cam = Image.open(frames[unit][0]).resize((cw, ch), Image.LANCZOS)
        sheet.paste(cam, (x, head))
        sheet.paste(frames[unit][1].resize((cw, ch), Image.LANCZOS), (x, head + ch + pad))
        d.text((x, head + 2 * ch + pad + 22), f"Sp1 louvre {r['Sp1']['temp_c']} °C   ·   cabinet max {r['Bx1']['max_c']} °C",
               font=fs, fill=(30, 30, 30), anchor="ls")
    d.text((pad, H - 16), "Simulated plant (GridTwin sim, seed 6). Camera frames are AI-generated stand-ins; infrared is "
           "modelled from the sim's true heatsink and ambient temperatures.", font=fs, fill=(110, 110, 110), anchor="ls")
    sheet.save(path)


def stage(dest, out=OUT):
    """Copy only agent/ into an empty folder, so the agent's working folder holds no answer key."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} is not empty")
    shutil.copytree(Path(out) / "agent", dest / "agent")
    return dest


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", nargs="?", default=str(OUT))
    ap.add_argument("--stage", metavar="DIR", help="copy agent/ into DIR for a live run, instead of building")
    a = ap.parse_args()
    if a.stage:
        print(f"staged {stage(a.stage, a.out)}: start the agent there, with agent/PROMPT.md as its task")
    else:
        f = build(a.out)
        print(json.dumps(f["evidence"], indent=2))

"""A synthetic plant corner for the segmentation tests: three equipment boxes on a table in front of a wall, station
cameras around each box, and the registration file scripts/register_viewpoints.py would write for them.

The scene is built in metres with z up and moved into splat coordinates by tests/splat/synth.py's similarity, like a
real capture. Every Gaussian carries its true asset ("" for the table, the wall and the floaters) to score against.
The stations all stand on the open side of the table, so no camera sees the boxes' backs (the faces towards the wall).
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

from gridtwin.splat.index import SplatIndex, fill_holes
from gridtwin.splat.register import Board, intrinsics
from tests.splat import synth

STEP = 0.003
SIZE, F35 = (960, 720), 26.0
BOARD_CENTRE = np.array([0.12, -0.16, 0.0005])  # where the board lay, flat on the table
WALL_Y = 0.30
# asset -> (low corner, high corner in metres, colour); the boxes stand on the table at z = 0
BOXES = {"INV-1": ((-0.25, 0.02, 0.0), (-0.13, 0.12, 0.22), (70, 76, 84)),
         "XFMR-1": ((0.05, 0.05, 0.0), (0.21, 0.19, 0.12), (88, 112, 92)),
         "CB-1.5": ((-0.04, -0.15, 0.0), (0.02, -0.09, 0.10), (196, 196, 186))}
# view id -> (asset, azimuth in degrees around the box centre, horizontal distance m, height m)
STATIONS = {"inv1_cabinet": ("INV-1", 265, 0.42, 0.40), "inv1_left": ("INV-1", 210, 0.40, 0.36),
            "inv1_right": ("INV-1", 320, 0.42, 0.42), "xfmr1_yard": ("XFMR-1", 270, 0.45, 0.40),
            "xfmr1_left": ("XFMR-1", 215, 0.42, 0.38), "xfmr1_right": ("XFMR-1", 325, 0.44, 0.42),
            "cb15_box": ("CB-1.5", 270, 0.30, 0.30), "cb15_left": ("CB-1.5", 200, 0.32, 0.28),
            "cb15_right": ("CB-1.5", 340, 0.30, 0.32)}


def _grid(a0, a1, b0, b1, step):
    aa, bb = np.meshgrid(np.arange(a0, a1, step), np.arange(b0, b1, step))
    return aa.ravel(), bb.ravel()


def make_scene(seed: int = 0):
    """(centers in splat coordinates, colors, opacity, scale in splat units, true asset per Gaussian)."""
    rng = np.random.default_rng(seed)
    x, y = _grid(-0.4, 0.4, -0.3, WALL_Y, STEP)
    table = np.stack([x, y, np.zeros_like(x)], axis=1)
    x, z = _grid(-0.4, 0.4, 0.0, 0.35, 0.004)
    wall = np.stack([x, np.full_like(x, WALL_Y), z], axis=1)
    parts = [(table, (150, 120, 90), 0.0015, ""), (wall, (182, 180, 172), 0.002, "")]
    parts += [(synth._box(lo, hi, STEP), color, 0.002, asset) for asset, (lo, hi, color) in BOXES.items()]
    centers = np.concatenate([p[0] for p in parts])
    colors = np.concatenate([np.clip(np.array(c) + rng.normal(0, 6, (len(p), 3)), 0, 255) for p, c, _, _ in parts])
    scale = np.concatenate([np.full(len(p), s) for p, _, s, _ in parts])
    truth = np.concatenate([np.full(len(p), a, dtype="<U8") for p, _, _, a in parts])
    floaters = rng.uniform([-0.4, -0.3, 0.02], [0.4, WALL_Y, 0.4], (2000, 3))
    opacity = np.concatenate([np.full(len(centers), 0.95), np.full(len(floaters), 0.05)])
    centers = np.concatenate([centers, floaters])
    colors = np.concatenate([colors, np.full((len(floaters), 3), 200)]).astype(np.uint8)
    scale = np.concatenate([scale, np.full(len(floaters), 0.01)])
    truth = np.concatenate([truth, np.full(len(floaters), "", dtype="<U8")])
    return synth.to_splat(centers), colors, opacity, scale * synth.SCALE, truth


def box_centre(asset):
    lo, hi, _ = BOXES[asset]
    return (np.asarray(lo) + hi) / 2


def first_hit(origin, direction, asset):
    """Where a ray first meets the box (slab method), metres."""
    lo, hi, _ = BOXES[asset]
    with np.errstate(divide="ignore"):
        t0, t1 = (np.asarray(lo) - origin) / direction, (np.asarray(hi) - origin) / direction
    return origin + np.max(np.minimum(t0, t1)) * direction


def station_camera(asset, az, dist, height):
    """World position, then (R, t) in splat coordinates, of a station camera aimed at the box centre."""
    c = box_centre(asset)
    position = c + [dist * np.cos(np.radians(az)), dist * np.sin(np.radians(az)), 0.0]
    position[2] = height
    R_w, t_w = synth.look_at(position, c)
    R = R_w @ synth.splat_rotation().T
    return position, R, synth.SCALE * t_w - R @ synth.SHIFT


def registration(splat_path: Path, photos: dict | None = None, stations: dict = STATIONS) -> dict:
    """The registration document for the stations, as register_viewpoints writes it; photos maps vid -> path."""
    K = intrinsics(*SIZE, F35)
    views = {}
    for vid, (asset, az, dist, height) in stations.items():
        position, R, t = station_camera(asset, az, dist, height)
        forward = box_centre(asset) - position
        target = first_hit(position, forward / np.linalg.norm(forward), asset)
        views[vid] = {"photo": str((photos or {}).get(vid, f"missing/{vid}.jpg")), "focal_35mm": F35, "K": K.tolist(),
                      "R": R.tolist(), "t": t.tolist(), "size": list(SIZE), "rms_px": 0.3, "tags": [0, 1, 2, 3],
                      "position": synth.to_splat(position).tolist(), "target": synth.to_splat(target).tolist(),
                      "up": (-R.T @ [0, 1.0, 0]).tolist(),
                      "fov_deg": float(np.degrees(2 * np.arctan(SIZE[1] / 2 / K[1, 1])))}
    fit = {"scale": synth.SCALE, "R": synth.splat_rotation().tolist(), "t": synth.to_splat(BOARD_CENTRE).tolist(),
           "rms_mm": 0.2, "tags": [0, 1, 2, 3], "source": "synthetic"}
    return {"splat": str(splat_path), "splat_sha256": hashlib.sha256(Path(splat_path).read_bytes()).hexdigest(),
            "board": Board().as_dict(), "board_to_splat": fit, "views": views}


def write_scene(d: Path, with_photos: bool = True, stations: dict = STATIONS):
    """scene.ply, scene.registration.json, viewpoints.yaml and (optionally) a photo per station in d.

    The photos are the splat rendered from each station, so a model sees the same scene the depth masks see.
    Returns (ply path, registration path, viewpoints path, truth).
    """
    centers, colors, opacity, scale, truth = make_scene()
    ply = d / "scene.ply"
    synth.write_ply(ply, centers, colors, opacity, scale)
    photos = {}
    if with_photos:
        (d / "photos").mkdir(exist_ok=True)
        keep = opacity >= 0.3
        index = SplatIndex(centers[keep], colors[keep], scale[keep])
        for vid, (asset, az, dist, height) in stations.items():
            _, R, t = station_camera(asset, az, dist, height)
            color, depth = index.render(intrinsics(*SIZE, F35), R, t, SIZE)
            photos[vid] = d / "photos" / f"{vid}.jpg"
            Image.fromarray(fill_holes(color, depth, 4)).save(photos[vid], quality=92)
    reg = d / "scene.registration.json"
    reg.write_text(json.dumps(registration(ply, photos, stations), indent=1) + "\n")
    vps = d / "viewpoints.yaml"
    entries = {vid: {"asset": s[0], "modes": ["rgb"]} for vid, s in stations.items()}
    vps.write_text(yaml.safe_dump({"viewpoints": entries}))
    return ply, reg, vps, truth

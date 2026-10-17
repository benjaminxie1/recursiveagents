"""Write a hand-placed registration for a scan that has no AprilTag board in it, so the twin can show stations and pins.

The input is a small JSON of points picked in the twin viewer, all in the splat file's own coordinates:

    {"splat": "assets/splats/plant.ply", "up": [0, -1, 0], "origin": [x, y, z], "metres_per_unit": 1.0,
     "source": "hand-placed on ...",
     "stations": {"inv1_cabinet": {"eye": [x, y, z], "target": [x, y, z]}, ...}}

"up" is the world-up direction in the file, "origin" a point on the floor. The output has the same shape as
scripts/register_viewpoints.py writes (board_to_splat plus one view per station), so twin.js needs no special case.

    python -m scripts.place_pins assets/splats/plant.picks.json
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

SIZE, FOV = (1600, 1200), 60.0


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def view(eye, target, up):
    z = unit(np.subtract(target, eye))  # camera looks down +z (OpenCV: x right, y down)
    y = -unit(up - np.dot(up, z) * z)
    x = np.cross(y, z)
    R = np.stack([x, y, z])
    w, h = SIZE
    f = (h / 2) / np.tan(np.radians(FOV) / 2)
    return {
        "photo": None, "focal_35mm": round(float(f * 24 / h), 2),
        "registered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "K": [[f, 0.0, (w - 1) / 2], [0.0, f, (h - 1) / 2], [0.0, 0.0, 1.0]],
        "R": R.round(6).tolist(), "t": (-R @ np.asarray(eye, float)).round(6).tolist(), "size": list(SIZE),
        "rms_px": 0.0, "tags": [], "position": [round(float(c), 4) for c in eye],
        "target": [round(float(c), 4) for c in target], "up": (-y).round(6).tolist(), "fov_deg": FOV,
    }


def main(path):
    p = json.loads(Path(path).read_text())
    up = unit(p["up"])
    # board frame: z = world up, x = any horizontal; splat = s * R @ board + t
    seed = np.array([1.0, 0, 0]) if abs(up[0]) < 0.9 else np.array([0, 0, 1.0])
    bx = unit(seed - np.dot(seed, up) * up)
    R = np.stack([bx, np.cross(up, bx), up], axis=1)
    s = 1.0 / float(p.get("metres_per_unit", 1.0))
    out = {
        "splat": p["splat"], "board": None,
        "board_to_splat": {"scale": s, "R": R.round(6).tolist(), "t": [float(c) for c in p["origin"]], "rms_mm": None,
                           "tags": [], "source": p.get("source", "hand-placed (scripts/place_pins.py)")},
        "views": {vid: view(st["eye"], st["target"], up) for vid, st in p["stations"].items()},
    }
    dst = Path(p["splat"]).with_suffix("").with_suffix(".registration.json")
    dst.write_text(json.dumps(out, indent=1))
    print(f"wrote {dst} with {len(out['views'])} stations")


if __name__ == "__main__":
    main(sys.argv[1])

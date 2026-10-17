"""A synthetic tabletop splat with the board lying on it, and photos of the board from known cameras.

The table can stand in a room (a floor and two walls, each larger than the table), and the board can show only some
of its tags, as if the others were covered. The scene is built in metres with z up, then moved into "splat"
coordinates by a similarity with a non-metric scale and an arbitrary rotation, the way a real capture arrives. Photos
are drawn by warping the printed board with each camera's homography, so they do not depend on the splat renderer they
are checked against.
"""
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from gridtwin.splat.ply import SH_C0
from gridtwin.splat.register import EXIF_IFD, FOCAL_35MM, Board

PX_MM, MARGIN_MM = 10, 12.0
BOARD_CENTRE = np.array([0.05, -0.04, 0.0005])
BOARD_YAW = np.radians(25)
SCALE, ROT_SEED, SHIFT = 2.5, 7, np.array([1.2, -0.7, 3.1])


def rot_z(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def splat_rotation():
    q, r = np.linalg.qr(np.random.default_rng(ROT_SEED).normal(size=(3, 3)))
    q = q * np.sign(np.diag(r))
    return q if np.linalg.det(q) > 0 else -q


def to_splat(p):
    return SCALE * np.asarray(p) @ splat_rotation().T + SHIFT


def look_at(position, target, up=(0, 0, 1.0)):
    """World -> OpenCV camera (x right, y down, z forward)."""
    f = np.asarray(target, float) - position
    f /= np.linalg.norm(f)
    right = np.cross(f, up)
    right /= np.linalg.norm(right)
    R = np.stack([right, np.cross(f, right), f])
    return R, -R @ position


def _board_to_texture(board):
    """3x3 map from board-plane metres (x, y, 1) to pixel centres (col, row, 1) of board.image(PX_MM, MARGIN_MM)."""
    w, h = board.size_mm
    return np.array([[1000 * PX_MM, 0, (w / 2 + MARGIN_MM) * PX_MM - 0.5],
                     [0, -1000 * PX_MM, (h / 2 + MARGIN_MM) * PX_MM - 0.5], [0, 0, 1.0]])


def _texture(board, tags=None):
    """board.image(PX_MM, MARGIN_MM); with tags, the other markers are covered with white paper."""
    tex = board.image(PX_MM, MARGIN_MM)
    side = round(board.marker_mm * PX_MM)
    for k, tag in enumerate(board.ids):
        if tags is not None and tag not in tags:
            x0, y0 = (round((MARGIN_MM + c) * PX_MM) for c in board._top_left_mm(k))
            tex[y0:y0 + side, x0:x0 + side] = 255
    return tex


def _plane(origin, a, b, step):
    """Points on the parallelogram origin + [0, 1) * a + [0, 1) * b, step apart."""
    s, t = np.meshgrid(np.arange(0, 1, step / np.linalg.norm(a)), np.arange(0, 1, step / np.linalg.norm(b)))
    return np.asarray(origin) + s.reshape(-1, 1) * a + t.reshape(-1, 1) * b


def _box(lo, hi, step):
    """Points on the four sides and the top of an axis-aligned box."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    faces = []
    for x in (x0, x1):
        yy, zz = np.meshgrid(np.arange(y0, y1, step), np.arange(z0, z1, step))
        faces.append(np.stack([np.full(yy.size, x), yy.ravel(), zz.ravel()], axis=1))
    for y in (y0, y1):
        xx, zz = np.meshgrid(np.arange(x0, x1, step), np.arange(z0, z1, step))
        faces.append(np.stack([xx.ravel(), np.full(xx.size, y), zz.ravel()], axis=1))
    xx, yy = np.meshgrid(np.arange(x0, x1, step), np.arange(y0, y1, step))
    faces.append(np.stack([xx.ravel(), yy.ravel(), np.full(xx.size, z1)], axis=1))
    return np.concatenate(faces)


def make_scene(board: Board, seed: int = 0, room: bool = False, tags=None):
    """(centers in splat coordinates, colors, opacity, scale in splat units) for a table, the board and a cabinet.

    room=True stands the table 0.75 m above a 2.4 m square floor with two 1.5 m walls, so the table top is only the
    fourth largest plane. tags lists the tags printed on the board (default all).
    """
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.arange(-0.3, 0.3, 0.002), np.arange(-0.25, 0.25, 0.002))
    table = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1)
    table[:, :2] += rng.uniform(-0.0003, 0.0003, (len(table), 2))
    colors = np.clip(np.array([150, 120, 90]) + rng.normal(0, 8, (len(table), 3)), 0, 255)
    tex = _texture(board, tags)
    q = (table - BOARD_CENTRE) @ rot_z(BOARD_YAW)  # each row: rot_z(-yaw) @ (p - centre)
    col, row = (np.c_[q[:, :2], np.ones(len(q))] @ _board_to_texture(board).T)[:, :2].T
    on = (col >= 0) & (col < tex.shape[1] - 1) & (row >= 0) & (row < tex.shape[0] - 1)
    colors[on] = tex[np.rint(row[on]).astype(int), np.rint(col[on]).astype(int)][:, None]
    table[on, 2] = BOARD_CENTRE[2]
    cabinet = _box((-0.25, 0.1, 0.0), (-0.12, 0.2, 0.25), 0.003)
    floaters = rng.uniform([-0.3, -0.25, 0.05], [0.3, 0.25, 0.4], (3000, 3))

    walls = np.zeros((0, 3))
    if room:
        floor_z, x, y, z = -0.75, np.array([2.4, 0, 0]), np.array([0, 2.4, 0]), np.array([0, 0, 1.5])
        walls = np.concatenate([_plane([-1.2, -1.2, floor_z], x, y, 0.006), _plane([-1.2, -1.2, floor_z], y, z, 0.006),
                                _plane([-1.2, 1.2, floor_z], x, z, 0.006)])
        walls += rng.normal(0, 0.0005, walls.shape)

    centers = np.concatenate([table, cabinet, walls, floaters])
    colors = np.concatenate([colors, np.tile([60, 60, 65], (len(cabinet), 1)),
                             np.clip(np.array([185, 180, 170]) + rng.normal(0, 10, (len(walls), 3)), 0, 255),
                             np.full((len(floaters), 3), 200)])
    opacity = np.concatenate([np.full(len(table) + len(cabinet) + len(walls), 0.95), np.full(len(floaters), 0.05)])
    scale = np.concatenate([np.full(len(table), 0.0015), np.full(len(cabinet), 0.002), np.full(len(walls), 0.004),
                            np.full(len(floaters), 0.01)])
    return to_splat(centers), colors.astype(np.uint8), opacity, scale * SCALE


def write_ply(path: Path, centers, colors, opacity, scale) -> None:
    """Binary little-endian PLY in the 3DGS training layout (log scales, logit opacity, SH DC colour)."""
    names = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0", "scale_1", "scale_2",
             "rot_0", "rot_1", "rot_2", "rot_3"]
    v = np.zeros(len(centers), [(n, "<f4") for n in names])
    v["x"], v["y"], v["z"] = centers.T
    for i, ch in enumerate((colors / 255.0 - 0.5).T / SH_C0):
        v[f"f_dc_{i}"] = ch
    v["opacity"] = np.log(opacity / (1 - opacity))
    for i in range(3):
        v[f"scale_{i}"] = np.log(scale)
    v["rot_0"] = 1
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {len(v)}",
              *[f"property float {n}" for n in names], "end_header"]
    with open(path, "wb") as f:
        f.write(("\n".join(header) + "\n").encode())
        f.write(v.tobytes())


def photo(board: Board, K, R, t, size, seed: int = 0, show_board: bool = True, tags=None) -> np.ndarray:
    """RGB photo from a metric-frame camera: table-coloured noise with the printed board warped in (only tags, if
    given, are uncovered)."""
    rng = np.random.default_rng(seed)
    W, H = size
    out = np.clip(np.array([150, 120, 90]) + rng.normal(0, 8, (H, W, 3)), 0, 255).astype(np.uint8)
    if not show_board:
        return out
    Rb = R @ rot_z(BOARD_YAW)
    plane = K @ np.column_stack([Rb[:, 0], Rb[:, 1], R @ BOARD_CENTRE + t])  # board metres -> pixels
    Hm = plane @ np.linalg.inv(_board_to_texture(board))
    tex = cv2.GaussianBlur(_texture(board, tags), (0, 0), 2.0)
    warped = cv2.warpPerspective(tex, Hm, size, flags=cv2.INTER_LINEAR, borderValue=0)
    mask = cv2.warpPerspective(np.full(tex.shape, 255, np.uint8), Hm, size, flags=cv2.INTER_NEAREST)
    out[mask > 127] = warped[mask > 127][:, None]
    return out


def save_jpeg(path: Path, rgb: np.ndarray, focal_35mm: float | None, rotated: bool = False) -> None:
    """With rotated=True the pixels are stored on their side with EXIF Orientation 6, the way an iPhone saves them."""
    im = Image.fromarray(np.ascontiguousarray(np.rot90(rgb, 1)) if rotated else rgb)
    exif = im.getexif()
    if focal_35mm:
        exif.get_ifd(EXIF_IFD)[FOCAL_35MM] = focal_35mm
    if rotated:
        exif[0x0112] = 6
    im.save(path, quality=92, exif=exif)

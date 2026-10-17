"""Which Gaussians of a splat make up each piece of equipment, from the station photos registered into it.

A station photo is aimed at its asset: the registration's `target` is the splat point at the image centre.
1. Masks. A provider marks the asset's pixels in each view at a working resolution. DepthMasks needs no model: it
   renders the splat from the view and grows the region around the centre pixel while depth stays continuous, never onto
   the support plane the board lay on, optionally only through colours near the seed's. SamMasks
   (gridtwin/splat/segment_sam.py) prompts SAM 2.1 on the station photo with the same seed and the depth mask's box.
2. Votes. Every Gaussian centre is projected into every view of an asset. A view votes on a Gaussian only if the centre
   lands inside the image, in front of the camera and within vis_tol of the view's z-buffer depth there, so Gaussians
   hidden behind something do not vote. A Gaussian is the asset's candidate when its in-mask votes beat its out-of-mask
   votes, so it was seen in-mask at least once.
3. Clean. Each asset keeps the voxel-connected pieces of its candidates that hold one of its views' seeds, which drops
   mask bleed onto whatever is behind the silhouette. A Gaussian that is still a candidate of several assets goes to
   the one with the largest in-mask share of its votes. Last, an asset takes the opaque Gaussians that none of its views
   saw inside its footprint on the support plane and its height range (the back of a cabinet shot from the front).

Contract: assets/splats/<stem>.assets.json, written by scripts/segment_assets.py and read by the viewer.
    {"splat": "assets/splats/scene.ply", "splat_sha256": "<sha256 of the PLY file>", "vertex_count": 812345,
     "method": "depth", "created_at": "2026-10-17T14:05:00-07:00",
     "assets": {"INV-1": {"count": 41210, "centroid": [x, y, z], "bbox": [[x0, y0, z0], [x1, y1, z1]],
                          "indices_b64": "<base64>"}},
     "views_used": {"inv1_cabinet": {"asset": "INV-1", "mask_coverage": 0.214, "voted": 30411, "in_mask": 20975}}}
- method is "depth" or "sam:<model id>"; created_at is local ISO 8601 with its UTC offset.
- centroid and bbox are the mean, minimum and maximum of the asset's Gaussian centres in the splat file's own
  coordinates, the registration's (x_cam = R @ x_splat + t). An asset whose views all voted for nothing has count 0
  and null centroid and bbox.
- indices_b64 is base64 of little-endian uint32 indices into the PLY's vertex element (index i is the i-th vertex in
  the file), sorted ascending, `count` of them. No index is under two assets.
- mask_coverage is the fraction of the view's pixels in its mask; voted and in_mask count the Gaussians that view saw,
  and saw inside its mask.
- Spark 2.3.1 keeps the file order: a SplatMesh loaded without `lod` streams the PLY into packedSplats vertex by
  vertex, so splat i is vertex i (recolour with packedSplats.setSplat(i, ...), then packedSplats.needsUpdate = true).
  With `lod` on, Spark builds a separate LoD set that merges and reorders splats, so keep it off for this mesh.
  Check vertex_count against packedSplats.numSplats and splat_sha256 against the registration before recolouring.
"""
import base64
from dataclasses import dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np

from gridtwin.splat.index import SplatIndex, fill_holes
from gridtwin.splat.ply import Splat
from gridtwin.splat.register import load_photo, scaled

NONE = -1  # label of a Gaussian that belongs to no asset
OFFSETS = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dy, dz) > (0, 0, 0)]


class MaskError(ValueError):
    """A view gave no mask; the message says why."""


@dataclass(frozen=True)
class Camera:
    """A registered station view in splat coordinates (OpenCV: x_cam = R @ x_splat + t) at size (W, H)."""
    vid: str
    asset: str
    K: np.ndarray
    R: np.ndarray
    t: np.ndarray
    size: tuple
    target: np.ndarray
    photo: Path | None = None

    @classmethod
    def from_view(cls, vid: str, asset: str, view: dict, root: Path = Path(".")) -> "Camera":
        """From one entry of a registration file's `views`; its photo path is relative to root (the repo)."""
        return cls(vid, asset, np.asarray(view["K"], float), np.asarray(view["R"], float),
                   np.asarray(view["t"], float), tuple(view["size"]), np.asarray(view["target"], float),
                   Path(root) / view["photo"] if view.get("photo") else None)

    def at_width(self, width: int) -> "Camera":
        """The same camera at width pixels (never above its own), aspect kept."""
        width = min(width, self.size[0])
        f = width / self.size[0]
        return replace(self, K=scaled(self.K, f), size=(width, max(1, round(self.size[1] * f))))

    @property
    def position(self) -> np.ndarray:
        return -self.R.T @ self.t

    @property
    def distance(self) -> float:
        """Camera to target in splat units; a view's tolerances are fractions of it."""
        return float(np.linalg.norm(self.target - self.position))


@dataclass(frozen=True)
class Plane:
    """The support plane the board lay on during capture: a point, and the unit normal on the cameras' side."""
    point: np.ndarray
    normal: np.ndarray

    @classmethod
    def from_registration(cls, registration: dict, cameras=()) -> "Plane":
        """The board's z axis (out of the paper) through its centre, flipped if the cameras are on the other side."""
        fit = registration["board_to_splat"]
        point, n = np.asarray(fit["t"], float), np.asarray(fit["R"], float)[:, 2]
        n = n / np.linalg.norm(n)
        if len(cameras) and np.mean([(c.position - point) @ n for c in cameras]) < 0:
            n = -n
        return cls(point, n)

    def height(self, points) -> np.ndarray:
        return (np.asarray(points) - self.point) @ self.normal


@dataclass
class Render:
    """The opaque Gaussians seen from a camera: colour with holes filled, and the z-depth of the nearest centre per
    pixel, holes between discs filled from their nearest neighbour (inf where nothing is near)."""
    color: np.ndarray  # (H, W, 3) uint8
    depth: np.ndarray  # (H, W) float32


def render_view(index: SplatIndex, cam: Camera, max_radius: int = 4, fill: int = 2) -> Render:
    color, depth = index.render(cam.K, cam.R, cam.t, cam.size, max_radius)
    d = depth.astype(np.float32)
    # a min filter: holes take the nearest surface. The default border would pad with FLT_MAX, a finite "depth".
    near = cv2.erode(d, np.ones((2 * fill + 1, 2 * fill + 1), np.uint8), borderType=cv2.BORDER_REPLICATE)
    return Render(fill_holes(color, depth), np.where(np.isfinite(d), d, near))


@dataclass
class Mask:
    pixels: np.ndarray      # (H, W) bool at the camera's size
    seed: tuple             # (x, y): the pixel on the asset the mask is anchored to
    seed_point: np.ndarray  # (3,) that pixel lifted into the splat


def lift_pixels(cam: Camera, depth: np.ndarray) -> np.ndarray:
    """(H, W, 3) splat point under every pixel at its depth (inf or nan where the depth is inf)."""
    H, W = depth.shape
    ys, xs = np.mgrid[0:H, 0:W]
    with np.errstate(invalid="ignore"):
        pts = SplatIndex.lift(cam.K, cam.R, cam.t, np.c_[xs.ravel(), ys.ravel()], depth.ravel().astype(np.float64))
    return pts.reshape(H, W, 3)


def depth_steps(depth: np.ndarray, tol: float) -> np.ndarray:
    """Pixels on the far side of a relative depth step above tol between 4-neighbours.

    Only the far side is marked, so a surface in front keeps its whole outline and the one behind it loses a pixel.
    """
    cut = np.zeros(depth.shape, bool)
    with np.errstate(invalid="ignore"):
        for a, b, sa, sb in ((depth[:-1], depth[1:], np.s_[:-1], np.s_[1:]),
                             (depth[:, :-1], depth[:, 1:], np.s_[:, :-1], np.s_[:, 1:])):
            step = np.abs(a - b) > tol * np.minimum(a, b)
            cut[sa] |= step & (a > b)
            cut[sb] |= step & (b > a)
    return cut


@dataclass
class DepthMasks:
    """The asset as the connected region of continuous depth around the image centre, off the support plane."""
    depth_tol: float = 0.03         # largest relative depth step between neighbouring pixels of one surface
    plane_tol: float = 0.01         # pixels this close to the support plane (times the view distance) are floor
    color_tol: float | None = None  # if set, also stay within this CIELAB distance of the seed's colour
    seed_window: float = 0.05       # when the centre pixel is floor or empty, seed at the nearest pixel this close
    name: str = "depth"

    def __call__(self, cam: Camera, render: Render, plane: Plane | None) -> Mask:
        d = render.depth
        points = lift_pixels(cam, d)
        free = np.isfinite(d)
        if plane is not None:
            with np.errstate(invalid="ignore"):
                free &= plane.height(points) > self.plane_tol * cam.distance
        grow = free & ~depth_steps(d, self.depth_tol)
        x, y = self.seed(grow)
        if self.color_tol is not None:
            lab = cv2.cvtColor(render.color.astype(np.float32) / 255, cv2.COLOR_RGB2LAB)
            near = np.s_[max(0, y - 2):y + 3, max(0, x - 2):x + 3]
            ref = np.median(lab[near][grow[near]], axis=0)
            grow &= np.linalg.norm(lab - ref, axis=2) <= self.color_tol
            grow[y, x] = True
        comp = cv2.connectedComponents(grow.astype(np.uint8), connectivity=4)[1]
        pixels = cv2.morphologyEx((comp == comp[y, x]).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        return Mask(pixels.astype(bool) & free, (x, y), points[y, x])

    def seed(self, ok: np.ndarray) -> tuple:
        """The pixel of ok nearest the image centre, within seed_window of the width."""
        H, W = ok.shape
        cx, cy = (W - 1) / 2, (H - 1) / 2
        r = max(1, round(self.seed_window * W))
        x0, y0 = max(0, int(cx) - r), max(0, int(cy) - r)
        ys, xs = np.nonzero(ok[y0:int(cy) + r + 2, x0:int(cx) + r + 2])
        if not len(xs):
            raise MaskError("nothing but floor or empty space at the image centre")
        k = np.argmin((xs + x0 - cx) ** 2 + (ys + y0 - cy) ** 2)
        return int(xs[k] + x0), int(ys[k] + y0)


@dataclass
class SamMasks:
    """SAM on the station photo, prompted with a positive point at the depth seed and a box around the depth mask.

    predictor.predict(rgb, point, box) -> (masks (K, H, W) bool, predicted IoU (K,)), from
    gridtwin/splat/segment_sam.load_predictor. The mask with the best predicted IoU is kept, brought to the working
    size and eroded by erode_px so registration error does not carry it onto the background.
    """
    predictor: object
    model: str = "facebook/sam2.1-hiera-tiny"
    depth: DepthMasks = field(default_factory=DepthMasks)
    use_box: bool = True
    box_pad: float = 0.05     # the box grows by this fraction of the depth mask's larger side
    long_side: int = 1024     # the photo is shrunk to this before the model sees it
    erode_px: int = 1         # about the registration error at the working width

    @property
    def name(self) -> str:
        return f"sam:{self.model}"

    def __call__(self, cam: Camera, render: Render, plane: Plane | None) -> Mask:
        if cam.photo is None or not Path(cam.photo).exists():
            raise MaskError(f"no photo at {cam.photo}")
        rgb = load_photo(cam.photo)[0]
        W, H = cam.size
        if abs(rgb.shape[1] / rgb.shape[0] - W / H) > 0.01:
            raise MaskError(f"photo is {rgb.shape[1]}x{rgb.shape[0]}, not the registered aspect {W}:{H}")
        f = min(1.0, self.long_side / max(rgb.shape[:2]))
        if f < 1:
            rgb = cv2.resize(rgb, (round(rgb.shape[1] * f), round(rgb.shape[0] * f)), interpolation=cv2.INTER_AREA)
        s = rgb.shape[1] / W  # working pixels -> model pixels, centres at integers in both
        try:
            guide = self.depth(cam, render, plane)
        except MaskError:
            guide = None
        x, y = guide.seed if guide else ((W - 1) // 2, (H - 1) // 2)
        box = None
        if guide is not None and self.use_box:
            ys, xs = np.nonzero(guide.pixels)
            pad = self.box_pad * max(np.ptp(xs), np.ptp(ys)) + 0.5
            box = np.clip([(xs.min() - pad + 0.5) * s - 0.5, (ys.min() - pad + 0.5) * s - 0.5,
                           (xs.max() + pad + 0.5) * s - 0.5, (ys.max() + pad + 0.5) * s - 0.5],
                          0, [rgb.shape[1] - 1, rgb.shape[0] - 1] * 2)
        masks, scores = self.predictor.predict(rgb, ((x + 0.5) * s - 0.5, (y + 0.5) * s - 0.5), box)
        best = masks[int(np.argmax(scores))].astype(np.float32)
        pixels = cv2.resize(best, (W, H), interpolation=cv2.INTER_AREA) > 0.5
        if self.erode_px:
            k = 2 * self.erode_px + 1
            pixels = cv2.erode(pixels.astype(np.uint8), np.ones((k, k), np.uint8)).astype(bool)
        if not pixels.any():
            raise MaskError("SAM gave an empty mask")
        if not pixels[y, x]:
            ys, xs = np.nonzero(pixels)
            k = np.argmin((xs - x) ** 2 + (ys - y) ** 2)
            x, y = int(xs[k]), int(ys[k])
        point = lift_pixels(cam, render.depth)[y, x]
        return Mask(pixels, (x, y), point)


def project(centers: np.ndarray, cam: Camera):
    """Flat pixel index (-1 outside the image or behind the camera) and z-depth per centre, rounded as render does."""
    p = centers @ cam.R.T + cam.t
    z = p[:, 2]
    W, H = cam.size
    with np.errstate(divide="ignore", invalid="ignore"):
        u = np.rint(cam.K[0, 0] * p[:, 0] / z + cam.K[0, 2])
        v = np.rint(cam.K[1, 1] * p[:, 1] / z + cam.K[1, 2])
        ok = (z > 1e-9) & (u >= 0) & (u < W) & (v >= 0) & (v < H)
    return np.where(ok, v * W + u, -1).astype(np.int64), z


def vote(centers: np.ndarray, cams: list, renders: dict, masks: dict, assets: list, vis_tol: float = 0.03):
    """inside[a, g] and seen[a, g]: how many views of asset a see Gaussian g inside their mask, and see it at all.

    A view sees a Gaussian when its centre is in the image and within vis_tol (relative) of the z-buffer depth there.
    Also returns {vid: (seen, seen in-mask)}.
    """
    inside = np.zeros((len(assets), len(centers)), np.uint16)
    seen = np.zeros_like(inside)
    stats = {}
    for cam in cams:
        a = assets.index(cam.asset)
        pix, z = project(centers, cam)
        idx = np.flatnonzero(pix >= 0)
        zb = renders[cam.vid].depth.ravel()[pix[idx]]
        idx = idx[np.isfinite(zb) & (np.abs(z[idx] - zb) <= vis_tol * zb)]
        hit = idx[masks[cam.vid].pixels.ravel()[pix[idx]]]
        seen[a, idx] += 1
        inside[a, hit] += 1
        stats[cam.vid] = (len(idx), len(hit))
    return inside, seen, stats


def assign(inside: np.ndarray, seen: np.ndarray, candidate: np.ndarray | None = None) -> np.ndarray:
    """Asset index per Gaussian, NONE for none: among the assets whose in-mask votes beat their out-of-mask votes
    (and that candidate allows), the largest in-mask share wins, then the most in-mask votes, then the first asset."""
    inside = inside.astype(np.int32)
    wins = 2 * inside > seen
    if candidate is not None:
        wins &= candidate
    share = np.where(wins, inside / np.maximum(seen, 1), -1.0)
    best = np.argmax(np.where(share == share.max(axis=0), inside, -1), axis=0)
    return np.where(wins.any(axis=0), best, NONE)


def components(points: np.ndarray, voxel: float) -> np.ndarray:
    """Label per point of its 26-connected piece on a grid of voxel-sized cells (labels are arbitrary integers)."""
    key = np.floor((points - points.min(0)) / voxel).astype(np.int64) + 1  # +1 and +2 below: neighbours never wrap
    dims = key.max(0) + 2
    flat = (key[:, 0] * dims[1] + key[:, 1]) * dims[2] + key[:, 2]
    cells, of = np.unique(flat, return_inverse=True)
    src, dst = [], []
    for dx, dy, dz in OFFSETS:
        nb = cells + (dx * dims[1] + dy) * dims[2] + dz
        j = np.minimum(np.searchsorted(cells, nb), len(cells) - 1)
        hit = np.flatnonzero(cells[j] == nb)
        src.append(hit)
        dst.append(j[hit])
    src, dst = np.concatenate(src), np.concatenate(dst)
    label = np.arange(len(cells))  # each cell points at a cell of its piece with a lower index, until all agree
    while True:
        low = np.minimum(label[src], label[dst])
        new = label.copy()
        np.minimum.at(new, src, low)
        np.minimum.at(new, dst, low)
        new = new[new]
        if np.array_equal(new, label):
            return label[of.ravel()]
        label = new


def keep_seeded(points: np.ndarray, seeds, voxel: float) -> np.ndarray:
    """Mask over points: the voxel-connected pieces that hold the point nearest each seed."""
    if not len(points):
        return np.zeros(0, bool)
    label = components(points, voxel)
    keep = [label[np.argmin(((points - s) ** 2).sum(1))] for s in seeds if np.all(np.isfinite(s))]
    return np.isin(label, keep) if keep else np.ones(len(points), bool)


def fill_unseen(centers: np.ndarray, members: np.ndarray, open_: np.ndarray, plane: Plane, floor: float) -> np.ndarray:
    """Indices among open_ (Gaussians free to take) inside the convex footprint, on the support plane, of the members
    higher than floor above it, and within their height range."""
    n = plane.normal
    e1 = np.cross(n, [1.0, 0, 0] if abs(n[0]) < 0.9 else [0, 1.0, 0])
    e1 /= np.linalg.norm(e1)
    basis = np.stack([e1, np.cross(n, e1)], axis=1)
    rel = centers[members] - plane.point
    rel = rel[rel @ n > floor]
    if len(rel) < 3:
        return np.zeros(0, np.int64)
    flat, h = rel @ basis, rel @ n
    hull = cv2.convexHull(flat.astype(np.float32))[:, 0].astype(np.float64)
    cand = np.flatnonzero(open_)
    rel = centers[cand] - plane.point
    fc, hc = rel @ basis, rel @ n
    lo, hi = flat.min(0), flat.max(0)
    ok = (hc >= h.min()) & (hc <= h.max()) & np.all((fc >= lo) & (fc <= hi), axis=1)
    cand, fc = cand[ok], fc[ok]
    if len(hull) < 3 or not len(cand):
        return np.zeros(0, np.int64)
    edge = np.roll(hull, -1, axis=0) - hull
    cross = edge[:, 0] * (fc[:, None, 1] - hull[:, 1]) - edge[:, 1] * (fc[:, None, 0] - hull[:, 0])
    eps = 1e-12 * float(np.abs(edge).max()) ** 2
    return cand[np.all(cross >= -eps, axis=1) | np.all(cross <= eps, axis=1)]


@dataclass
class Segmentation:
    assets: list     # asset ids, sorted; labels index into this list
    labels: np.ndarray  # (N,) per PLY vertex: index into assets, or NONE
    views: dict      # vid -> {"asset", "mask_coverage", "voted", "in_mask"} for every view that voted
    failed: list     # (vid, reason) for every view that gave no mask
    cameras: dict    # vid -> Camera at the working size
    renders: dict    # vid -> Render
    masks: dict      # vid -> Mask


def segment(splat: Splat, cameras: list, masker, plane: Plane | None = None, width: int = 640,
            min_opacity: float = 0.3, vis_tol: float = 0.03, floor_tol: float = 0.01, clean_voxel: float | None = 0.03,
            fill: bool = True, max_radius: int = 4) -> Segmentation:
    """Labels every Gaussian with the asset its station views agree on (see the module docstring).

    masker(camera, render, plane) -> Mask, or raises MaskError. The z-buffer comes from Gaussians at min_opacity or
    more; every Gaussian votes. Gaussians within floor_tol of the support plane are floor and get no asset. floor_tol,
    clean_voxel (None to skip the clean pass) and the masker's tolerances are fractions of the asset's median
    camera-to-target distance; vis_tol is relative to depth.
    """
    opaque = splat.opacity >= min_opacity
    index = SplatIndex(splat.centers[opaque], splat.colors[opaque], splat.scale[opaque])
    cams = {c.vid: c.at_width(width) for c in cameras}
    renders, masks, failed = {}, {}, []
    for vid, cam in cams.items():
        renders[vid] = render_view(index, cam, max_radius)
        try:
            masks[vid] = masker(cam, renders[vid], plane)
        except MaskError as e:
            failed.append((vid, str(e)))
    voting = [cams[vid] for vid in masks]
    assets = sorted({c.asset for c in voting})
    inside, seen, stats = vote(splat.centers, voting, renders, masks, assets, vis_tol)
    candidate = 2 * inside.astype(np.int32) > seen
    distance = [float(np.median([c.distance for c in voting if c.asset == asset])) for asset in assets]
    height = plane.height(splat.centers) if plane is not None else np.full(len(splat.centers), np.inf)
    for a, asset in enumerate(assets):
        candidate[a] &= height > floor_tol * distance[a]
        if clean_voxel:
            mine = np.flatnonzero(candidate[a])
            seeds = [masks[c.vid].seed_point for c in voting if c.asset == asset]
            candidate[a, mine[~keep_seeded(splat.centers[mine], seeds, clean_voxel * distance[a])]] = False
    labels = assign(inside, seen, candidate)
    if fill and plane is not None:
        for a in range(len(assets)):
            add = fill_unseen(splat.centers, labels == a, (labels == NONE) & (seen[a] == 0) & opaque, plane,
                              floor_tol * distance[a])
            labels[add] = a
    views = {c.vid: {"asset": c.asset, "mask_coverage": round(float(masks[c.vid].pixels.mean()), 4),
                     "voted": stats[c.vid][0], "in_mask": stats[c.vid][1]} for c in voting}
    return Segmentation(assets, labels, views, failed, cams, renders, masks)


def encode_indices(indices) -> str:
    return base64.b64encode(np.sort(np.asarray(indices, np.int64)).astype("<u4").tobytes()).decode("ascii")


def decode_indices(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), "<u4").astype(np.int64)


def assets_doc(seg: Segmentation, centers: np.ndarray, splat: str, sha256: str, method: str, created_at: str) -> dict:
    """The assets.json document of the module docstring."""
    def r(a):
        return [round(float(x), 6) for x in a]

    assets = {}
    for a, asset in enumerate(seg.assets):
        idx = np.flatnonzero(seg.labels == a)
        pts = centers[idx]
        assets[asset] = {"count": int(len(idx)), "centroid": r(pts.mean(0)) if len(idx) else None,
                         "bbox": [r(pts.min(0)), r(pts.max(0))] if len(idx) else None,
                         "indices_b64": encode_indices(idx)}
    return {"splat": splat, "splat_sha256": sha256, "vertex_count": int(len(centers)), "method": method,
            "created_at": created_at, "assets": assets, "views_used": seg.views}

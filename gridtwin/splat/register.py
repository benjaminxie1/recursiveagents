"""Find a printed AprilTag board inside a splat, then place every photo that sees the board in splat coordinates.

The board lies flat in the scene while the splat is captured. We fit the scene's dominant planes, render each one straight
down from both sides, and detect the tags in those renders. Matching their corners gives the board-to-splat similarity
(scale, rotation, translation), so the splat needs neither metric scale nor any particular up axis. A photo that sees the
board gets its camera from PnP, and the similarity carries that camera into splat coordinates. A print that came out at
the wrong scale changes both steps by the same factor, so it cancels out of the final poses.
"""
import math
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps

from gridtwin.splat.index import SplatIndex, fill_holes

TAGS = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
EXIF_IFD, FOCAL_35MM = 0x8769, 41989
FULL_FRAME_DIAGONAL_MM = math.hypot(36, 24)


@dataclass(frozen=True)
class Board:
    """tag36h11 markers in a grid, row-major as printed.

    Board frame: origin at the grid centre, x right and y up as printed, z out of the paper, metres.
    """
    ids: tuple = (0, 1, 2, 3)
    cols: int = 2
    marker_mm: float = 80.0
    gap_mm: float = 16.0

    @property
    def size_mm(self) -> tuple[float, float]:
        pitch, rows = self.marker_mm + self.gap_mm, len(self.ids) // self.cols
        return self.cols * pitch - self.gap_mm, rows * pitch - self.gap_mm

    def _top_left_mm(self, k: int) -> tuple[float, float]:
        """Marker k's top-left corner in print coordinates: mm from the grid's top-left, y down."""
        pitch = self.marker_mm + self.gap_mm
        return (k % self.cols) * pitch, (k // self.cols) * pitch

    def marker_corners(self) -> dict[int, np.ndarray]:
        """id -> (4, 3) corners in detector order: top-left, top-right, bottom-right, bottom-left as printed."""
        w, h = self.size_mm
        m = self.marker_mm
        out = {}
        for k, tag in enumerate(self.ids):
            x0, y0 = self._top_left_mm(k)
            pts = [(x0, y0), (x0 + m, y0), (x0 + m, y0 + m), (x0, y0 + m)]
            out[tag] = np.array([[(x - w / 2) / 1000, (h / 2 - y) / 1000, 0.0] for x, y in pts])
        return out

    def outline(self) -> np.ndarray:
        """(4, 3) outer corners of the marker grid: top-left, top-right, bottom-right, bottom-left as printed."""
        w, h = (s / 2000 for s in self.size_mm)
        return np.array([[-w, h, 0.0], [w, h, 0.0], [w, -h, 0.0], [-w, -h, 0.0]])

    def image(self, px_per_mm: float, margin_mm: float = 12.0) -> np.ndarray:
        """Grayscale print of the grid inside a white margin; pixel column c spans print mm [c, c + 1) / px_per_mm."""
        w, h = self.size_mm
        img = np.full((round((h + 2 * margin_mm) * px_per_mm), round((w + 2 * margin_mm) * px_per_mm)), 255, np.uint8)
        side = round(self.marker_mm * px_per_mm)
        for k, tag in enumerate(self.ids):
            x0, y0 = (round((margin_mm + c) * px_per_mm) for c in self._top_left_mm(k))
            img[y0:y0 + side, x0:x0 + side] = cv2.aruco.generateImageMarker(TAGS, tag, side, borderBits=1)
        return img

    def as_dict(self) -> dict:
        return {"family": "tag36h11", "ids": list(self.ids), "cols": self.cols, "marker_mm": self.marker_mm,
                "gap_mm": self.gap_mm}


def detect(gray: np.ndarray, board: Board):
    """Board corners in an image: (N, 2) pixels, the matching (N, 3) board points, and the tag ids found."""
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    corners, ids, _ = cv2.aruco.ArucoDetector(TAGS, params).detectMarkers(gray)
    model = board.marker_corners()
    px, obj, found = [], [], []
    for c, tag in zip(corners, [] if ids is None else ids.ravel().tolist()):
        if tag in model and tag not in found:
            px.append(c.reshape(4, 2).astype(np.float64))
            obj.append(model[tag])
            found.append(tag)
    if not found:
        return np.zeros((0, 2)), np.zeros((0, 3)), []
    return np.concatenate(px), np.concatenate(obj), found


def similarity(src: np.ndarray, dst: np.ndarray):
    """Least-squares s, R, t with dst ~ s * R @ src + t (Umeyama); a planar src is fine."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    U, S, Vt = np.linalg.svd(b.T @ a / len(src))
    D = np.eye(3)
    D[2, 2] = 1.0 if np.linalg.det(U @ Vt) >= 0 else -1.0
    R = U @ D @ Vt
    s = float((S * np.diag(D)).sum() / (a ** 2).sum(1).mean())
    return s, R, mu_d - s * R @ mu_s


@dataclass
class BoardFit:
    """Board frame -> splat coordinates: X_splat = scale * R @ X_board + t."""
    scale: float
    R: np.ndarray
    t: np.ndarray
    rms_mm: float
    tags: list
    source: str

    def as_dict(self) -> dict:
        return {"scale": self.scale, "R": self.R.tolist(), "t": self.t.tolist(), "rms_mm": self.rms_mm,
                "tags": self.tags, "source": self.source}


def _fit(obj: np.ndarray, pts: np.ndarray, tags: list, source: str) -> BoardFit:
    s, R, t = similarity(obj, pts)
    rms = float(np.sqrt(np.mean(np.sum((s * obj @ R.T + t - pts) ** 2, axis=1)))) / s * 1000
    return BoardFit(s, R, t, rms, tags, source)


def board_from_corners(board: Board, corners: np.ndarray) -> BoardFit:
    """From the grid's four outer corners picked by hand in splat coordinates (Board.outline order)."""
    pts = np.asarray(corners, float)
    if pts.shape != (4, 3) or not np.isfinite(pts).all():
        raise ValueError(f"needs 4 corners of 3 numbers each, got shape {pts.shape}")
    return _fit(board.outline(), pts, list(board.ids), "picked corners")


def dominant_planes(points: np.ndarray, k: int = 8, iters: int = 400, seed: int = 0, min_frac: float = 0.01):
    """Planes by sequential RANSAC on a sample of the points, largest first: (centre, unit normal, inlier threshold,
    inliers in the sample). Stops after k planes or once the best plane left holds under min_frac of the sample.

    Half the candidate triples are drawn from one voxel each, so a small plane such as a table among a room's floor and
    walls still gets candidates that lie on it.
    """
    rng = np.random.default_rng(seed)
    pts = points[rng.choice(len(points), min(len(points), 30000), replace=False)]
    lo, hi = np.percentile(points, [2, 98], axis=0)
    thr = 0.004 * float(np.linalg.norm(hi - lo))
    least = max(50, int(min_frac * len(pts)))
    planes = []
    while len(planes) < k and len(pts) >= least:
        vox = np.floor((pts - pts.min(0)) / (8 * thr)).astype(np.int64) @ np.array([1 << 42, 1 << 21, 1])
        order = np.argsort(vox)
        sv = vox[order]
        seed_at = rng.integers(0, len(pts), iters // 2)
        lo_i, hi_i = np.searchsorted(sv, sv[seed_at], "left"), np.searchsorted(sv, sv[seed_at], "right")
        mates = lo_i[:, None] + (rng.random((len(seed_at), 2)) * (hi_i - lo_i)[:, None]).astype(np.int64)
        tri = np.concatenate([pts[rng.integers(0, len(pts), (iters - len(seed_at), 3))],
                              pts[order[np.c_[seed_at, mates]]]])
        normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        norm = np.linalg.norm(normals, axis=1)
        good = norm > 1e-12
        normals = normals[good] / norm[good, None]
        offs = np.einsum("ij,ij->i", normals, tri[good, 0])
        counts = np.concatenate([(np.abs(pts @ normals[i:i + 64].T - offs[i:i + 64]) < thr).sum(0)
                                 for i in range(0, len(normals), 64)])
        best = int(np.argmax(counts))
        inl = np.abs(pts @ normals[best] - offs[best]) < thr
        if inl.sum() < least:
            break
        centre = pts[inl].mean(0)
        normal = np.linalg.svd(pts[inl] - centre, full_matrices=False)[2][2]
        on = np.abs((pts - centre) @ normal) < thr
        planes.append((centre, normal, thr, int(on.sum())))
        pts = pts[~on]
    return sorted(planes, key=lambda p: -p[3])


def _regions(rel: np.ndarray, u: np.ndarray, w: np.ndarray, cells: int = 64, most: int = 3, min_frac: float = 0.02):
    """Index arrays of the separate clusters of a plane slab's points (rel to a point on the plane, u and w spanning
    it), largest first. A room's table-height slab holds the table top and thin strips of every wall; rendering the
    table alone keeps its pixels small."""
    a, b = rel @ u, rel @ w
    (alo, ahi), (blo, bhi) = np.percentile(a, [0.5, 99.5]), np.percentile(b, [0.5, 99.5])
    cell = max(ahi - alo, bhi - blo, 1e-12) / cells
    ia = np.clip(((a - alo) / cell).astype(np.int64), 0, cells)
    ib = np.clip(((b - blo) / cell).astype(np.int64), 0, cells)
    occ = np.zeros((cells + 1, cells + 1), np.uint8)
    occ[ib, ia] = 1
    _, label = cv2.connectedComponents(cv2.dilate(occ, np.ones((3, 3), np.uint8)), connectivity=8)
    of = label[ib, ia]
    counts = np.bincount(of)
    big = [k for k in np.argsort(-counts) if counts[k] >= max(500, min_frac * len(rel))][:most]
    return [np.flatnonzero(of == k) for k in big] or [np.arange(len(rel))]


def locate_board(index: SplatIndex, board: Board, widths=(1600, 2400, 3200), refine: int = 5, max_planes: int = 8,
                 max_radius: int = 2):
    """Finds the board in the splat. Returns (BoardFit or None, debug renders as (name, gray image) pairs).

    Each dominant plane's slab is rendered straight down from both faces, planes largest first and the coarsest width
    first, and the search stops at the first render that shows every tag. That face is then rendered at `refine` widths
    across the range and the fit uses each corner's median over the renders that show every tag, since each width
    samples the tag edges differently. If no render shows every tag, the fit from the most tags (then the lowest rms) is
    returned. Discs are capped at max_radius pixels and fill_holes closes the gaps, which keeps the renders fast.
    """
    faces = []
    for p, (centre, normal, thr, _) in enumerate(dominant_planes(index.centers, max_planes)):
        slab = np.flatnonzero(np.abs((index.centers - centre) @ normal) < 3 * thr)
        u0 = np.cross(normal, [1.0, 0, 0] if abs(normal[0]) < 0.9 else [0, 1.0, 0])
        u0 /= np.linalg.norm(u0)
        for c, near in enumerate(_regions(index.centers[slab] - centre, u0, np.cross(normal, u0))):
            near = slab[near]
            rel = index.centers[near] - centre
            for side, n in (("a", normal), ("b", -normal)):
                u = u0 if side == "a" else -u0
                v = np.cross(-n, u)  # look along -n: u x v = -n
                (a0, a1), (b0, b1) = np.percentile(rel @ u, [0.5, 99.5]), np.percentile(rel @ v, [0.5, 99.5])
                name = f"plane{p}{side}" + (f"-{c + 1}" if c else "")
                faces.append((name, near, centre + a0 * u + b0 * v, u, v, a1 - a0, b1 - b0))
    renders = {}

    def look(face, width):
        """Tag id -> (4, 3) splat corners seen in one render of a face (plain or equalised, whichever sees more)."""
        name, near, origin, u, v, du, dv = face
        res = max(du, dv) / width
        size = (int(du / res) + 1, int(dv / res) + 1)
        color, depth = index.subset(near).render_ortho(origin, u, v, res, size, max_radius)
        gray = cv2.cvtColor(fill_holes(color, depth), cv2.COLOR_RGB2GRAY)
        renders[name] = (f"{name}-{width}px", gray)  # the finest render of each face, for the user to check
        seen = {}
        for equalize in (False, True):
            px, _, tags = detect(cv2.equalizeHist(gray) if equalize else gray, board)
            if len(tags) > len(seen):
                pts = origin + res * (px[:, :1] * u + px[:, 1:] * v)
                seen = {tag: pts[4 * k:4 * k + 4] for k, tag in enumerate(tags)}
            if len(seen) == len(board.ids):
                break
        return seen

    model = board.marker_corners()
    best = None
    for width in widths:
        for face in faces:
            seen = look(face, width)
            if not seen:
                continue
            tags = list(seen)
            fit = _fit(np.concatenate([model[t] for t in tags]), np.concatenate(list(seen.values())), tags,
                       f"{face[0]} {width}px")
            if best is None or (len(fit.tags), -fit.rms_mm) > (len(best.tags), -best.rms_mm):
                best = fit
            if len(seen) < len(board.ids):
                continue
            full = [seen] + [s for w in np.linspace(widths[0], widths[-1], refine).round().astype(int) if w != width
                             for s in [look(face, int(w))] if len(s) == len(board.ids)]
            pts = np.median([np.concatenate([s[t] for t in board.ids]) for s in full], axis=0)
            fit = _fit(np.concatenate([model[t] for t in board.ids]), pts, list(board.ids),
                       f"{face[0]} median of {len(full)} renders")
            return fit, list(renders.values())
    return best, list(renders.values())


def load_photo(path):
    """RGB pixels upright as displayed, and the EXIF 35 mm-equivalent focal length (None if missing)."""
    if str(path).lower().endswith((".heic", ".heif")):
        raise ValueError(f"{path}: HEIC is not supported; export JPEG (iPhone: Settings > Camera > Formats > "
                         "Most Compatible)")
    try:
        with Image.open(path) as im:
            f35 = im.getexif().get_ifd(EXIF_IFD).get(FOCAL_35MM)
            rgb = np.asarray(ImageOps.exif_transpose(im).convert("RGB"))
    except (OSError, SyntaxError, Image.DecompressionBombError) as e:  # PIL raises all three for broken files
        raise ValueError(f"not a readable image ({e})") from e
    try:
        f35 = float(f35) if f35 else None
    except (TypeError, ValueError):
        f35 = None
    return rgb, f35


def intrinsics(width: int, height: int, focal_35mm: float) -> np.ndarray:
    """Pinhole K from a 35 mm-equivalent focal length (it is defined on the diagonal), centred principal point."""
    f = focal_35mm * math.hypot(width, height) / FULL_FRAME_DIAGONAL_MM
    return np.array([[f, 0, (width - 1) / 2], [0, f, (height - 1) / 2], [0, 0, 1.0]])


def focal_35mm(f_px: float, width: int, height: int) -> float:
    """The 35 mm-equivalent of a focal length in pixels (inverse of intrinsics)."""
    return float(f_px * FULL_FRAME_DIAGONAL_MM / math.hypot(width, height))


def refine_focal(obj: np.ndarray, px: np.ndarray, K: np.ndarray, size, tags: list, min_tags: int = 2,
                 max_std: float = 0.01, max_change: float = 0.1, noise_px: float = 0.3):
    """One focal length (principal point fixed, no distortion) from a photo's board corners.

    Returns (K to use, the board's focal length in pixels or None, why). EXIF stores whole millimetres, so 27 for a
    26.4 mm-equivalent lens is 2 % off, which moves a camera 0.6 m from the board by about 1 cm. The board's value is
    used only if it is well determined (at least min_tags tags and enough perspective: 1 sigma under max_std, with
    corner noise taken as at least noise_px), within max_change of the given value, and more than 2 sigma from it.
    """
    if len(tags) < min_tags:
        return K, None, f"kept: refining it needs {min_tags}+ tags in the photo"
    flags = (cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_PRINCIPAL_POINT | cv2.CALIB_FIX_ASPECT_RATIO
             | cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_K1 | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3)
    try:
        _, Kb, _, rvecs, tvecs = cv2.calibrateCamera([obj.astype(np.float32)], [px.astype(np.float32)], tuple(size),
                                                     K.copy(), np.zeros(5), flags=flags)
    except cv2.error:
        return K, None, "kept: the refinement did not converge"
    proj, J = cv2.projectPoints(obj, rvecs[0], tvecs[0], Kb, None)
    resid = proj.reshape(-1) - px.reshape(-1)
    sigma = max(float(np.sqrt(resid @ resid / max(len(resid) - 7, 1))), noise_px)
    J = np.c_[J[:, :6], J[:, 6] + J[:, 7]]  # rvec, tvec, and one f for fx = fy
    f0, f = float(K[1, 1]), float(Kb[1, 1])
    try:
        std = sigma * math.sqrt(np.linalg.inv(J.T @ J)[6, 6])
    except np.linalg.LinAlgError:
        return K, None, "kept: the board view does not determine it"
    if not math.isfinite(std) or std > max_std * f:
        return K, f, f"kept: the board view determines it only to +-{100 * std / f:.1f} %"
    if abs(f / f0 - 1) > max_change:
        return K, f, f"kept: the board says {100 * (f / f0 - 1):+.1f} %, more than EXIF rounding explains"
    if abs(f - f0) <= 2 * std:
        return K, f, f"kept: the board agrees within 2 sigma (+-{100 * std / f:.2f} %)"
    out = K.copy()
    out[0, 0] = out[1, 1] = f
    return out, f, f"refined from the board, {100 * (f / f0 - 1):+.1f} % (+-{100 * std / f:.2f} %)"


def scaled(K: np.ndarray, factor: float) -> np.ndarray:
    """K for the same camera at factor times the resolution (pixel centres at integers)."""
    out = K.copy()
    out[:2, :2] *= factor
    out[:2, 2] = (K[:2, 2] + 0.5) * factor - 0.5
    return out


@dataclass
class View:
    """One photo's camera in splat coordinates (OpenCV: X_cam = R @ X + t)."""
    K: np.ndarray
    R: np.ndarray
    t: np.ndarray
    size: tuple
    rms_px: float
    tags: list
    corners_px: np.ndarray
    position: np.ndarray
    target: np.ndarray
    up: np.ndarray
    fov_deg: float
    focal_refined_px: float | None = None  # the board's estimate, used or not
    focal_note: str = ""

    def pose(self, digits: int = 4) -> dict:
        """The viewpoints.yaml pose: where the camera is, the surface point at the image centre, up, vertical FOV."""
        def r(a):
            return [round(float(x), digits) for x in a]
        return {"position": r(self.position), "target": r(self.target), "up": r(self.up),
                "fov_deg": round(self.fov_deg, 2)}

    @property
    def focal_35mm(self) -> float:
        return focal_35mm(self.K[1, 1], *self.size)

    def as_dict(self) -> dict:
        refined = None if self.focal_refined_px is None else focal_35mm(self.focal_refined_px, *self.size)
        return {"K": self.K.tolist(), "R": self.R.tolist(), "t": self.t.tolist(), "size": list(self.size),
                "rms_px": self.rms_px, "tags": self.tags, "focal_35mm_used": self.focal_35mm,
                "focal_35mm_refined": refined, "focal_note": self.focal_note, **self.pose(6)}


def _pnp(obj, px, K):
    _, rvecs, tvecs, errs = cv2.solvePnPGeneric(obj, px, K, None, flags=cv2.SOLVEPNP_IPPE)
    i = int(np.argmin(np.ravel(errs)))
    return cv2.solvePnPRefineLM(obj, px, K, None, rvecs[i], tvecs[i])


def register_photo(rgb: np.ndarray, K: np.ndarray, board: Board, fit: BoardFit, index: SplatIndex,
                   refine: bool = True) -> View | None:
    """The photo's camera in splat coordinates, or None if it does not see the board.

    With refine, the focal length comes from the board corners when they determine it better than K does (refine_focal).
    """
    px, obj, tags = detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), board)
    if not tags:
        return None
    H, W = rgb.shape[:2]
    f_board, note = None, "kept: refinement off"
    if refine:
        K, f_board, note = refine_focal(obj, px, K, (W, H), tags)
    rvec, tvec = _pnp(obj, px, K)
    proj = cv2.projectPoints(obj, rvec, tvec, K, None)[0].reshape(-1, 2)
    rms = float(np.sqrt(np.mean(np.sum((proj - px) ** 2, axis=1))))
    R_cb = cv2.Rodrigues(rvec)[0]
    R = R_cb @ fit.R.T  # X_cam (splat units) = R @ X_splat + t
    t = fit.scale * tvec.ravel() - R @ fit.t
    position, forward, up = -R.T @ t, R.T @ [0, 0, 1.0], R.T @ [0, -1.0, 0]
    distance = _centre_depth(index, K, R, t, (W, H)) or float(forward @ (fit.t - position))
    fov = math.degrees(2 * math.atan(H / 2 / K[1, 1]))
    return View(K, R, t, (W, H), rms, tags, px, position, position + distance * forward, up, fov, f_board, note)


def _centre_depth(index, K, R, t, size, width=480):
    """Median splat depth in the middle tenth of the image, or None if the splat shows nothing there."""
    f = min(1.0, width / size[0])
    w, h = round(size[0] * f), round(size[1] * f)
    _, depth = index.render(scaled(K, f), R, t, (w, h))
    mid = depth[int(h * 0.45):int(h * 0.55) + 1, int(w * 0.45):int(w * 0.55) + 1]
    mid = mid[np.isfinite(mid)]
    return float(np.median(mid)) if len(mid) else None


def overlay(rgb: np.ndarray, view: View, index: SplatIndex, width: int = 960) -> np.ndarray:
    """The photo blended half and half with the splat rendered from the solved camera, detected tag corners circled.

    If the registration is right, edges in the photo and the render line up.
    """
    f = min(1.0, width / view.size[0])
    w, h = round(view.size[0] * f), round(view.size[1] * f)
    color, depth = index.render(scaled(view.K, f), view.R, view.t, (w, h))
    render = fill_holes(color, depth)
    seen = cv2.dilate(np.isfinite(depth).astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool)
    photo = cv2.resize(rgb, (w, h), interpolation=cv2.INTER_AREA)
    out = photo.copy()
    out[seen] = (photo[seen].astype(np.uint16) + render[seen]) // 2
    for x, y in (view.corners_px + 0.5) * f - 0.5:
        cv2.circle(out, (round(x), round(y)), 4, (0, 255, 0), 1, cv2.LINE_AA)
    return out

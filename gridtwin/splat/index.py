"""Look at a splat from any camera without a GPU: colour and depth renders, and pixels lifted back to 3D points.

Cameras follow OpenCV: X_cam = R @ X + t, with x right, y down and z forward, and X in the splat file's own coordinates.
Each Gaussian is drawn as a flat disc one sigma wide and the nearest one wins each pixel. That is enough for depth and for
finding a printed board; it is not a substitute for the viewer.
"""
import cv2
import numpy as np

from gridtwin.splat.ply import Splat


class SplatIndex:
    def __init__(self, centers: np.ndarray, colors: np.ndarray, scale: np.ndarray):
        self.centers, self.colors, self.scale = centers, colors, scale

    @classmethod
    def from_splat(cls, splat: Splat, min_opacity: float = 0.3) -> "SplatIndex":
        """Drops faint Gaussians (floaters and fog) so depth comes from surfaces."""
        keep = splat.opacity >= min_opacity
        return cls(splat.centers[keep], splat.colors[keep], splat.scale[keep])

    def subset(self, mask: np.ndarray) -> "SplatIndex":
        return SplatIndex(self.centers[mask], self.colors[mask], self.scale[mask])

    def render(self, K, R, t, size, max_radius: int = 6):
        """Pinhole view at size (W, H): colour (H, W, 3) uint8 and z-depth (H, W) with inf where nothing is seen."""
        cam = self.centers @ np.asarray(R).T + np.asarray(t)
        ok = cam[:, 2] > 1e-9
        cam = cam[ok]
        z = cam[:, 2]
        u = K[0, 0] * cam[:, 0] / z + K[0, 2]
        v = K[1, 1] * cam[:, 1] / z + K[1, 2]
        return _raster(u, v, z, K[0, 0] * self.scale[ok] / z, self.colors[ok], size, max_radius)

    def render_ortho(self, origin, u_axis, v_axis, res: float, size, max_radius: int = 6):
        """Orthographic view looking along u x v: pixel (x, y) shows origin + res * (x * u_axis + y * v_axis)."""
        rel = self.centers - np.asarray(origin)
        z = rel @ np.cross(u_axis, v_axis)
        return _raster(rel @ u_axis / res, rel @ v_axis / res, z, self.scale / res, self.colors, size, max_radius)

    @staticmethod
    def lift(K, R, t, pixels, depth) -> np.ndarray:
        """(N, 2) pixels with their (N,) z-depths -> (N, 3) points in splat coordinates."""
        pixels, depth = np.asarray(pixels, float), np.asarray(depth, float)
        cam = np.stack([(pixels[:, 0] - K[0, 2]) / K[0, 0] * depth, (pixels[:, 1] - K[1, 2]) / K[1, 1] * depth, depth],
                       axis=1)
        return (cam - np.asarray(t)) @ np.asarray(R)


def _raster(u, v, z, radius, colors, size, max_radius):
    """Nearest disc per pixel without sorting every (Gaussian, offset) pair.

    Gaussians are sorted once by centre pixel, nearest first, so for each disc offset the first Gaussian of each centre
    pixel is the only candidate there and every target pixel is written at most once per offset: a z-buffer of depth
    ranks that needs no scatter-min.
    """
    W, H = size
    m = max_radius
    on = np.isfinite(u) & np.isfinite(v) & (u >= -m - 0.5) & (u < W + m - 0.5) & (v >= -m - 0.5) & (v < H + m - 0.5)
    ui, vi = np.rint(u[on]).astype(np.int64), np.rint(v[on]).astype(np.int64)
    z, r, colors = z[on], np.clip(np.nan_to_num(radius[on]), 0, m), colors[on]
    color = np.zeros((H * W, 3), np.uint8)
    depth = np.full(H * W, np.inf)
    n = len(z)
    if not n:
        return color.reshape(H, W, 3), depth.reshape(H, W)
    by_z = np.argsort(z, kind="stable")
    rank = np.empty(n, np.int64)
    rank[by_z] = np.arange(n)
    cell = (vi + m) * (W + 2 * m) + ui + m
    order = np.argsort(cell * n + rank)  # by centre pixel, nearest first
    ui, vi, r, rank, cell = ui[order], vi[order], r[order], rank[order], cell[order]
    best = np.full(H * W, n, np.int64)  # depth rank of the nearest disc per pixel; n = nothing drawn
    reach = int(np.ceil(r.max()))
    offsets = {}
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            offsets.setdefault(dx * dx + dy * dy, []).append((dx, dy))
    for d2, group in sorted(offsets.items()):
        sel = np.flatnonzero(r >= np.sqrt(d2)) if d2 else np.arange(n)
        if not len(sel):
            break  # farther offsets need even larger discs
        c = cell[sel]
        keep = np.ones(len(sel), bool)
        keep[1:] = c[1:] != c[:-1]
        sel = sel[keep]
        su, sv, sr = ui[sel], vi[sel], rank[sel]
        for dx, dy in group:
            x, y = su + dx, sv + dy
            inside = (x >= 0) & (x < W) & (y >= 0) & (y < H)
            pix = y[inside] * W + x[inside]
            best[pix] = np.minimum(best[pix], sr[inside])
    hit = best < n
    which = by_z[best[hit]]
    depth[hit] = z[which]
    color[hit] = colors[which]
    return color.reshape(H, W, 3), depth.reshape(H, W)


def fill_holes(color: np.ndarray, depth: np.ndarray, radius: int = 3) -> np.ndarray:
    """Fills gaps between Gaussians with the mean of their drawn neighbours; open background stays black."""
    drawn = np.isfinite(depth)
    weight = drawn.astype(np.float32)
    k = (2 * radius + 1, 2 * radius + 1)
    num = cv2.boxFilter(color.astype(np.float32) * weight[..., None], -1, k, normalize=False)
    den = cv2.boxFilter(weight, -1, k, normalize=False)
    fill = np.rint(num / np.maximum(den, 1)[..., None]).astype(np.uint8)  # 0 where no neighbour is drawn
    return np.where(drawn[..., None], color, fill)

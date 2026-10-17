"""Thin a big Gaussian splat so the twin viewer loads it fast and renders it smoothly.

Keeps the N Gaussians that cover the most of the picture (opacity times the area of their two largest axes), drops far
floaters, and for a 3DGS PLY drops the view-dependent colour terms (f_rest_*), which are most of the file.
Writes the same format it reads: .splat (32-byte rows) or 3DGS binary .ply.

With --focus points (where the camera will stand), "the picture" is the view from the nearest of them: each Gaussian's
area is divided by its squared distance to that point, so the small splats that carry the detail up close are kept
and only the ones too small to see from there are dropped (a level of detail baked for those spots).

    python -m scripts.thin_splat ~/Downloads/scan.ply assets/splats/plant.ply --keep 1500000
    python -m scripts.thin_splat full.splat demo.splat --keep 1600000 --focus 0.05,-0.91,-3.35 --focus 0,-1,1.2
"""
import argparse
from pathlib import Path

import numpy as np

from gridtwin.splat.ply import _TYPES

SPLAT_ROW = np.dtype([("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])


def read_ply(path: Path):
    data = path.read_bytes()
    end = data.find(b"end_header\n")
    if not data.startswith(b"ply") or end < 0:
        raise ValueError(f"{path}: not a PLY file")
    props, count, fmt = [], None, None
    for line in data[:end].decode("ascii", "replace").splitlines():
        p = line.split()
        if p[:1] == ["format"]:
            fmt = p[1]
        elif p[:2] == ["element", "vertex"]:
            count = int(p[2])
        elif p[:1] == ["element"] and count is not None:
            break  # only the vertex element is kept
        elif p[:1] == ["property"] and count is not None:
            props.append((p[2], _TYPES[p[1]]))
    if fmt != "binary_little_endian" or count is None:
        raise ValueError(f"{path}: needs a binary little-endian 3DGS PLY with a vertex element")
    return np.frombuffer(data, np.dtype(props), count, end + len(b"end_header\n"))


def write_ply(path: Path, v: np.ndarray):
    head = ["ply", "format binary_little_endian 1.0", f"element vertex {len(v)}"]
    inv = {"<f4": "float", "|u1": "uchar", "<i4": "int", "<u4": "uint", "<f8": "double", "<i2": "short", "<u2": "ushort",
           "|i1": "char"}
    head += [f"property {inv[v.dtype[n].str]} {n}" for n in v.dtype.names] + ["end_header", ""]
    with path.open("wb") as f:
        f.write("\n".join(head).encode("ascii"))
        f.write(np.ascontiguousarray(v).tobytes())


def importance_and_centres(v: np.ndarray, kind: str):
    if kind == "splat":
        s = np.sort(v["scale"].astype(np.float64), axis=1)
        return v["rgba"][:, 3] / 255.0 * s[:, 1] * s[:, 2], v["pos"].astype(np.float64)
    s = np.sort(np.stack([v[f"scale_{i}"] for i in range(3)], 1).astype(np.float64), axis=1)  # log sigma
    alpha = 1 / (1 + np.exp(-v["opacity"].astype(np.float64)))
    return alpha * np.exp(s[:, 1] + s[:, 2]), np.stack([v["x"], v["y"], v["z"]], 1).astype(np.float64)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--keep", type=int, default=1_500_000, help="Gaussians to keep (default 1.5M)")
    ap.add_argument("--focus", action="append", default=[], metavar="X,Y,Z",
                    help="a camera spot in file coordinates; repeat for more (keeps detail near them)")
    ap.add_argument("--focus-min", type=float, default=0.3, help="distances under this count as this (file units)")
    a = ap.parse_args()

    kind = a.src.suffix.lower().lstrip(".")
    if kind not in ("splat", "ply") or a.dst.suffix.lower() != a.src.suffix.lower():
        raise SystemExit("src and dst must both be .splat or both be .ply")
    v = np.frombuffer(a.src.read_bytes(), SPLAT_ROW) if kind == "splat" else read_ply(a.src)
    imp, c = importance_and_centres(v, kind)
    if a.focus:
        spots = np.array([[float(x) for x in f.split(",")] for f in a.focus])
        d2 = np.full(len(c), np.inf)
        for p in spots:
            d2 = np.minimum(d2, ((c - p) ** 2).sum(1))
        imp = imp / np.maximum(d2, a.focus_min ** 2)

    lo, hi = np.percentile(c, 0.5, axis=0), np.percentile(c, 99.5, axis=0)
    pad = 0.1 * (hi - lo)
    inside = np.all((c >= lo - pad) & (c <= hi + pad), axis=1) & np.isfinite(imp)
    idx = np.flatnonzero(inside)
    if len(idx) > a.keep:
        idx = idx[np.argpartition(-imp[idx], a.keep)[: a.keep]]
    idx = np.sort(idx)
    out = v[idx]
    if kind == "ply":
        keep = [n for n in out.dtype.names if not n.startswith("f_rest_") and n not in ("nx", "ny", "nz")]
        packed = np.empty(len(out), np.dtype([(n, out.dtype[n]) for n in keep]))
        for n in keep:
            packed[n] = out[n]
        write_ply(a.dst, packed)
        out = packed
    else:
        a.dst.write_bytes(out.tobytes())

    cc = c[idx]
    print(f"{a.src.name}: {len(v):,} Gaussians -> kept {len(out):,} ({a.dst.stat().st_size / 1e6:.1f} MB) in {a.dst}")
    for i, ax in enumerate("xyz"):
        p = np.percentile(cc[:, i], [1, 5, 50, 95, 99])
        print(f"  {ax}: p1 {p[0]:.2f}  p5 {p[1]:.2f}  median {p[2]:.2f}  p95 {p[3]:.2f}  p99 {p[4]:.2f}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Thin, crop and trim a 3D Gaussian splat PLY so a multi-million splat scene loads smoothly in the twin page.

    python3 scripts/prepare_splat.py assets/splats/engineroom-source.ply --out assets/splats/engineroom.ply \\
        --crop-auto --max-splats 2000000 --drop-sh --drop-normals \\
        --title "Engine room of Croatian cruise boat" --author tosolini --url https://superspl.at/scene/ae149bd3

Order of work: crop, then keep the --max-splats most visible, then drop properties, then recentre. Every vertex
property that is not dropped (f_dc_*, f_rest_* SH bands, opacity, scale_*, rot_*, anything else) is copied as is, and
other PLY elements and header comments survive, so a run with no options writes a byte-identical copy.
Reads uncompressed binary PLY only (what SuperSplat calls "PLY"). Spark loads compressed PLY and .sog directly but they
cannot be thinned here. Only numpy is needed. Coordinates stay in the file's own units unless --recentre is given.
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PLY_TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "i2", "int16": "i2", "ushort": "u2",
             "uint16": "u2", "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4", "float": "f4", "float32": "f4",
             "double": "f8", "float64": "f8"}
FORMATS = {"binary_little_endian": "<", "binary_big_endian": ">"}
LICENSE = "CC BY 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
FAR_FROM_ORIGIN = 64.0  # Spark keeps splat centres as 16-bit floats, so precision falls with distance from the origin


class PrepareError(Exception):
    """A problem with the input or the options, reported without a traceback."""


# ---------- PLY reader and writer ----------

@dataclass
class Element:
    name: str
    count: int
    props: list  # [(property name, PLY type word)] in file order; the words are kept so a copy reads the same
    data: np.ndarray = None


class Ply:
    """The header items (format, comments, elements) in file order, and each element's rows as a structured array."""

    def __init__(self, header):
        self.header = header

    @property
    def elements(self):
        return [h for h in self.header if isinstance(h, Element)]

    @property
    def vertex(self):
        return self._vertex_element().data

    def _vertex_element(self):
        for e in self.elements:
            if e.name == "vertex":
                return e
        raise PrepareError("the PLY has no vertex element")

    def set_vertex(self, rows):
        e = self._vertex_element()
        types = dict(e.props)
        e.data, e.count, e.props = rows, len(rows), [(n, types[n]) for n in rows.dtype.names]


def read_ply(path):
    path = Path(path)
    try:
        fh = open(path, "rb")
    except OSError as err:
        raise PrepareError(f"{path}: cannot open ({err.strerror})") from err
    with fh:
        if fh.readline().strip() != b"ply":
            raise PrepareError(f"{path}: not a PLY file")
        header, order = [], None
        for _ in range(100_000):
            raw = fh.readline()
            if not raw:
                raise PrepareError(f"{path}: the PLY header never ends")
            line = raw.decode("ascii", "replace").rstrip("\r\n")
            parts = line.split()
            word = parts[0] if parts else ""
            if word == "end_header":
                break
            if word == "format":
                if len(parts) < 2 or parts[1] not in FORMATS:
                    raise PrepareError(f"{path}: {parts[1] if len(parts) > 1 else 'unknown'} PLY is not supported; "
                                       "export a binary PLY")
                order = FORMATS[parts[1]]
                header.append(line)
            elif word in ("comment", "obj_info"):
                header.append(line)
            elif word == "element" and len(parts) == 3:
                header.append(Element(parts[1], int(parts[2]), []))
            elif word == "property":
                if not header or not isinstance(header[-1], Element):
                    raise PrepareError(f"{path}: property line before any element")
                if parts[1] == "list":
                    raise PrepareError(f"{path}: has list properties, so it is a mesh, not a splat")
                if parts[1] not in PLY_TYPES or len(parts) != 3:
                    raise PrepareError(f"{path}: unsupported property line '{line}'")
                header[-1].props.append((parts[2], parts[1]))
            else:
                raise PrepareError(f"{path}: unsupported PLY header line '{line}'")
        else:
            raise PrepareError(f"{path}: the PLY header is too long")
        if order is None:
            raise PrepareError(f"{path}: the PLY has no format line")
        offset = fh.tell()
    ply = Ply(header)
    names = {e.name for e in ply.elements}
    vertex_props = {n for e in ply.elements if e.name == "vertex" for n, _ in e.props}
    if "chunk" in names or any(n.startswith("packed_") for n in vertex_props):
        raise PrepareError(f"{path}: this is a compressed PLY (PlayCanvas / SuperSplat). Spark loads it directly, but it "
                           "cannot be thinned here; download the uncompressed PLY from SuperSplat instead")
    if "vertex" not in names or not {"x", "y", "z"} <= vertex_props:
        raise PrepareError(f"{path}: no x, y, z per Gaussian, so this is not a splat PLY")
    size = path.stat().st_size
    for e in ply.elements:
        dtype = np.dtype([(n, order + PLY_TYPES[t]) for n, t in e.props])
        if offset + dtype.itemsize * e.count > size:
            raise PrepareError(f"{path}: truncated, the {e.name} element needs {dtype.itemsize * e.count:,} bytes "
                               f"but only {size - offset:,} are left")
        e.data = np.fromfile(path, dtype, e.count, offset=offset) if e.count else np.empty(0, dtype)
        offset += dtype.itemsize * e.count
    return ply


def write_ply(path, ply):
    with open(path, "wb") as fh:
        fh.write(b"ply\n")
        for item in ply.header:
            if isinstance(item, Element):
                fh.write(f"element {item.name} {item.count}\n".encode("ascii"))
                for name, ptype in item.props:
                    fh.write(f"property {ptype} {name}\n".encode("ascii"))
            else:
                fh.write(item.encode("ascii", "replace") + b"\n")
        fh.write(b"end_header\n")
        for e in ply.elements:
            e.data.tofile(fh)


# ---------- operations on the vertex array ----------

def log_visibility(v):
    """log(sigmoid(opacity) * exp(scale_0 + scale_1 + scale_2)): opacity times the volume from the three log scales.
    The log keeps the ranking identical and cannot overflow on huge scales."""
    missing = [n for n in ("opacity", "scale_0", "scale_1", "scale_2") if n not in v.dtype.names]
    if missing:
        raise PrepareError(f"cannot rank splats by visibility, the PLY has no {', '.join(missing)}")
    log_volume = v["scale_0"].astype(np.float64) + v["scale_1"] + v["scale_2"]
    with np.errstate(invalid="ignore"):  # NaN opacities stay NaN and rank last
        return -np.logaddexp(0.0, -v["opacity"].astype(np.float64)) + log_volume


def visibility(v):
    return np.exp(log_visibility(v))


def select_visible(v, max_splats):
    """Indices, ascending, of the max_splats most visible splats; ties go to the earlier splat, NaN scores go last."""
    if max_splats < 1:
        raise PrepareError("--max-splats must be at least 1")
    n = len(v)
    if max_splats >= n:
        return np.arange(n)
    score = log_visibility(v)
    score[np.isnan(score)] = -np.inf
    return np.sort(np.argsort(-score, kind="stable")[:max_splats])


def crop_mask(v, box=None, sphere=None, auto=None, margin=0.1):
    """Boolean keep-mask in the file's coordinates. box = (x0, y0, z0, x1, y1, z1), inclusive; sphere = (cx, cy, cz, r);
    auto = (low, high) percentiles of the centres per axis, widened by margin times their span so walls survive.
    Several crops intersect."""
    mask = np.ones(len(v), bool)
    if box is not None:
        lo, hi = box[:3], box[3:]
        if any(a > b for a, b in zip(lo, hi)):
            raise PrepareError("--crop-box needs X0 Y0 Z0 X1 Y1 Z1 with each minimum below its maximum")
        for axis, a, b in zip("xyz", lo, hi):
            mask &= (v[axis] >= a) & (v[axis] <= b)
    if sphere is not None:
        cx, cy, cz, r = sphere
        if r < 0:
            raise PrepareError("--crop-sphere needs a radius of 0 or more")
        d2 = sum((v[axis].astype(np.float64) - c) ** 2 for axis, c in zip("xyz", (cx, cy, cz)))
        mask &= d2 <= r * r
    if auto is not None:
        low, high = auto
        if not 0 <= low < high <= 100:
            raise PrepareError("--crop-auto needs LOW and HIGH percentiles with 0 <= LOW < HIGH <= 100")
        for axis in "xyz":
            c = v[axis].astype(np.float64)
            finite = c[np.isfinite(c)]
            if not len(finite):
                raise PrepareError(f"no finite {axis} coordinates to crop by")
            a, b = np.percentile(finite, [low, high])
            pad = margin * (b - a)
            mask &= (c >= a - pad) & (c <= b + pad)
    return mask


def is_sh_rest(name):
    return name.startswith("f_rest_")


def is_normal(name):
    return name in ("nx", "ny", "nz")


def drop_properties(v, predicate):
    keep = [n for n in v.dtype.names if not predicate(n)]
    if len(keep) == len(v.dtype.names):
        return v
    out = np.empty(len(v), [(n, v.dtype[n]) for n in keep])
    for n in keep:
        out[n] = v[n]
    return out


def recentre(v):
    """Shift the centres so their median is the origin. Returns (new array, the offset that was subtracted)."""
    out = v.copy()
    offset = []
    for axis in "xyz":
        c = v[axis].astype(np.float64)
        offset.append(float(np.median(c[np.isfinite(c)])))
        out[axis] = c - offset[-1]
    return out, offset


def extent(v):
    """Bounding box of the centres, plus the 1st to 99th percentile box so a few floaters do not hide the scene."""
    lo, hi, rlo, rhi = [], [], [], []
    for axis in "xyz":
        c = v[axis].astype(np.float64)
        c = c[np.isfinite(c)]
        lo.append(float(c.min()))
        hi.append(float(c.max()))
        a, b = np.percentile(c[:: max(1, len(c) // 500_000)], [1, 99])
        rlo.append(float(a))
        rhi.append(float(b))
    size = [b - a for a, b in zip(lo, hi)]
    return {"min": lo, "max": hi, "size": size, "centre": [(a + b) / 2 for a, b in zip(lo, hi)],
            "diagonal": float(np.linalg.norm(size)), "robust_min": rlo, "robust_max": rhi}


def write_credits(scene_path, title, author, url, changes, license=LICENSE, license_url=LICENSE_URL):
    """<scene>.credits.json next to the scene, for the twin page's settings panel."""
    scene_path = Path(scene_path)
    path = scene_path.with_name(scene_path.stem + ".credits.json")
    credits = {"title": title, "author": author, "url": url, "license": license, "license_url": license_url,
               "changes": changes}
    path.write_text(json.dumps(credits, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# ---------- command line ----------

def size_text(n):
    return f"{n:,} bytes ({n / 1e6:.1f} MB)"


def extent_text(e):
    axes = "  ".join(f"{a} {lo:.2f}..{hi:.2f} ({s:.2f})" for a, lo, hi, s in zip("xyz", e["min"], e["max"], e["size"]))
    centre = ", ".join(f"{c:.2f}" for c in e["centre"])
    return f"{axes}; centre ({centre}); diagonal {e['diagonal']:.2f}"


def parse_args(argv):
    p = argparse.ArgumentParser(prog="prepare_splat.py", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", type=Path, help="uncompressed binary 3DGS PLY")
    p.add_argument("--out", type=Path, help="output PLY (default: <input>-prepared.ply next to the input)")
    p.add_argument("--max-splats", type=int, metavar="N",
                   help="keep the N most visible splats (opacity times volume), in their original order")
    p.add_argument("--crop-box", type=float, nargs=6, metavar=("X0", "Y0", "Z0", "X1", "Y1", "Z1"),
                   help="keep splats inside this box, in file coordinates")
    p.add_argument("--crop-sphere", type=float, nargs=4, metavar=("X", "Y", "Z", "R"),
                   help="keep splats inside this sphere, in file coordinates")
    p.add_argument("--crop-auto", type=float, nargs="*", metavar="PCT",
                   help="drop far floaters outside LOW HIGH percentiles of the centres per axis (default 1 99)")
    p.add_argument("--crop-margin", type=float, default=0.1,
                   help="widen the --crop-auto box by this fraction of its span so walls survive (default 0.1)")
    p.add_argument("--drop-sh", action="store_true", help="remove the f_rest_* SH bands (about 4x smaller)")
    p.add_argument("--drop-normals", action="store_true", help="remove nx, ny, nz (3DGS files store zeros there)")
    p.add_argument("--recentre", action="store_true",
                   help="shift the scene so the median centre is the origin (better 16-bit precision in Spark; "
                        "changes the file coordinates, so do it before registering the scene)")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--url", help="source page of the scene")
    p.add_argument("--license", default=LICENSE, help=f"default {LICENSE}")
    p.add_argument("--license-url", default=LICENSE_URL)
    return p.parse_args(argv)


def run(args):
    src = args.input
    out = args.out or src.with_name(src.stem + "-prepared.ply")
    if out.resolve() == src.resolve():
        raise PrepareError("refusing to overwrite the input; choose a different --out")
    given = {"--title": args.title, "--author": args.author, "--url": args.url}
    if any(given.values()) and not all(given.values()):
        missing = ", ".join(k for k, val in given.items() if not val)
        raise PrepareError(f"credits need --title, --author and --url together; missing {missing}")
    auto = None
    if args.crop_auto is not None:
        if len(args.crop_auto) not in (0, 2):
            raise PrepareError("--crop-auto takes either nothing or LOW HIGH")
        auto = tuple(args.crop_auto) or (1.0, 99.0)

    ply = read_ply(src)
    v = ply.vertex
    before_count, before_size = len(v), src.stat().st_size
    print(f"before: {before_count:,} splats, {size_text(before_size)}, {len(v.dtype.names)} properties "
          f"({sum(map(is_sh_rest, v.dtype.names))} SH bands)")
    print(f"  extent: {extent_text(extent(v))}")

    changes = []
    if args.crop_box or args.crop_sphere or auto:
        mask = crop_mask(v, box=args.crop_box, sphere=args.crop_sphere, auto=auto, margin=args.crop_margin)
        v = v[mask]
        changes.append(f"cropped from {before_count:,} to {len(v):,} splats")
        print(f"crop: kept {len(v):,} of {before_count:,} splats")
    if args.max_splats is not None:
        n = len(v)
        v = v[select_visible(v, args.max_splats)]
        if len(v) < n:
            changes.append(f"thinned from {n:,} to {len(v):,} splats")
        print(f"thin: kept the {len(v):,} most visible of {n:,} splats")
    if args.drop_sh and any(map(is_sh_rest, v.dtype.names)):
        v = drop_properties(v, is_sh_rest)
        changes.append("SH removed")
    if args.drop_normals and any(map(is_normal, v.dtype.names)):
        v = drop_properties(v, is_normal)
        changes.append("normals removed")
    if args.recentre:
        v, offset = recentre(v)
        changes.append("recentred")
        print("recentre: subtracted (" + ", ".join(f"{o:.3f}" for o in offset) + ") from every centre")
    if not len(v):
        raise PrepareError("nothing is left after cropping")

    ply.set_vertex(v)
    tmp = out.with_name(out.name + ".tmp")
    try:
        write_ply(tmp, ply)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    after_size = out.stat().st_size
    e = extent(v)
    print(f"after:  {len(v):,} splats, {size_text(after_size)}, {len(v.dtype.names)} properties "
          f"({before_size / after_size:.1f}x smaller)")
    print(f"  extent: {extent_text(e)}")
    reach = max(abs(x) for x in e["robust_min"] + e["robust_max"])
    if reach > FAR_FROM_ORIGIN:
        print(f"warning: the scene reaches {reach:,.0f} units from the origin. Spark stores centres as 16-bit floats, "
              f"good to about {reach / 1024:.2g} units there, so detail will shimmer. Add --recentre.")
    print(f"wrote {out}")
    if all(given.values()):
        path = write_credits(out, args.title, args.author, args.url, ", ".join(changes) or "none",
                             args.license, args.license_url)
        print(f"wrote {path}")
    else:
        print("no credits written (pass --title, --author and --url to write <scene>.credits.json)")


def main(argv=None):
    try:
        run(parse_args(argv))
    except PrepareError as err:
        print(f"prepare_splat: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

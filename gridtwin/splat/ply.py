"""Load a 3D Gaussian splat from a binary PLY (the layout Scaniverse, Polycam, Luma and 3DGS training export).

Coordinates stay exactly as stored in the file, so anything placed with them lines up with the same file in the viewer.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SH_C0 = 0.28209479177387814  # degree-0 spherical harmonic: rgb = 0.5 + SH_C0 * f_dc
_TYPES = {"char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1", "short": "<i2", "int16": "<i2", "ushort": "<u2",
          "uint16": "<u2", "int": "<i4", "int32": "<i4", "uint": "<u4", "uint32": "<u4", "float": "<f4",
          "float32": "<f4", "double": "<f8", "float64": "<f8"}


@dataclass
class Splat:
    centers: np.ndarray  # (N, 3) float64, the file's own coordinates
    colors: np.ndarray   # (N, 3) uint8 RGB from the view-independent colour
    opacity: np.ndarray  # (N,) float32 in [0, 1]
    scale: np.ndarray    # (N,) float32, one sigma along each Gaussian's longest axis, file units


def load_ply(path) -> Splat:
    data = Path(path).read_bytes()
    marker = b"end_header\n"
    end = data.find(marker)
    if not data.startswith(b"ply") or end < 0:
        raise ValueError(f"{path}: not a PLY file")
    elements, fmt = [], None
    for line in data[:end].decode("ascii", "replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[:2] == ["property", "list"]:
            raise ValueError(f"{path}: has list properties, so it is a mesh, not a splat")
        try:
            if parts[0] == "format":
                fmt = parts[1]
            elif parts[0] == "element":
                elements.append((parts[1], int(parts[2]), []))
            elif parts[0] == "property":
                elements[-1][2].append((parts[2], _TYPES[parts[1]]))
        except (IndexError, KeyError, ValueError) as e:
            raise ValueError(f"{path}: unreadable PLY header line {line.strip()!r}") from e
    if fmt != "binary_little_endian":
        raise ValueError(f"{path}: {fmt} PLY is not supported; export a binary PLY")
    offset, body = 0, data[end + len(marker):]
    for name, count, props in elements:
        dtype = np.dtype(props)
        if name == "vertex":
            if count < 0 or len(body) < offset + dtype.itemsize * count:
                raise ValueError(f"{path}: truncated: the header promises {count} Gaussians "
                                 f"({offset + dtype.itemsize * count} bytes) but only {len(body)} bytes follow it; "
                                 "copy or export the file again")
            v = np.frombuffer(body, dtype, count, offset)
            break
        offset += dtype.itemsize * count
    else:
        raise ValueError(f"{path}: no vertex element")
    names = set(v.dtype.names)
    if not {"x", "y", "z"} <= names:
        raise ValueError(f"{path}: no x/y/z per Gaussian (a compressed PLY?); export the uncompressed PLY")

    centers = np.stack([v["x"], v["y"], v["z"]], axis=1).astype(np.float64)
    if {"f_dc_0", "f_dc_1", "f_dc_2"} <= names:
        rgb = 0.5 + SH_C0 * np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=1)
        colors = np.clip(np.rint(rgb * 255), 0, 255).astype(np.uint8)
    elif {"red", "green", "blue"} <= names:
        colors = np.stack([v["red"], v["green"], v["blue"]], axis=1).astype(np.uint8)
    else:
        colors = np.full((count, 3), 128, np.uint8)
    opacity = 1 / (1 + np.exp(-v["opacity"].astype(np.float32))) if "opacity" in names else np.ones(count, np.float32)
    if {"scale_0", "scale_1", "scale_2"} <= names:  # stored as log sigma
        scale = np.exp(np.max(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], axis=1), axis=1)).astype(np.float32)
    else:
        scale = np.zeros(count, np.float32)
    return Splat(centers, colors, opacity.astype(np.float32), scale)

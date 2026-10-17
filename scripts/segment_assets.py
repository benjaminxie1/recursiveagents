"""Label every Gaussian of the plant splat with the asset it belongs to, from the station photos registered into it.

python -m scripts.segment_assets --splat assets/splats/scene.ply [--masks depth|sam] [--views id,id] [--out ...]

Reads assets/splats/<stem>.registration.json (written by scripts/register_viewpoints.py) and the viewpoint -> asset map
(gridtwin/sim/viewpoints_extra.yaml, then assets/photos/viewpoints.yaml, merged as /api/viewpoints merges them).
Writes assets/splats/<stem>.assets.json, whose contract is in gridtwin/splat/segment.py, and an overlay JPEG per view
in runs/segment/<time>/ to check by eye: on the left the mask over the photo (over the splat render when there is no
photo), on the right the splat seen from that camera with every asset's Gaussians tinted.

--masks depth (the default) needs no model. --masks sam runs SAM 2.1 (gridtwin/splat/segment_sam.py) and, if it is not
installed, stops with the exact downloads to approve.
Exit 0 when every view gave a mask and every asset got Gaussians, 1 when some did not (the rest is still written), 2
when nothing could run: no splat or registration, a registration made for another PLY, an unknown view, or no SAM.
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import yaml
from PIL import Image

from gridtwin.splat.index import SplatIndex, fill_holes
from gridtwin.splat.ply import load_ply
from gridtwin.splat.register import load_photo
from gridtwin.splat.segment import Camera, DepthMasks, Plane, SamMasks, assets_doc, segment

REPO = Path(__file__).resolve().parents[1]
VIEWPOINTS = [REPO / "gridtwin" / "sim" / "viewpoints_extra.yaml", REPO / "assets" / "photos" / "viewpoints.yaml"]
PALETTE = [(255, 140, 0), (0, 200, 255), (255, 64, 160), (120, 220, 60), (255, 215, 0), (160, 110, 255)]


def rel(path: Path) -> str:
    path = Path(path).resolve()
    return str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)


def asset_map(paths) -> dict:
    """Viewpoint id -> asset id over the viewpoint files, later files winning on a clash."""
    out = {}
    for path in paths:
        for vid, vp in ((yaml.safe_load(Path(path).read_text()) or {}).get("viewpoints") or {}).items():
            if (vp or {}).get("asset"):
                out[vid] = vp["asset"]
    return out


def station_photo(cam: Camera):
    """The full-size RGB station photo, or None if it is missing or not the registered aspect."""
    if cam.photo is None or not Path(cam.photo).exists():
        return None
    rgb = load_photo(cam.photo)[0]
    return rgb if abs(rgb.shape[1] / rgb.shape[0] - cam.size[0] / cam.size[1]) < 0.01 else None


def caption(img: np.ndarray, text: str) -> np.ndarray:
    bar = np.full((28, img.shape[1], 3), 24, np.uint8)
    cv2.putText(bar, text, (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1, cv2.LINE_AA)
    return np.concatenate([bar, img])


def overlay(seg, vid: str, tinted: SplatIndex, photo) -> np.ndarray:
    """Mask over the photo (or the render) beside the splat with every asset's Gaussians tinted, both from vid."""
    cam, render, mask = seg.cameras[vid], seg.renders[vid], seg.masks[vid]
    W, H = cam.size
    color = PALETTE[seg.assets.index(cam.asset) % len(PALETTE)]
    left = cv2.resize(photo, (W, H), interpolation=cv2.INTER_AREA) if photo is not None else render.color.copy()
    m = mask.pixels
    left[m] = (0.45 * left[m] + 0.55 * np.array(color)).astype(np.uint8)
    contours = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]
    cv2.drawContours(left, contours, -1, color, 2, cv2.LINE_AA)
    cv2.drawMarker(left, mask.seed, (255, 255, 255), cv2.MARKER_CROSS, 18, 3, cv2.LINE_AA)
    cv2.drawMarker(left, mask.seed, (0, 0, 0), cv2.MARKER_CROSS, 18, 1, cv2.LINE_AA)
    right = fill_holes(*tinted.render(cam.K, cam.R, cam.t, cam.size))
    info = seg.views[vid]
    left = caption(left, f"{vid} -> {cam.asset}: mask {info['mask_coverage']:.1%} over the "
                         f"{'photo' if photo is not None else 'splat render (no photo)'}, seed {mask.seed}")
    right = caption(right, f"labelled Gaussians from this camera ({', '.join(seg.assets)})")
    return np.concatenate([left, np.full((H + 28, 4, 3), 255, np.uint8), right], axis=1)


def tinted_index(splat, labels: np.ndarray, n_assets: int, min_opacity: float) -> SplatIndex:
    colors = splat.colors.astype(np.float32)
    for a in range(n_assets):
        colors[labels == a] = 0.35 * colors[labels == a] + 0.65 * np.array(PALETTE[a % len(PALETTE)])
    keep = splat.opacity >= min_opacity
    return SplatIndex(splat.centers[keep], colors[keep].astype(np.uint8), splat.scale[keep])


def run(args) -> int:
    started = time.perf_counter()
    splat_path = Path(args.splat)
    reg_path = Path(args.registration) if args.registration else splat_path.with_name(
        f"{splat_path.stem}.registration.json")
    if not splat_path.exists():
        print(f"no splat at {args.splat}")
        return 2
    if not reg_path.exists():
        print(f"no registration at {rel(reg_path)}; run python -m scripts.register_viewpoints register "
              f"--splat {args.splat} first")
        return 2
    registration = json.loads(reg_path.read_text())
    sha = hashlib.sha256(splat_path.read_bytes()).hexdigest()
    if registration.get("splat_sha256") != sha:
        print(f"{rel(reg_path)} was made for another file (sha256 {str(registration.get('splat_sha256'))[:12]}..., "
              f"{rel(splat_path)} is {sha[:12]}...); register the photos against this splat again")
        return 2
    assets = asset_map(args.viewpoints or VIEWPOINTS)
    registered = registration.get("views") or {}
    wanted = [v for v in args.views.split(",") if v] if args.views else sorted(registered)
    unknown = [v for v in wanted if v not in registered]
    if unknown:
        print(f"not registered: {', '.join(unknown)}. Registered views: {', '.join(sorted(registered)) or 'none'}")
        return 2
    orphans = [v for v in wanted if v not in assets]
    if orphans and args.views:
        print(f"no asset for {', '.join(orphans)} in {', '.join(rel(p) for p in args.viewpoints or VIEWPOINTS)}")
        return 2
    for vid in orphans:
        print(f"{vid:16s} SKIPPED: no asset for this viewpoint id")
    cams = [Camera.from_view(vid, assets[vid], registered[vid], REPO) for vid in wanted if vid in assets]
    if not cams:
        print("no registered view maps to an asset; nothing to segment")
        return 2

    depth = DepthMasks(args.depth_tol, args.plane_tol, args.color_tol)
    predictor = None
    if args.masks == "sam":
        from gridtwin.splat.segment_sam import ModelUnavailable, load_predictor
        try:
            predictor = load_predictor(args.sam_model, args.sam_python)
        except ModelUnavailable as e:
            print(e)
            return 2
        masker = SamMasks(predictor, args.sam_model, depth)
    else:
        masker = depth
    try:
        splat = load_ply(splat_path)
        loaded = time.perf_counter()
        seg = segment(splat, cams, masker, Plane.from_registration(registration, cams), args.width, args.min_opacity,
                      args.vis_tol, args.floor_tol, None if args.no_clean else args.clean_voxel, not args.no_fill)
    finally:
        if predictor is not None:
            predictor.close()
    done = time.perf_counter()
    print(f"{len(splat.centers)} Gaussians, {len(cams)} views, masks: {masker.name}; load {loaded - started:.1f} s, "
          f"segment {done - loaded:.1f} s")

    stamp = datetime.now().astimezone()
    out_dir = Path(args.overlays) if args.overlays else REPO / "runs" / "segment" / stamp.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    tinted = tinted_index(splat, seg.labels, len(seg.assets), args.min_opacity)
    for vid, info in seg.views.items():
        Image.fromarray(overlay(seg, vid, tinted, station_photo(seg.cameras[vid]))).save(out_dir / f"{vid}.jpg",
                                                                                          quality=90)
        print(f"{vid:16s} {info['asset']:8s} mask {info['mask_coverage']:6.1%} of the view, seed "
              f"{seg.masks[vid].seed}, {info['voted']} Gaussians seen, {info['in_mask']} in the mask")
    for vid, why in seg.failed:
        print(f"{vid:16s} FAILED: {why}")

    doc = assets_doc(seg, splat.centers, rel(splat_path), sha, masker.name, stamp.isoformat(timespec="seconds"))
    empty = []
    for asset, entry in doc["assets"].items():
        views = [vid for vid, info in seg.views.items() if info["asset"] == asset]
        coverage = np.mean([seg.views[v]["mask_coverage"] for v in views])
        if not entry["count"]:
            empty.append(asset)
            print(f"{asset:8s} no Gaussians from {len(views)} views")
            continue
        size = " x ".join(f"{s:.3f}" for s in np.subtract(*entry["bbox"][::-1]))
        print(f"{asset:8s} {entry['count']:7d} Gaussians from {len(views)} views ({', '.join(views)}), mean mask "
              f"coverage {coverage:.1%}, centroid ({', '.join(f'{c:.3f}' for c in entry['centroid'])}), bbox {size}")
    if args.dry_run:
        print(f"dry run: overlays in {rel(out_dir)}, nothing else written")
    else:
        out = Path(args.out) if args.out else splat_path.with_name(f"{splat_path.stem}.assets.json")
        out.write_text(json.dumps(doc) + "\n")
        print(f"wrote {rel(out)} ({out.stat().st_size // 1024} KB) and overlays in {rel(out_dir)}")
    return 1 if seg.failed or empty else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--splat", required=True, help="the binary 3DGS PLY the photos were registered against")
    parser.add_argument("--registration", help="default: <splat stem>.registration.json next to the splat")
    parser.add_argument("--viewpoints", action="append", help="viewpoint files mapping view ids to assets, later "
                        "ones winning (repeat the flag); default: the sim's extras, then assets/photos/viewpoints.yaml")
    parser.add_argument("--masks", choices=["depth", "sam"], default="depth")
    parser.add_argument("--views", help="comma-separated view ids (default: every registered view with an asset)")
    parser.add_argument("--out", help="default: <splat stem>.assets.json next to the splat")
    parser.add_argument("--overlays", help="overlay folder (default runs/segment/<time>)")
    parser.add_argument("--width", type=int, default=640, help="working width of the masks and z-buffers, pixels")
    parser.add_argument("--depth-tol", type=float, default=0.03, help="relative depth step that ends a depth mask")
    parser.add_argument("--plane-tol", type=float, default=0.01, help="depth masks skip pixels this close to the "
                        "support plane, as a fraction of the camera-to-target distance")
    parser.add_argument("--color-tol", type=float, help="depth masks also stay within this CIELAB distance of the "
                        "seed's colour (off by default)")
    parser.add_argument("--vis-tol", type=float, default=0.03, help="a view sees a Gaussian whose depth is within this "
                        "fraction of the z-buffer depth")
    parser.add_argument("--floor-tol", type=float, default=0.01, help="Gaussians this close to the support plane, as "
                        "a fraction of the camera-to-target distance, are floor and get no asset")
    parser.add_argument("--clean-voxel", type=float, default=0.03, help="voxel of the connected-piece clean pass, as "
                        "a fraction of the camera-to-target distance")
    parser.add_argument("--no-clean", action="store_true", help="keep every voted Gaussian, connected or not")
    parser.add_argument("--no-fill", action="store_true", help="do not take the unseen Gaussians inside an asset's "
                        "footprint")
    parser.add_argument("--min-opacity", type=float, default=0.3,
                        help="Gaussians this opaque or more make the z-buffer")
    parser.add_argument("--sam-model", default="facebook/sam2.1-hiera-tiny")
    parser.add_argument("--sam-python", default="python3", help="interpreter with torch, torchvision and transformers "
                        "for SAM when this one lacks them")
    parser.add_argument("--dry-run", action="store_true", help="segment and write overlays, but not the assets file")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())

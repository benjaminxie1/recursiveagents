"""Register station photos to the splat and write their camera poses into assets/photos/viewpoints.yaml (contract C5).

1. python -m scripts.register_viewpoints board
   Print assets/splats/board.pdf at 100 % (not "fit to page") and lay it flat where the capture and the stations see it.
2. Capture the scene with Scaniverse with the board in view, and export a PLY to assets/splats/.
3. From each station take one JPEG that shows the board, named after its viewpoint id, for example
   assets/photos/_stations/inv1_cabinet.jpg. Keep the EXIF (AirDrop and the "Most Compatible" camera format do).
4. python -m scripts.register_viewpoints register --splat assets/splats/scene.ply
   Writes the poses, assets/splats/scene.registration.json (full cameras, for the viewer and Afterimage stations) and an
   overlay per photo in runs/registration/<time>/ to check by eye. The board can leave the scene after that.
   If any photo fails, nothing is written (exit 1); --partial writes the photos that registered. Bad input exits 2.
   The EXIF focal length is refined from the board when the board pins it down better (--keep-focal to skip).

Poses are in the splat file's own coordinates and units, so the viewer must put cameras and pins under the same object
as the splat.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

from gridtwin.splat.index import SplatIndex
from gridtwin.splat.ply import load_ply
from gridtwin.splat.register import (Board, board_from_corners, intrinsics, load_photo, locate_board, overlay,
                                     register_photo)

REPO = Path(__file__).resolve().parents[1]
VIEWPOINTS = REPO / "assets" / "photos" / "viewpoints.yaml"
EXTRA = REPO / "gridtwin" / "sim" / "viewpoints_extra.yaml"
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}  # HEIC only to explain that it needs a JPEG export
MIN_BOARD_TAGS = 2  # one tag's 4 corners leave the board's rotation ~1.4 deg off (~15 mm at 0.6 m)


def rel(path: Path) -> str:
    path = Path(path).resolve()
    return str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)


def make_board(out: Path, board: Board = Board(), dpi: int = 300) -> None:
    """A US Letter page with the board, a 100 mm bar to check the print scale, and instructions."""
    px_mm = dpi / 25.4
    page = Image.new("L", (round(215.9 * px_mm), round(279.4 * px_mm)), 255)
    grid = Image.fromarray(board.image(px_mm))
    top = round(25 * px_mm)
    page.paste(grid, ((page.width - grid.width) // 2, top))
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=round(3.5 * px_mm))
    y = top + grid.height + round(8 * px_mm)
    x0 = (page.width - round(100 * px_mm)) // 2
    draw.rectangle([x0, y, x0 + round(100 * px_mm) - 1, y + round(3 * px_mm)], fill=0)
    w, h = board.size_mm
    lines = ["This bar is 100 mm. If it is not, the print was scaled; reprint at 100 %.",
             f"GridTwin registration board: tag36h11 ids {', '.join(map(str, board.ids))}, "
             f"{board.marker_mm:g} mm tags, {board.gap_mm:g} mm gaps ({w:g} x {h:g} mm).",
             "Lay it flat where the splat capture and every station photo can see it."]
    for i, line in enumerate(lines):
        x = (page.width - draw.textlength(line, font=font)) / 2
        draw.text((x, y + round((8 + 6 * i) * px_mm)), line, fill=0, font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    page.save(out.with_suffix(".pdf"), resolution=dpi)
    page.save(out.with_suffix(".png"), dpi=(dpi, dpi))


HEADER = re.compile(r"viewpoints:[ \t]*(#.*)?")


def one_line(e) -> str:
    return " ".join(str(e).split())


def _parse_viewpoints(text: str, name) -> tuple[dict, dict]:
    """(whole document, its viewpoints mapping); ValueError with a readable reason if it is not that shape."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"{name} is not valid YAML: {one_line(e)}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("viewpoints"), (dict, type(None))):
        raise ValueError(f"{name} has no top-level 'viewpoints:' mapping")
    return doc, dict(doc["viewpoints"] or {})


def load_viewpoints(path: Path) -> dict:
    return _parse_viewpoints(Path(path).read_text(), path)[1]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _split_comment(line: str) -> tuple[str, str]:
    """(content, trailing '# comment' or ''). A '#' after a space starts the comment unless it sits inside a quoted
    scalar, which is checked by asking YAML whether the line still means the same without it."""
    cuts = [m.start() for m in re.finditer(r"[ \t]#", line)]
    try:
        whole = yaml.safe_load(line)
    except yaml.YAMLError:  # a fragment of a multi-line entry: take the first candidate
        return (line[:cuts[0]], line[cuts[0]:].strip()) if cuts else (line, "")
    for c in cuts:
        try:
            if yaml.safe_load(line[:c]) == whole:
                return line[:c], line[c:].strip()
        except yaml.YAMLError:
            continue
    return line, ""


def write_viewpoints(path: Path, poses: dict, note: str) -> list[str]:
    """Sets each pose, adding viewpoints only viewpoints_extra.yaml had.

    Only the lines of the viewpoints being set are replaced (by one flow-style line each); every other line, comments
    and other people's entries included, stays byte for byte. Comments on or inside a replaced entry are kept: a
    trailing one stays on the new line, the others follow it. New viewpoints go after the last entry. Raises ValueError,
    without writing anything, if the file cannot be edited that way.
    """
    text = path.read_text()
    doc, entries = _parse_viewpoints(text, path)
    extra = load_viewpoints(EXTRA)
    added = [vid for vid in poses if vid not in entries]
    for vid, pose in poses.items():
        base = entries.get(vid) or extra.get(vid)
        if not isinstance(base, dict):
            raise ValueError(f"viewpoint {vid} is neither a mapping in the file nor in {rel(EXTRA)}")
        entries[vid] = {**base, "pose": pose}

    lines = text.splitlines()
    heads = [i for i, ln in enumerate(lines) if HEADER.fullmatch(ln)]
    if len(heads) != 1:
        raise ValueError("expected exactly one 'viewpoints:' line in column 0 with the entries indented below it")
    start = heads[0]
    end = start + 1  # the block runs to the next column-0 line, column-0 comments inside it included
    while end < len(lines):
        ln = lines[end]
        if ln.strip() and ln[0] not in " \t":
            nxt = next((x for x in lines[end + 1:] if x.strip() and x[0] != "#"), "")
            if ln[0] != "#" or nxt[:1] not in (" ", "\t"):
                break
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    body = [ln for ln in lines[start + 1:end] if ln.strip() and not _is_comment(ln)]
    pad = " " * (_indent(body[0]) if body else 2)

    def line(vid):
        return pad + f"{vid}: " + yaml.safe_dump(entries[vid], default_flow_style=True, sort_keys=False,
                                                 width=10 ** 6).strip()

    block, i = [], start + 1
    while i < end:
        ln = lines[i]
        own = ln.strip() and not _is_comment(ln) and _indent(ln) == len(pad)
        vid = ln.split(":", 1)[0].strip().strip("'\"") if own else None
        if vid not in poses:
            block.append(ln)
            i += 1
            continue
        comment = _split_comment(ln)[1]
        block.append(line(vid) + (f"  {comment}" if comment else ""))
        i += 1
        while i < end:  # the entry's own continuation lines: anything up to the last line indented deeper than it
            j = i
            while j < end and (not lines[j].strip() or _is_comment(lines[j])):
                j += 1
            if j == end or _indent(lines[j]) <= len(pad):
                break
            for x in lines[i:j]:
                if x.strip():
                    block.append(x)  # a comment line inside the entry
            comment = _split_comment(lines[j])[1]
            if comment:
                block.append(" " * _indent(lines[j]) + comment)
            i = j + 1
    block += [line(vid) for vid in added]
    top = [ln for ln in lines[:start] if not ln.startswith("# Poses:")] + [f"# Poses: {note}"]
    new = "\n".join([*top, lines[start], *block, *lines[end:]]) + "\n"
    try:
        after = yaml.safe_load(new)
    except yaml.YAMLError as e:
        raise ValueError(f"the edited file would not be valid YAML ({one_line(e)}); edit the entries by hand") from e
    rest = {k: v for k, v in doc.items() if k != "viewpoints"}
    if after.get("viewpoints") != entries or {k: v for k, v in after.items() if k != "viewpoints"} != rest:
        raise ValueError("rewriting it line by line would change more than the poses; check its layout")
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(new)
    tmp.replace(path)
    return added


def write_registration(path: Path, splat: Path, board: Board, fit, views: dict, stamp: datetime) -> None:
    """Full cameras per view; views from an earlier run against the same splat file are kept."""
    sha = hashlib.sha256(splat.read_bytes()).hexdigest()
    try:
        old = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        old = {}  # unreadable: it is derived data, so start it again
    kept = old.get("views", {}) if isinstance(old, dict) and old.get("splat_sha256") == sha else {}
    new = {vid: {"photo": rel(photo), "focal_35mm": f35, "focal_source": source,
                 "registered_at": stamp.isoformat(timespec="seconds"), **view.as_dict()}
           for vid, (view, photo, f35, source) in views.items()}
    doc = {"splat": rel(splat), "splat_sha256": sha, "board": board.as_dict(), "board_to_splat": fit.as_dict(),
           "views": {**kept, **new}}
    path.write_text(json.dumps(doc, indent=1) + "\n")


def register(args) -> int:
    """0 when every photo registered and the poses were written, 1 when a photo or the board search failed, 2 for bad
    input. viewpoints.yaml is written only with exit 0, or with --partial when some photos registered."""
    board, splat, folder, vp_path = Board(), Path(args.splat), Path(args.photos), Path(args.viewpoints)
    stamp = datetime.now().astimezone()
    out = Path(args.out) if args.out else REPO / "runs" / "registration" / stamp.strftime("%Y%m%d-%H%M%S")
    if not folder.is_dir():
        print(f"no photos folder at {folder}; put one <viewpoint_id>.jpg per station there or pass --photos")
        return 2
    try:
        photos = sorted(p for p in folder.iterdir() if p.suffix.lower() in PHOTO_EXTS)
        known = {**load_viewpoints(EXTRA), **load_viewpoints(vp_path)}
    except (OSError, ValueError) as e:
        print(f"cannot read the photos or viewpoints: {one_line(e)}")
        return 2
    if not photos:
        print(f"no photos in {args.photos}; name each one after its viewpoint id, e.g. inv1_cabinet.jpg")
        return 2
    unknown = [p.name for p in photos if p.stem not in known]
    if unknown:
        print(f"not a viewpoint id: {', '.join(unknown)}. Known ids: {', '.join(sorted(known))}")
        return 2
    if not splat.is_file():
        print(f"no splat file at {splat}; export a binary PLY from Scaniverse into assets/splats/ and pass --splat")
        return 2
    try:
        index = SplatIndex.from_splat(load_ply(splat))
    except (OSError, ValueError) as e:
        print(f"cannot use the splat: {one_line(e)}")
        return 2
    if len(index.centers) < 1000:
        print(f"cannot use the splat: {splat} has only {len(index.centers)} Gaussians with opacity 0.3 or more")
        return 2
    if args.board_corners:
        try:
            fit = board_from_corners(board, json.loads(Path(args.board_corners).read_text()))
        except (OSError, ValueError, TypeError) as e:
            print(f"cannot use --board-corners {args.board_corners}: {one_line(e)}; it takes JSON [[x, y, z] x 4]")
            return 2

    out.mkdir(parents=True, exist_ok=True)
    if not args.board_corners:
        fit, renders = locate_board(index, board)
        if fit is None or len(fit.tags) < MIN_BOARD_TAGS:
            for name, gray in renders:
                Image.fromarray(gray).save(out / f"board-search-{name}.png")
            seen = "no board tag" if fit is None else f"only tag {fit.tags[0]}"
            print(f"board not found in {splat} ({len(index.centers)} Gaussians): {seen} in the plane renders, which "
                  f"are in {rel(out)}; at least {MIN_BOARD_TAGS} tags are needed to fix the board's rotation. "
                  "Re-capture with the board larger in frame and flat, or pass --board-corners.")
            return 1
    print(f"board in splat: tags {fit.tags} via {fit.source}, fit {fit.rms_mm:.2f} mm rms, "
          f"{fit.scale:.4g} splat units per metre")
    if len(fit.tags) < len(board.ids):
        print(f"WARNING: the splat shows only tags {fit.tags} of {list(board.ids)}; check the overlays")

    views, failed = {}, []
    for photo in photos:
        try:
            rgb, f35 = load_photo(photo)
        except ValueError as e:
            failed.append((photo.stem, one_line(e)))
            continue
        source = "--focal-35mm" if args.focal_35mm else "EXIF"
        f35 = args.focal_35mm or f35
        if not f35:
            failed.append((photo.stem, "no EXIF focal length; pass --focal-35mm (iPhone main camera: 26)"))
            continue
        view = register_photo(rgb, intrinsics(rgb.shape[1], rgb.shape[0], f35), board, fit, index,
                              refine=not args.keep_focal)
        if view is None:
            failed.append((photo.stem, "no board tags found in the photo"))
            continue
        Image.fromarray(overlay(rgb, view, index)).save(out / f"{photo.stem}.jpg")
        where = ", ".join(f"{x:.3f}" for x in view.position)
        print(f"{photo.stem:16s} tags {view.tags} rms {view.rms_px:.2f} px at ({where}) "
              f"overlay {rel(out)}/{photo.stem}.jpg")
        print(f"{'':16s} focal {f35:g} mm ({source}) -> {view.focal_35mm:.2f} mm: {view.focal_note}")
        if len(view.tags) == 1:
            print(f"{photo.stem:16s} WARNING: PnP used only tag {view.tags[0]} (4 corners), so the pose is weak; "
                  "check the overlay or retake the photo with more of the board in frame")
        if view.rms_px > args.max_rms_px:
            failed.append((photo.stem, f"reprojection {view.rms_px:.2f} px > {args.max_rms_px} px; check the overlay"))
        else:
            views[photo.stem] = (view, photo, f35, source)

    for vid, why in failed:
        print(f"{vid:16s} SKIPPED: {why}")
    code = 1 if failed else 0
    if not views:
        print(f"nothing written: no photo registered; {rel(vp_path)} is unchanged")
        return 1
    if args.dry_run:
        print(f"dry run: {len(views)} poses solved, nothing written")
        return code
    if failed and not args.partial:
        print(f"nothing written: {len(failed)} of {len(photos)} photos failed, so {rel(vp_path)} is unchanged. Fix "
              f"the failed photos and rerun, or pass --partial to write the {len(views)} that registered")
        return 1
    note = (f"{len(views)} registered against {rel(splat)} at {stamp:%Y-%m-%d %H:%M} by "
            "scripts/register_viewpoints.py; units and axes are the splat file's own")
    try:
        added = write_viewpoints(vp_path, {vid: v.pose() for vid, (v, *_) in views.items()}, note)
    except (OSError, ValueError) as e:
        print(f"nothing written: cannot edit {rel(vp_path)}: {one_line(e)}")
        return 2
    registration = splat.with_name(f"{splat.stem}.registration.json")
    write_registration(registration, splat, board, fit, views, stamp)
    print(("--partial: " if failed else "") + f"wrote {len(views)} pose{'s' * (len(views) != 1)} to {rel(vp_path)}"
          + (f" (added {', '.join(added)})" if added else "") + f" and {rel(registration)}"
          + (f"; not written: {', '.join(vid for vid, _ in failed)}" if failed else ""))
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("board", help="write the printable board (PDF and PNG)")
    b.add_argument("--out", default=str(REPO / "assets" / "splats" / "board"), help="path without extension")
    r = sub.add_parser("register", help="solve every station photo's camera in the splat and write the poses")
    r.add_argument("--splat", required=True, help="binary 3DGS PLY that shows the board")
    r.add_argument("--photos", default=str(REPO / "assets" / "photos" / "_stations"),
                   help="folder of <viewpoint_id>.jpg photos that each show the board")
    r.add_argument("--viewpoints", default=str(VIEWPOINTS))
    r.add_argument("--focal-35mm", type=float, help="35 mm-equivalent focal length for every photo, overriding EXIF")
    r.add_argument("--board-corners", help="JSON [[x, y, z] x 4]: the grid's outer corners in the splat, picked by "
                   "hand, top-left, top-right, bottom-right, bottom-left as printed")
    r.add_argument("--max-rms-px", type=float, default=3.0, help="skip views whose reprojection error is above this")
    r.add_argument("--keep-focal", action="store_true",
                   help="use the EXIF (or --focal-35mm) focal length as is instead of refining it from the board")
    r.add_argument("--partial", action="store_true",
                   help="when some photos fail, still write the poses of those that registered (exit code stays 1)")
    r.add_argument("--dry-run", action="store_true", help="solve and write overlays, but not the poses")
    r.add_argument("--out", help="overlay folder (default runs/registration/<time>)")
    args = parser.parse_args(argv)
    if args.cmd == "board":
        make_board(Path(args.out))
        print(f"wrote {rel(Path(args.out).with_suffix('.pdf'))} and .png; print at 100 %")
        return 0
    return register(args)


if __name__ == "__main__":
    sys.exit(main())

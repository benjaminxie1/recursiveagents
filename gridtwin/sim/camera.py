"""Viewpoint + hidden state -> photo (contract C1/C5).

Source photos live in folders named after their state, so the camera copies the chosen file to an
opaque path under runs/<run_id>/frames/ and only that path is ever returned to agents.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

import yaml
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[2]
PHOTOS = REPO / "assets" / "photos"
SPLAT_RENDERS = REPO / "assets" / "splats" / "renders"
EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class Camera:
    def __init__(self, seed: int, run_dir: Path, photos_dir: Path = PHOTOS):
        self.seed = seed
        self.photos_dir = Path(photos_dir)
        self.frames_dir = Path(run_dir) / "frames"
        extra = yaml.safe_load((Path(__file__).parent / "viewpoints_extra.yaml").read_text())["viewpoints"]
        main = yaml.safe_load((self.photos_dir / "viewpoints.yaml").read_text())["viewpoints"]
        self.viewpoints = {**extra, **main}
        self.captures = 0

    def source_for(self, viewpoint, asset, state, count) -> Path:
        folder = self.photos_dir / asset / state
        files = sorted(p for p in folder.glob("*") if p.suffix.lower() in EXTS) if folder.is_dir() else []
        if not files:
            return self.photos_dir / "_placeholders" / asset / f"{state}.png"
        h = int(hashlib.sha256(f"{self.seed}|{viewpoint}|{state}|{count}".encode()).hexdigest(), 16)
        return files[h % len(files)]

    def capture(self, viewpoint, asset, mode, state) -> str:
        self.captures += 1
        src = self.source_for(viewpoint, asset, state, self.captures)
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        dst = self.frames_dir / f"{self.captures:03d}-{viewpoint}-{mode}.jpg"
        if src.exists():
            img = Image.open(src).convert("RGB")
        else:  # no photo and no placeholder yet: a plain grey card keeps the run going
            img = Image.new("RGB", (800, 600), (128, 128, 128))
            ImageDraw.Draw(img).text((20, 280), f"{asset} / {mode} (no image)", fill=(240, 240, 240))
        img.thumbnail((1024, 1024))
        img.save(dst, "JPEG", quality=88)
        try:
            return str(dst.relative_to(REPO))
        except ValueError:
            return str(dst)

    def render(self, viewpoint, mode, state):
        """The plant splat re-rendered from this viewpoint with the hidden state painted in (scripts/render_splat_views.py),
        copied to an opaque frame path like the photo. None when there is no render for it or GRIDTWIN_SPLAT=0:
        the photo is still the frame vision reads, so a missing render never breaks a run."""
        if os.environ.get("GRIDTWIN_SPLAT", "1") == "0":
            return None
        try:
            if not hasattr(self, "_render_index"):
                idx = SPLAT_RENDERS / "index.json"
                self._render_index = json.loads(idx.read_text()) if idx.exists() else {}
            hit = self._render_index.get(viewpoint, {}).get(state)
            src = REPO / hit["file"] if hit else None
            if src is None or not src.exists():
                return None
            dst = self.frames_dir / f"{self.captures:03d}-{viewpoint}-{mode}-splat.jpg"
            self.frames_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            try:
                return str(dst.relative_to(REPO))
            except ValueError:
                return str(dst)
        except Exception:  # a broken render cache must never break a camera call
            return None

"""Viewpoint + hidden state -> photo (contract C1/C5).

Source photos live in folders named after their state, so the camera copies the chosen file to an
opaque path under runs/<run_id>/frames/ and only that path is ever returned to agents.
"""
import hashlib
from pathlib import Path

import yaml
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
PHOTOS = REPO / "assets" / "photos"
EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class Camera:
    def __init__(self, seed: int, run_dir: Path, photos_dir: Path = PHOTOS):
        self.seed = seed
        self.photos_dir = Path(photos_dir)
        self.frames_dir = Path(run_dir) / "frames"
        self.viewpoints = yaml.safe_load((self.photos_dir / "viewpoints.yaml").read_text())["viewpoints"]
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
        img = Image.open(src).convert("RGB")
        img.thumbnail((1024, 1024))
        img.save(dst, "JPEG", quality=88)
        try:
            return str(dst.relative_to(REPO))
        except ValueError:
            return str(dst)

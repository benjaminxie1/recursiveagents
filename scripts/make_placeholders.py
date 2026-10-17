"""Write the placeholder cards the camera serves for states that have no real photo yet (contract C1).

Every state of an asset gets the same bytes: one card that shows only the asset id and "no photo yet",
so a placeholder never tells an agent (or the vision model reading it) which hidden state it stands for.
States come from the `states:` lists in assets/photos/viewpoints.yaml and gridtwin/sim/viewpoints_extra.yaml.

Run: python -m scripts.make_placeholders
"""
import io
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
PHOTOS = REPO / "assets" / "photos"
OUT = PHOTOS / "_placeholders"
SIZE = (800, 600)
GREY, TEXT, FAINT = (128, 128, 128), (240, 240, 240), (200, 200, 200)


def states() -> dict[str, list[str]]:
    extra = yaml.safe_load((REPO / "gridtwin" / "sim" / "viewpoints_extra.yaml").read_text())["states"]
    main = yaml.safe_load((PHOTOS / "viewpoints.yaml").read_text())["states"]
    return {**extra, **main}  # same precedence as Camera: viewpoints.yaml wins


def card(asset: str) -> bytes:
    img = Image.new("RGB", SIZE, GREY)
    d = ImageDraw.Draw(img)
    for text, size, y in ((asset, 64, 245), ("no photo yet", 32, 340)):
        font = ImageFont.load_default(size=size)
        w = d.textbbox((0, 0), text, font=font)[2]
        d.text(((SIZE[0] - w) / 2, y), text, fill=TEXT, font=font)
    d.text((20, 560), "placeholder", fill=FAINT, font=ImageFont.load_default(size=12))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def main():
    for asset, names in sorted(states().items()):
        data = card(asset)
        (OUT / asset).mkdir(parents=True, exist_ok=True)
        for state in names:
            (OUT / asset / f"{state}.png").write_bytes(data)
        print(f"{asset}: {len(names)} states")


if __name__ == "__main__":
    main()

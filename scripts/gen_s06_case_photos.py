"""Generate the RGB camera stand-ins for the s06 case pack: one fixed display camera per central inverter.

Each frame is an OpenAI image edit of a photo-bank picture of the same cabinet model (assets/photos/INV-3/...), so all
four units look like one product line. The lit displays show the sim's real ac_kw at 11:00, and INV-3's display is dark
because the inverter has safe-stopped. Output goes to assets/demo/s06_case/_src/ with the prompt beside each image;
scripts/make_s06_case.py crops, stamps and files them. Needs OPENAI_API_KEY (env, or GRIDTWIN_ENV_FILE / .env).

Run: python -m scripts.gen_s06_case_photos [--only INV-1,INV-3]
"""
import argparse
import base64
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "assets" / "demo" / "s06_case" / "_src"
MODELS = ("gpt-image-1.5", "gpt-image-1")

COMMON = (
    "Keep the camera position, lens, framing and the cabinet's size and place in the frame exactly as in the input photo. "
    "Keep the same industrial central inverter cabinet model: grey steel double doors, louvred vents, a display panel on the "
    "right door, concrete pad, gravel yard, chain-link fence and solar panel rows behind. Bright late-morning summer sun, "
    "clear sky with a few light clouds. Photorealistic, like a fixed site security camera. "
    "Add a small yellow engraved equipment tag on the upper left door that reads '{unit}' in black letters. "
)
UNITS = {
    "INV-1": ("assets/photos/INV-3/healthy/01_ai_generated.jpg",
              "The display is lit and shows '{unit}' on the top line and '1189 kW' in large digits, with a small rising power "
              "graph. Make the background a little different from the input: the fence post and the panel rows sit slightly "
              "further left."),
    "INV-2": ("assets/photos/INV-3/healthy/01_ai_generated.jpg",
              "The display is lit and shows '{unit}' on the top line and '1200 kW' in large digits, with a small rising power "
              "graph. Make the background a little different from the input: a light pole on the right and an extra panel row."),
    "INV-3": ("assets/photos/INV-3/display_off/01_ai_generated.jpg",
              "The display is completely dark: switched off, black glass with only a faint sky reflection, no text, no "
              "backlight, no status light. Everything else looks normal and undamaged."),
    "INV-4": ("assets/photos/INV-3/healthy/01_ai_generated.jpg",
              "The display is lit and shows '{unit}' on the top line and '1203 kW' in large digits, with a small rising power "
              "graph. Make the background a little different from the input: a distant tree line and a cable tray on the left."),
}


def _key() -> str:
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    for env in (os.environ.get("GRIDTWIN_ENV_FILE"), REPO / ".env", Path.home() / "claude" / "recursiveagents" / ".env"):
        if env and Path(env).is_file():
            for line in Path(env).read_text().splitlines():
                line = line.strip().removeprefix("export ").strip()
                if line.startswith("OPENAI_API_KEY="):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:  # an empty placeholder falls through to the next file
                        return value
    sys.exit("OPENAI_API_KEY not set (env, GRIDTWIN_ENV_FILE or .env)")


def _edit(unit: str, key: str) -> dict:
    src, extra = UNITS[unit]
    prompt = COMMON.format(unit=unit) + extra.format(unit=unit)
    errors = []
    for model in MODELS:
        for fidelity in (True, False):
            data = {"model": model, "prompt": prompt, "size": "1536x1024", "quality": "high", "n": "1"}
            if fidelity:
                data["input_fidelity"] = "high"
            with open(REPO / src, "rb") as f:
                r = requests.post("https://api.openai.com/v1/images/edits", headers={"Authorization": f"Bearer {key}"},
                                  data=data, files={"image": (Path(src).name, f, "image/jpeg")}, timeout=300)
            if r.ok:
                out = OUT / f"{unit}.png"
                out.write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))
                meta = {"unit": unit, "source": src, "model": model, "input_fidelity": "high" if fidelity else None,
                        "prompt": prompt}
                (OUT / f"{unit}.json").write_text(json.dumps(meta, indent=2) + "\n")
                return meta
            errors.append(f"{model} fidelity={fidelity}: {r.status_code} {r.text[:200]}")
    raise RuntimeError(f"{unit}: " + " | ".join(errors))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(UNITS))
    units = [u.strip() for u in ap.parse_args(argv).only.split(",") if u.strip()]
    OUT.mkdir(parents=True, exist_ok=True)
    key = _key()
    with ThreadPoolExecutor(len(units)) as pool:
        for unit, fut in [(u, pool.submit(_edit, u, key)) for u in units]:
            try:
                meta = fut.result()
                print(f"{unit}: ok ({meta['model']})", flush=True)
            except Exception as e:  # report every unit, then fail
                print(f"{unit}: FAILED {e}", flush=True)


if __name__ == "__main__":
    main()

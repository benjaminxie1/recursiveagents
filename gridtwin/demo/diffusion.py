"""Expected-state image for the redispatch email (CONTRACT.md stage 6).

expected_state_image(asset, src_image, prompt, out_path) -> {"image", "backend", "prompt", "source"}

When the contractor's after-repair photo fails the visual check, the verifier emails them back with a picture of
what the asset must look like when fixed (for INV-3: the same cabinet with its display lit).

Backend "diffusion": img2img on the failing photo with stabilityai/sd-turbo (diffusers; Apple MPS or CUDA when
present), so the picture keeps the contractor's own framing and only the fault area should change. The model is
loaded once per process and only from the local Hugging Face cache, so a demo run never starts a 2.6 GB download.
Backend "photo_bank": the healthy reference photo from assets/photos/<asset>/healthy/, preferring the one with the
same file name as the source (the bank pairs fault and healthy shots by angle).

Both get a small "EXPECTED STATE" banner so nobody mistakes the picture for a real site photo. Never raises: a
missing install, a load or generation error, or a run longer than DIFFUSION_TIMEOUT_S falls back to the photo bank;
GRIDTWIN_NO_DIFFUSION=1 forces the fallback.

Setup and one example run (downloads the weights on first use):
    .venv/bin/python -m gridtwin.demo.diffusion --download
"""
from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
PHOTOS = REPO_ROOT / "assets" / "photos"
# Healthy-state folders in the photo bank, best first (XFMR-1 calls it "normal").
HEALTHY_DIRS = ("healthy", "normal", "thermal_normal")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")

MODEL_ID = "stabilityai/sd-turbo"
# Long edge of the img2img canvas. sd-turbo is trained at 512 px; the result is resized back to the source size.
DIFFUSION_EDGE_PX = 512
# sd-turbo runs int(steps * strength) denoising steps; 0.5 keeps the cabinet, doors and framing of the source.
DIFFUSION_STEPS = 2
DIFFUSION_STRENGTH = 0.5
DIFFUSION_SEED = 7
# Covers the model load on the first call and the generation itself. Past this the photo bank answers.
DIFFUSION_TIMEOUT_S = float(os.environ.get("GRIDTWIN_DIFFUSION_TIMEOUT_S", "20"))
STYLE_SUFFIX = ", realistic photo, outdoor solar plant, daylight, sharp focus"

_pipe = None
_pipe_lock = threading.Lock()
# Held while a generation runs. A call that finds it held (an earlier run still going past its timeout) falls back
# at once instead of queueing behind it.
_busy = threading.Lock()


# ---------- public API ----------

def expected_state_image(asset: str, src_image, prompt: str, out_path) -> dict:
    """Write the expected-state picture for `asset` to `out_path`. Never raises."""
    prompt = (prompt or "").strip() or f"{asset} in its healthy, working state"
    try:
        out = _resolve(out_path)
        if diffusion_enabled():
            try:
                src = _resolve(src_image)
                t0 = time.monotonic()
                image = _run_with_timeout(lambda: _diffuse(src, prompt), DIFFUSION_TIMEOUT_S)
                _save(_label(image, "EXPECTED STATE", f"AI-generated from your photo ({MODEL_ID})"), out)
                log.info("diffusion: %s in %.1fs -> %s", asset, time.monotonic() - t0, out)
                return _result(out, "diffusion", prompt, src)
            except Exception as exc:  # noqa: BLE001 - any diffusion failure means the photo bank answers
                log.warning("diffusion failed for %s, using the photo bank: %s", asset, exc)
        return _photo_bank(asset, src_image, prompt, out)
    except Exception as exc:  # noqa: BLE001 - the contract says never raise
        log.error("expected_state_image failed for %s: %s", asset, exc)
        return {"image": "", "backend": "photo_bank", "prompt": prompt, "source": ""}


def diffusion_enabled() -> bool:
    """True unless GRIDTWIN_NO_DIFFUSION is set or torch/diffusers are not installed. Imports nothing heavy."""
    if os.environ.get("GRIDTWIN_NO_DIFFUSION", "").strip().lower() not in ("", "0", "false"):
        return False
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "diffusers"))


def warm_up(background: bool = True) -> None:
    """Load the pipeline ahead of the first redispatch so that call fits in DIFFUSION_TIMEOUT_S. Never raises."""
    if not diffusion_enabled():
        return

    def load():
        try:
            _pipeline()
        except Exception as exc:  # noqa: BLE001
            log.warning("diffusion warm-up failed: %s", exc)

    if background:
        threading.Thread(target=load, name="gridtwin-diffusion-warmup", daemon=True).start()
    else:
        load()


# ---------- photo bank ----------

def healthy_photo(asset: str, src_image=None) -> Path | None:
    """The healthy reference photo for `asset`: same file name as `src_image` when the bank has it, else the first."""
    name = Path(str(src_image)).name if src_image else ""
    for folder in HEALTHY_DIRS:
        d = PHOTOS / asset / folder
        if not d.is_dir():
            continue
        photos = sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        if photos:
            return next((p for p in photos if p.name == name), photos[0])
    return None


def _photo_bank(asset: str, src_image, prompt: str, out: Path) -> dict:
    from PIL import Image

    ref = healthy_photo(asset, src_image)
    if ref is None:
        log.warning("photo bank has no healthy photo for %s; writing a text card", asset)
        _save(_card(asset, prompt), out)
        return _result(out, "photo_bank", prompt, None)
    with Image.open(ref) as im:
        _save(_label(im, "EXPECTED STATE", f"reference photo: {asset} healthy"), out)
    return _result(out, "photo_bank", prompt, ref)


# ---------- diffusion ----------

def _device():
    import torch

    if torch.backends.mps.is_available():
        return "mps", torch.float16
    if torch.cuda.is_available():
        return "cuda", torch.float16
    return "cpu", torch.float32


def _pipeline(allow_download: bool = False):
    """The cached sd-turbo img2img pipeline. Loads from the local cache only unless allow_download."""
    global _pipe
    if _pipe is None:
        with _pipe_lock:
            if _pipe is None:
                import torch
                from diffusers import AutoPipelineForImage2Image

                device, dtype = _device()
                kwargs = {"torch_dtype": dtype, "local_files_only": not allow_download}
                if dtype == torch.float16:
                    kwargs["variant"] = "fp16"
                t0 = time.monotonic()
                pipe = AutoPipelineForImage2Image.from_pretrained(MODEL_ID, **kwargs).to(device)
                pipe.set_progress_bar_config(disable=True)
                log.info("diffusion: loaded %s on %s in %.1fs", MODEL_ID, device, time.monotonic() - t0)
                _pipe = pipe
    return _pipe


def _canvas_size(size: tuple[int, int], edge: int = DIFFUSION_EDGE_PX) -> tuple[int, int]:
    """Scale so the long edge is `edge`, both sides multiples of 8 (the VAE downsamples by 8)."""
    w, h = size
    scale = edge / max(w, h)
    return max(8, round(w * scale / 8) * 8), max(8, round(h * scale / 8) * 8)


def _diffuse(src: Path, prompt: str):
    """img2img on `src`. Returns a PIL image at the source's size. Raises on any failure."""
    import torch
    from PIL import Image

    pipe = _pipeline()
    with Image.open(src) as im:
        source = im.convert("RGB")
    init = source.resize(_canvas_size(source.size), Image.LANCZOS)
    out = pipe(
        prompt=prompt + STYLE_SUFFIX,
        image=init,
        num_inference_steps=DIFFUSION_STEPS,
        strength=DIFFUSION_STRENGTH,
        guidance_scale=0.0,
        generator=torch.Generator("cpu").manual_seed(DIFFUSION_SEED),
    ).images[0]
    if out.convert("L").getextrema()[1] == 0:
        raise RuntimeError("diffusion returned a black image")
    return out.resize(source.size, Image.LANCZOS)


def _run_with_timeout(fn, timeout_s: float):
    """Run fn on a daemon thread. Raises TimeoutError past timeout_s (the thread finishes in the background)."""
    if not _busy.acquire(blocking=False):
        raise TimeoutError("an earlier diffusion run is still going")
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            box["error"] = exc
        finally:
            _busy.release()

    t = threading.Thread(target=target, name="gridtwin-diffusion", daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise TimeoutError(f"diffusion took longer than {timeout_s:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


# ---------- image helpers ----------

def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no sized default font
        return ImageFont.load_default()


def _label(im, title: str, subtitle: str):
    """Copy of `im` with a dark banner in the top-left corner: a title line and a smaller subtitle line."""
    from PIL import Image, ImageDraw

    base = im.convert("RGBA")
    unit = max(12, base.width // 48)
    big, small = _font(unit + 6), _font(unit)
    pad = unit // 2
    probe = ImageDraw.Draw(base)
    tb = probe.textbbox((0, 0), title, font=big)
    sb = probe.textbbox((0, 0), subtitle, font=small)
    w = max(tb[2], sb[2]) + 2 * pad
    h = tb[3] + sb[3] + 3 * pad
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle((pad, pad, pad + w, pad + h), fill=(12, 16, 20, 200))
    out = Image.alpha_composite(base, overlay).convert("RGB")
    draw = ImageDraw.Draw(out)
    draw.text((2 * pad, 2 * pad), title, font=big, fill=(120, 230, 140))
    draw.text((2 * pad, 3 * pad + tb[3]), subtitle, font=small, fill=(235, 235, 235))
    return out


def _card(asset: str, prompt: str):
    """Last resort when the bank has nothing for the asset: the expected state as text on a plain card."""
    import textwrap

    from PIL import Image, ImageDraw

    im = Image.new("RGB", (1024, 768), (32, 38, 46))
    body = textwrap.fill(f"{asset}: {prompt}", width=48)
    ImageDraw.Draw(im).multiline_text((48, 160), body, font=_font(30), fill=(235, 235, 235), spacing=12)
    return _label(im, "EXPECTED STATE", "no reference photo on file")


def _save(im, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.suffix.lower() in (".jpg", ".jpeg"):
        im.convert("RGB").save(out, quality=90)
    else:
        im.save(out)


# ---------- paths ----------

def _resolve(path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def _rel(path: Path | None) -> str:
    """Repo-relative path (what the demo server serves), or the absolute path for files outside the repo."""
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _result(out: Path, backend: str, prompt: str, source: Path | None) -> dict:
    return {"image": _rel(out), "backend": backend, "prompt": prompt, "source": _rel(source)}


# ---------- manual run ----------

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Generate the expected-state image twice and time both calls.")
    ap.add_argument("--asset", default="INV-3")
    ap.add_argument("--src", default="assets/photos/INV-3/display_off/01_ai_generated.jpg")
    ap.add_argument(
        "--prompt",
        default="the same inverter cabinet with its touchscreen display lit and showing live readings",
    )
    ap.add_argument("--out", default="runs/diffusion-check/expected.png")
    ap.add_argument("--download", action="store_true", help=f"allow downloading {MODEL_ID} (about 2.6 GB)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.download and diffusion_enabled():
        t0 = time.monotonic()
        _pipeline(allow_download=True)
        print(f"pipeline ready in {time.monotonic() - t0:.1f}s")
    out = Path(args.out)
    for i in (1, 2):  # the second call shows the cached-pipeline time the demo sees
        t0 = time.monotonic()
        result = expected_state_image(args.asset, args.src, args.prompt, out.with_name(f"{out.stem}-{i}{out.suffix}"))
        print(f"call {i}: {time.monotonic() - t0:.1f}s {result}")


if __name__ == "__main__":
    main()

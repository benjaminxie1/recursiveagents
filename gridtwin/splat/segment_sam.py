"""SAM 2.1 on station photos, behind SamMasks in gridtwin/splat/segment.py (scripts/segment_assets.py --masks sam).

Runs Hugging Face transformers' Sam2Model with facebook/sam2.1-hiera-tiny (Apache-2.0) on MPS, else the CPU. The
interpreter running segment_assets needs torch, torchvision and transformers; when it lacks them, this file serves the
model from another interpreter that has them (--sam-python, default python3), so it imports only numpy at the top.
Nothing is downloaded from here: the weights load with local_files_only, and anything missing raises ModelUnavailable
naming the exact download for the user to approve.

Helper protocol, one JSON object per line: once the model is loaded the helper prints {"ready": true, "device": ...}
or {"error": ...}; then it answers each {"image": <.npy of RGB uint8>, "point": [x, y], "box": [x0, y0, x1, y1] or
null, "out": <.npy path>} by saving the candidate masks (K, H, W) bool to "out" and printing {"scores": [...]}, or
{"error": ...}.
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

MODEL = "facebook/sam2.1-hiera-tiny"
HF_CACHE = Path(os.environ.get("HF_HUB_CACHE", Path.home() / ".cache" / "huggingface" / "hub"))
WEIGHTS = {MODEL: ("https://huggingface.co/facebook/sam2.1-hiera-tiny", "155,908,064 bytes (155.9 MB)")}
TORCHVISION = ("torchvision==0.29.1", "https://pypi.org/project/torchvision/0.29.1/",
               "1,813,852 bytes (1.8 MB) cp314 macOS arm64 wheel")


class ModelUnavailable(RuntimeError):
    """SAM cannot run here; the message names what to install or download."""


def weights_cached(model: str) -> bool:
    return any((HF_CACHE / f"models--{model.replace('/', '--')}" / "snapshots").glob("*/model.safetensors"))


def missing_message(model: str, problems: list, python: str | None) -> str:
    """What failed in each interpreter tried, and the downloads that would make SAM run, for the user to approve."""
    py = python or "python3"
    url, size = WEIGHTS.get(model, (f"https://huggingface.co/{model}", "size unknown"))
    lines = [f"SAM ({model}) cannot run yet. Tried:", *[f"  {p}" for p in problems],
             "With the user's OK, these make it run (nothing here downloads on its own):"]
    if any("torchvision" in p for p in problems):
        pkg, src, wheel = TORCHVISION
        lines.append(f"  - {pkg} from {src}, {wheel}, BSD-3-Clause: {py} -m pip install {pkg}")
    if weights_cached(model):
        lines.append(f"  - weights {model}: already in {HF_CACHE}")
    else:
        lines.append(f"  - weights {model} (model.safetensors and its JSON configs, not the .pt checkpoint) "
                     f"from {url}, {size}, Apache-2.0, into {HF_CACHE}: {py} -c \"from huggingface_hub import "
                     f"snapshot_download; "
                     f"snapshot_download('{model}', allow_patterns=['*.json', 'model.safetensors'])\"")
    lines.append("Then rerun with --masks sam. The depth masks (--masks depth, the default) need none of this.")
    return "\n".join(lines)


class Sam2Predictor:
    """SAM 2 in this interpreter."""

    def __init__(self, model: str = MODEL, device: str | None = None):
        try:
            import torch
            from transformers import Sam2Model, Sam2Processor
        except ImportError as e:
            raise ModelUnavailable(f"{sys.executable}: {e}") from e
        missing = [] if importlib.util.find_spec("torchvision") else ["No module named 'torchvision'"]
        if not weights_cached(model):
            missing.append(f"no {model} weights in {HF_CACHE}")
        if missing:
            raise ModelUnavailable(f"{sys.executable}: {'; '.join(missing)}")
        self.torch = torch
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        try:
            self.processor = Sam2Processor.from_pretrained(model, local_files_only=True)
            self.model = Sam2Model.from_pretrained(model, local_files_only=True).to(self.device).eval()
        except Exception as e:  # a missing torchvision or missing weights both mean "not available here"
            raise ModelUnavailable(f"{sys.executable}: {type(e).__name__}: {e}".splitlines()[0]) from e

    def predict(self, rgb: np.ndarray, point, box=None):
        """Candidate masks (K, H, W) bool at the image's size, and their predicted IoU (K,)."""
        from PIL import Image
        prompts = {"input_points": [[[[float(point[0]), float(point[1])]]]], "input_labels": [[[1]]]}
        if box is not None:
            prompts["input_boxes"] = [[[float(v) for v in box]]]
        inputs = self.processor(images=Image.fromarray(rgb), return_tensors="pt", **prompts).to(self.device)
        with self.torch.no_grad():
            out = self.model(**inputs, multimask_output=True)
        masks = self.processor.post_process_masks(out.pred_masks.cpu(), inputs["original_sizes"])[0]
        return masks[0].numpy().astype(bool), out.iou_scores[0, 0].float().cpu().numpy()

    def close(self):
        pass


class HelperPredictor:
    """SAM 2 served by this file under another interpreter; the model loads once."""

    def __init__(self, python: str, model: str = MODEL, device: str | None = None):
        cmd = [python, "-P", str(Path(__file__).resolve()), "--serve", model] + (["--device", device] if device else [])
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                         env={**os.environ, "PYTORCH_ENABLE_MPS_FALLBACK": "1", "HF_HUB_OFFLINE": "1"})
        except OSError as e:
            raise ModelUnavailable(f"{python}: {e}") from e
        hello = self._read()
        if not hello.get("ready"):
            self.close()
            raise ModelUnavailable(hello.get("error") or f"{python}: the helper exited without loading the model")
        self.device = hello.get("device")
        self.dir = Path(tempfile.mkdtemp(prefix="gridtwin-sam-"))

    def _read(self) -> dict:
        line = self.proc.stdout.readline()
        return json.loads(line) if line.strip() else {}

    def predict(self, rgb: np.ndarray, point, box=None):
        image, out = self.dir / "image.npy", self.dir / "masks.npy"
        np.save(image, np.ascontiguousarray(rgb))
        request = {"image": str(image), "point": [float(v) for v in point],
                   "box": None if box is None else [float(v) for v in box], "out": str(out)}
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        reply = self._read()
        if "scores" not in reply:
            raise RuntimeError(f"SAM helper: {reply.get('error', 'no reply')}")
        return np.load(out), np.asarray(reply["scores"], np.float32)

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)
        if getattr(self, "dir", None):
            shutil.rmtree(self.dir, ignore_errors=True)


def load_predictor(model: str = MODEL, python: str | None = "python3", device: str | None = None):
    """SAM 2 in this interpreter if it can, else served from python; ModelUnavailable says what is missing."""
    problems = []
    try:
        return Sam2Predictor(model, device)
    except ModelUnavailable as e:
        problems.append(str(e))
    found = shutil.which(python) if python else None
    if found and Path(found).absolute() != Path(sys.executable).absolute():  # a venv links to its base binary
        try:
            return HelperPredictor(python, model, device)
        except ModelUnavailable as e:
            problems.append(str(e))
    elif python:
        problems.append(f"{python}: not found, or the same interpreter")
    raise ModelUnavailable(missing_message(model, problems, python))


def serve(model: str, device: str | None = None) -> int:
    """The helper side of HelperPredictor: protocol lines on stdout, everything libraries print goes to stderr."""
    out, sys.stdout = sys.stdout, sys.stderr

    def say(message):
        out.write(json.dumps(message) + "\n")
        out.flush()

    try:
        predictor = Sam2Predictor(model, device)
    except ModelUnavailable as e:
        say({"error": str(e)})
        return 1
    say({"ready": True, "device": predictor.device})
    for line in sys.stdin:
        request = json.loads(line)
        try:
            masks, scores = predictor.predict(np.load(request["image"]), request["point"], request.get("box"))
            np.save(request["out"], masks)
            say({"scores": [float(s) for s in scores]})
        except Exception as e:
            say({"error": f"{type(e).__name__}: {e}"})
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SAM 2 helper for gridtwin/splat/segment.py (see the docstring)")
    parser.add_argument("--serve", default=MODEL, metavar="MODEL")
    parser.add_argument("--device")
    args = parser.parse_args()
    sys.exit(serve(args.serve, args.device))

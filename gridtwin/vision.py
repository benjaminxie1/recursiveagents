"""Contract C6: photo perception for workers, the verifier and calibration.

describe(image_path, question) -> str
check(image_path, claim) -> {"pass": bool, "confidence": float, "evidence": str}

Both send the photo as a base64 JPEG data URL (long edge <= 1024 px) to the `vision` role (Nemotron Omni)
through gridtwin.llm.chat. If Omni errors or takes longer than VISION_TIMEOUT_S, the call is retried once on
`vision_fast` (Gemma) and a warning is logged. check never raises: any failure is pass False, confidence 0.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from pathlib import Path

from gridtwin import llm
from gridtwin.config import ROLES

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_EDGE_PX = 1024
VISION_TIMEOUT_S = 60.0
VISION_TRIES = 3  # per model; after that Omni counts as unavailable and Gemma answers
THINKING = True  # Nemotron Omni's reasoning phase; Gemma has none
# The verifier counts a visual check as passed only at this confidence or above; calibration uses the same bar.
PASS_CONFIDENCE = 0.7

# Fixed prompts, byte-identical on every call so the prefix caches. Calibration uses these too.
# Evidence comes before the verdict: asked for the verdict first, Omni confirmed "no X visible" claims
# without looking (it passed "no oil" on a photo it described as oil-stained when asked directly).
CHECK_PROMPT = (
    "You are inspecting one photo taken at a solar power plant (panels, cabinets, breakers, "
    "transformers or similar equipment). Decide whether the photo supports the claim below. "
    "Judge only what is visible. Look over the whole photo first, including small details such as stains, "
    "streaks, discoloration, readings and labels. If the claim says something is absent or normal, it fails "
    "when you can see any sign of that thing. If the photo is unclear or does not show the subject of the claim, "
    "answer pass false with low confidence.\n"
    'Reply with JSON only, writing the evidence before the verdict: {"evidence": "one or two sentences on what '
    'you see that decides it", "pass": true or false, "confidence": number from 0 to 1}\n'
    "Claim: "
)
# Temperature 0 for verdicts: the same photo and claim should get the same answer every time.
CHECK_TEMPERATURE = 0
DESCRIBE_PROMPT = (
    "You are inspecting one photo taken at a solar power plant. Answer the question below in at most "
    "three sentences, describing only what is visible.\nQuestion: "
)


def _resolve(image_path) -> Path:
    p = Path(image_path)
    return p if p.is_absolute() else REPO_ROOT / p


def image_part(image_path) -> dict:
    """OpenAI image_url content part: JPEG data URL, long edge <= 1024 px."""
    from PIL import Image

    with Image.open(_resolve(image_path)) as im:
        im = im.convert("RGB")
        im.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def _call(role: str, msgs: list, **kwargs) -> str:
    resp = llm.chat(role, msgs, max_tokens=2048, timeout=VISION_TIMEOUT_S, max_tries=VISION_TRIES, **kwargs)
    # Empty content means the answer landed in the reasoning field (or the model ran out of tokens).
    return (llm.text(resp) or llm.reasoning(resp)).strip()


def ask(image_path, prompt: str, **kwargs) -> tuple[str, str]:
    """Send one photo and a prompt. Returns (reply text, model id that answered). Raises if both models fail."""
    msgs = [{"role": "user", "content": [{"type": "text", "text": prompt}, image_part(image_path)]}]
    try:
        return _call("vision", msgs, **{"thinking": THINKING, **kwargs}), ROLES["vision"]
    except Exception as e:  # unavailable, too slow, or still rate-limited after VISION_TRIES attempts
        log.warning("vision: %s failed (%s: %s); falling back to %s",
                    ROLES["vision"], type(e).__name__, e, ROLES["vision_fast"])
    return _call("vision_fast", msgs, **kwargs), ROLES["vision_fast"]


def parse_check(text: str) -> dict:
    """Pull {"pass", "confidence", "evidence"} out of a model reply; anything unreadable is a low-confidence fail."""
    data, dec, text = None, json.JSONDecoder(), text or ""
    for m in re.finditer(r"\{", text):  # last JSON object with a "pass" key wins
        try:
            cand, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(cand, dict) and "pass" in cand:
            data = cand
    if data is None:
        return {"pass": False, "confidence": 0.0, "evidence": f"unreadable vision reply: {(text or '')[:200]}"}
    ok = data.get("pass")
    if isinstance(ok, str):
        ok = ok.strip().lower() in ("true", "yes", "pass")
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    if conf > 1.0:  # some replies use a 0-100 scale
        conf /= 100.0
    return {"pass": bool(ok), "confidence": max(0.0, min(1.0, conf)), "evidence": str(data.get("evidence", ""))}


def check_detail(image_path, claim: str) -> dict:
    """check() plus the model id that answered, for calibration and logs."""
    try:
        text, model = ask(image_path, CHECK_PROMPT + claim, temperature=CHECK_TEMPERATURE)
    except Exception as e:
        return {"pass": False, "confidence": 0.0, "evidence": f"vision unavailable: {type(e).__name__}", "model": None}
    return {**parse_check(text), "model": model}


def check(image_path, claim: str) -> dict:
    """Does the photo support the claim? {"pass": bool, "confidence": 0..1, "evidence": str}. Never raises."""
    res = check_detail(image_path, claim)
    res.pop("model")
    return res


def describe(image_path, question: str) -> str:
    """Answer a question about the photo in at most three sentences."""
    try:
        text, _ = ask(image_path, DESCRIBE_PROMPT + question)
    except Exception as e:
        return f"vision unavailable: {type(e).__name__}"
    return text

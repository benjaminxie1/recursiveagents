"""Fallback photo checks used by dod.evaluate when gridtwin.vision (workstream 3) is not importable.

Same contract as gridtwin.vision: check(image_path, claim) -> {"pass", "confidence", "evidence"},
describe(image_path, question) -> str. Any failure yields pass False, confidence 0.
"""
from __future__ import annotations

import base64
import io
from pathlib import Path

from gridtwin.agents.common import extract_json, num

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_EDGE_PX = 1024

CHECK_PROMPT = (
    "You are inspecting one photo taken at a solar power plant (panels, cabinets, breakers, "
    "transformers or similar equipment). Decide whether the photo supports the claim below. "
    "Judge only what is visible. If the photo is unclear or does not show the subject of the claim, "
    "answer pass false with low confidence.\n"
    'Reply with JSON only: {"pass": true or false, "confidence": number from 0 to 1, '
    '"evidence": "one or two sentences on what you see that decides it"}\n'
    "Claim: "
)
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


def _ask(image_path, prompt: str) -> str:
    from gridtwin import llm

    msgs = [{"role": "user", "content": [{"type": "text", "text": prompt}, image_part(image_path)]}]
    resp = llm.chat("vision", msgs, max_tokens=2048)
    msg = resp.choices[0].message
    text = msg.content or ""
    if not text.strip():  # reasoning models sometimes leave the answer in the reasoning field
        text = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None) or ""
    return text


def check(image_path, claim) -> dict:
    try:
        data = extract_json(_ask(image_path, CHECK_PROMPT + str(claim)))
        if not isinstance(data, dict):
            return {"pass": False, "confidence": 0.0, "evidence": "vision reply was not JSON"}
        raw = data.get("pass")
        passed = raw is True or str(raw).strip().lower() == "true"
        conf = num(data.get("confidence"))
        conf = 0.0 if conf is None else max(0.0, min(1.0, conf))
        return {"pass": passed, "confidence": conf, "evidence": str(data.get("evidence", ""))[:600]}
    except Exception as ex:
        return {"pass": False, "confidence": 0.0, "evidence": f"vision error: {type(ex).__name__}"}


def describe(image_path, question) -> str:
    try:
        return _ask(image_path, DESCRIBE_PROMPT + str(question)).strip()
    except Exception as ex:
        return f"vision error: {type(ex).__name__}"

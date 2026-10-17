"""gridtwin.vision without the network: reply parsing, the Gemma fallback, and check never raising."""
from types import SimpleNamespace

import pytest
from PIL import Image

from gridtwin import llm, vision
from gridtwin.config import ROLES


def _resp(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")])


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "big.png"
    Image.new("RGB", (3000, 1500), "gray").save(p)
    return p


def test_image_part_is_a_jpeg_data_url_at_most_1024px(photo):
    import base64
    import io

    url = vision.image_part(photo)["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    im = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert max(im.size) == 1024


@pytest.mark.parametrize("text, want", [
    ('{"evidence": "a {brace} inside", "pass": true, "confidence": 0.9}', (True, 0.9)),
    ('```json\n{"evidence": "e", "pass": "false", "confidence": 85}\n```', (False, 0.85)),
    ('no json at all', (False, 0.0)),
])
def test_parse_check(text, want):
    r = vision.parse_check(text)
    assert (r["pass"], r["confidence"]) == want and isinstance(r["evidence"], str)


def test_check_falls_back_to_vision_fast_when_omni_fails(photo, monkeypatch):
    calls = []

    def fake_chat(role, msgs, **kw):
        calls.append(role)
        if role == "vision":
            raise RuntimeError("omni down")
        return _resp('{"evidence": "clean tank", "pass": true, "confidence": 0.8}')

    monkeypatch.setattr(llm, "chat", fake_chat)
    r = vision.check_detail(photo, "The transformer looks normal.")
    assert calls == ["vision", "vision_fast"]
    assert r == {"pass": True, "confidence": 0.8, "evidence": "clean tank", "model": ROLES["vision_fast"]}
    assert set(vision.check(photo, "x")) == {"pass", "confidence", "evidence"}


def test_check_never_raises(photo, monkeypatch):
    def down(*a, **kw):
        raise RuntimeError("everything down")

    monkeypatch.setattr(llm, "chat", down)
    assert vision.check(photo, "x") == {"pass": False, "confidence": 0.0, "evidence": "vision unavailable: RuntimeError"}
    assert vision.describe(photo, "what is this?").startswith("vision unavailable")

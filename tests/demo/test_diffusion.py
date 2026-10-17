"""gridtwin.demo.diffusion without the network or torch: the photo-bank fallback, the forced fallback, and a failing
or slow diffusion backend falling back instead of raising."""
import time

import pytest
from PIL import Image

from gridtwin.demo import diffusion

SRC = "assets/photos/INV-3/display_off/01_ai_generated.jpg"
PROMPT = "the same inverter cabinet with its touchscreen display lit and showing live readings"
KEYS = {"image", "backend", "prompt", "source"}


@pytest.fixture
def no_diffusion(monkeypatch):
    monkeypatch.setenv("GRIDTWIN_NO_DIFFUSION", "1")


@pytest.fixture
def fake_backend(monkeypatch):
    """Pretend torch and diffusers are installed; tests swap in their own _diffuse."""
    monkeypatch.delenv("GRIDTWIN_NO_DIFFUSION", raising=False)
    monkeypatch.setattr(diffusion.importlib.util, "find_spec", lambda name: object())


def test_photo_bank_fallback_writes_the_same_angle_healthy_photo(no_diffusion, tmp_path):
    out = tmp_path / "img" / "expected-r1.png"
    r = diffusion.expected_state_image("INV-3", SRC, PROMPT, out)
    assert set(r) == KEYS
    assert r["backend"] == "photo_bank" and r["prompt"] == PROMPT
    assert r["source"] == "assets/photos/INV-3/healthy/01_ai_generated.jpg"
    assert r["image"] == str(out) and out.is_file()
    with Image.open(out) as im, Image.open(diffusion.REPO_ROOT / r["source"]) as ref:
        ref = ref.convert("RGB")
        assert im.size == ref.size
        # The banner sits in the top-left corner and the rest of the photo is untouched.
        assert im.getpixel((20, 20)) != ref.getpixel((20, 20))
        assert im.getpixel((im.width - 5, im.height - 5)) == ref.getpixel((ref.width - 5, ref.height - 5))


def test_image_path_is_repo_relative_inside_the_repo(no_diffusion):
    out = diffusion.REPO_ROOT / "runs" / f"pytest-diffusion-{time.time_ns()}" / "expected.jpg"
    try:
        r = diffusion.expected_state_image("INV-3", SRC, PROMPT, out)
        assert r["image"] == out.relative_to(diffusion.REPO_ROOT).as_posix()
        assert (diffusion.REPO_ROOT / r["image"]).is_file()
    finally:
        out.unlink(missing_ok=True)
        if out.parent.exists():
            out.parent.rmdir()


def test_env_var_forces_the_fallback_even_when_diffusion_is_installed(fake_backend, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("diffusion ran although GRIDTWIN_NO_DIFFUSION=1")

    monkeypatch.setattr(diffusion, "_diffuse", boom)
    monkeypatch.setenv("GRIDTWIN_NO_DIFFUSION", "1")
    assert not diffusion.diffusion_enabled()
    r = diffusion.expected_state_image("INV-3", SRC, PROMPT, tmp_path / "e.png")
    assert r["backend"] == "photo_bank" and (tmp_path / "e.png").is_file()


def test_failing_backend_falls_back_to_the_photo_bank(fake_backend, monkeypatch, tmp_path):
    def broken(src, prompt):
        raise RuntimeError("MPS out of memory")

    monkeypatch.setattr(diffusion, "_diffuse", broken)
    r = diffusion.expected_state_image("INV-3", SRC, PROMPT, tmp_path / "e.png")
    assert r["backend"] == "photo_bank"
    assert r["source"] == "assets/photos/INV-3/healthy/01_ai_generated.jpg"
    assert (tmp_path / "e.png").is_file()


def test_slow_backend_times_out_to_the_photo_bank(fake_backend, monkeypatch, tmp_path):
    monkeypatch.setattr(diffusion, "DIFFUSION_TIMEOUT_S", 0.05)
    monkeypatch.setattr(diffusion, "_diffuse", lambda src, prompt: time.sleep(0.5) or Image.new("RGB", (8, 8)))
    t0 = time.monotonic()
    r = diffusion.expected_state_image("INV-3", SRC, PROMPT, tmp_path / "e.png")
    assert r["backend"] == "photo_bank" and time.monotonic() - t0 < 0.4
    # A call while the slow run is still going falls back at once instead of queueing behind it.
    assert diffusion.expected_state_image("INV-3", SRC, PROMPT, tmp_path / "f.png")["backend"] == "photo_bank"
    time.sleep(0.6)  # let the background run finish so it releases the busy lock for the next test
    assert not diffusion._busy.locked()


def test_working_backend_labels_its_output(fake_backend, monkeypatch, tmp_path):
    seen = {}

    def fake(src, prompt):
        seen.update(src=src, prompt=prompt)
        return Image.new("RGB", (640, 480), (90, 120, 160))

    monkeypatch.setattr(diffusion, "_diffuse", fake)
    out = tmp_path / "e.png"
    r = diffusion.expected_state_image("INV-3", SRC, PROMPT, out)
    assert r == {"image": str(out), "backend": "diffusion", "prompt": PROMPT, "source": SRC}
    assert seen == {"src": diffusion.REPO_ROOT / SRC, "prompt": PROMPT}
    with Image.open(out) as im:
        assert im.size == (640, 480) and im.getpixel((20, 20)) != (90, 120, 160)


def test_missing_install_means_photo_bank(monkeypatch, tmp_path):
    monkeypatch.delenv("GRIDTWIN_NO_DIFFUSION", raising=False)
    monkeypatch.setattr(diffusion.importlib.util, "find_spec", lambda name: None)
    assert not diffusion.diffusion_enabled()
    r = diffusion.expected_state_image("INV-3", SRC, PROMPT, tmp_path / "e.png")
    assert r["backend"] == "photo_bank"


@pytest.mark.parametrize("src, want", [
    ("assets/photos/INV-3/display_off/03_ai_generated.jpg", "assets/photos/INV-3/healthy/03_ai_generated.jpg"),
    ("runs/x/contractor-photo.jpg", "assets/photos/INV-3/healthy/01_ai_generated.jpg"),
    (None, "assets/photos/INV-3/healthy/01_ai_generated.jpg"),
])
def test_healthy_photo_prefers_the_same_angle(src, want):
    assert diffusion.healthy_photo("INV-3", src) == diffusion.REPO_ROOT / want


def test_unknown_asset_still_gets_an_image_and_never_raises(no_diffusion, tmp_path):
    r = diffusion.expected_state_image("NOPE-9", "missing.jpg", "", tmp_path / "e.png")
    assert r["backend"] == "photo_bank" and r["source"] == ""
    assert r["prompt"] and (tmp_path / "e.png").is_file()


def test_unwritable_output_returns_an_empty_image_instead_of_raising(no_diffusion, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    r = diffusion.expected_state_image("INV-3", SRC, PROMPT, blocker / "e.png")
    assert set(r) == KEYS and r["image"] == ""


def test_canvas_size_keeps_the_aspect_and_multiples_of_8():
    assert diffusion._canvas_size((1024, 768)) == (512, 384)
    w, h = diffusion._canvas_size((1024, 936))
    assert w == 512 and h % 8 == 0 and abs(h - 468) <= 8

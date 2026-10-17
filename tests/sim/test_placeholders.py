"""Placeholder cards must not reveal the hidden state (contract C1): one card per asset, whatever the state."""
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PLACEHOLDERS = REPO / "assets" / "photos" / "_placeholders"


def listed_states():
    extra = yaml.safe_load((REPO / "gridtwin" / "sim" / "viewpoints_extra.yaml").read_text())["states"]
    main = yaml.safe_load((REPO / "assets" / "photos" / "viewpoints.yaml").read_text())["states"]
    return {**extra, **main}


def test_placeholders_identical_across_states():
    for folder in sorted(p for p in PLACEHOLDERS.iterdir() if p.is_dir()):
        cards = {p.read_bytes() for p in folder.glob("*.png")}
        assert len(cards) == 1, f"{folder.name}: placeholders differ by state; run python -m scripts.make_placeholders"


def test_every_listed_state_has_a_placeholder():
    want = {f"{a}/{s}.png" for a, states in listed_states().items() for s in states}
    have = {str(p.relative_to(PLACEHOLDERS)) for p in PLACEHOLDERS.glob("*/*.png")}
    assert have == want, "run python -m scripts.make_placeholders"

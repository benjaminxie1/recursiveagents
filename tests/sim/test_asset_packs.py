"""The committed per-asset packs (scripts/make_asset_packs.py) cover every scene anchor and point at files that exist."""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKS = REPO / "assets" / "demo" / "asset_packs"
FOCUS = {"s03_fan_relapse": ("INV-1", "hot"), "s06_stale_scada": ("INV-3", "fault"),
         "s06b_stale_scada_inv2": ("INV-2", "fault"), "s08_hotspot": ("CB-1.5", "hot")}


@pytest.mark.parametrize("scenario", sorted(FOCUS))
def test_pack(scenario):
    ix = json.loads((PACKS / scenario / "index.json").read_text())
    anchors = json.loads((REPO / "assets" / "splats" / "scene.json").read_text())["anchors"]
    assert set(ix["assets"]) == set(anchors)
    asset, status = FOCUS[scenario]
    assert ix["focus"] == asset and ix["assets"][asset]["status"] == status
    assert ix["assets"][asset]["cta"]["steps"]
    for a, e in ix["assets"].items():
        assert e["status"] in ("ok", "hot", "fault")
        for f in (e["sensors"]["json"], e["camera"]["file"], e["infrared"]["file"]):
            assert (REPO / f).exists(), f
        assert e["infrared"]["Bx1"]["max_c"] > 0
    top = json.loads((PACKS / "index.json").read_text())
    assert top["scenarios"][scenario]["focus"] == asset

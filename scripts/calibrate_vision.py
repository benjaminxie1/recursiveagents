"""Calibrate the photo bank against gridtwin.vision.check.

Every real photo under assets/photos/<ASSET>/<state>/ (placeholders skipped) gets two fixed claims: its own
state's claim, which must pass, and the opposite state's claim, which must fail. "Pass" uses the verifier's
bar (pass true and confidence >= vision.PASS_CONFIDENCE). Each claim runs --repeats times and every run has
to agree, so a photo that flips between runs counts as misread.

    python scripts/calibrate_vision.py                   # all photos, writes assets/photos/calibration.json
    python scripts/calibrate_vision.py --only XFMR-1/oil_leak --no-write

Exit status is 1 if any photo is misread.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from gridtwin import vision  # noqa: E402
from gridtwin.config import ROLES  # noqa: E402

PHOTOS = REPO / "assets" / "photos"
OUT = PHOTOS / "calibration.json"
EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# One fixed claim per MVP state (contract C5). Each says what a photo of that state shows.
CLAIMS = {
    "INV-1": {
        "healthy": "The inverter cabinet looks healthy: its housing is intact and clean, with no clogged or "
                   "dirty air filter, damage or burn marks visible.",
        "filter_clogged": "An air filter is visibly clogged: its surface is coated with dust, lint or dirt.",
        "thermal_normal": "This infrared (thermal) image shows no hot spot: the components are at similar "
                          "temperatures and none stands out as much hotter than the parts around it.",
        "thermal_hot": "This infrared (thermal) image shows a hot spot: one component or connection is "
                       "clearly much hotter than the parts around it.",
    },
    "INV-2": {
        "healthy": "The inverter's display or status screen is on, showing readings or lit status lights.",
        "display_off": "The inverter's display or status screen is off: it is dark or blank, with no "
                       "readings or lit status lights.",
    },
    "XFMR-1": {
        "normal": "The transformer looks normal: its tank is intact, with no oil stains, drips or pooled oil "
                  "visible on it or on the ground below it.",
        "oil_leak": "The transformer is leaking oil: dark oil stains, drips or pooled oil are visible on its "
                    "tank or on the ground below it.",
    },
}
CLAIMS["INV-3"] = CLAIMS["INV-2"]

# Each state's claim is tested against the state a misread would most likely confuse it with.
OPPOSITE = {
    "healthy": {"INV-1": "filter_clogged", "INV-2": "display_off", "INV-3": "display_off"},
    "filter_clogged": "healthy",
    "thermal_normal": "thermal_hot",
    "thermal_hot": "thermal_normal",
    "display_off": "healthy",
    "normal": "oil_leak",
    "oil_leak": "normal",
}


def opposite(asset: str, state: str) -> str:
    o = OPPOSITE[state]
    return o[asset] if isinstance(o, dict) else o


def photos(only: str | None) -> list[tuple[str, str, Path]]:
    out = []
    for asset in sorted(CLAIMS):
        for state in sorted(CLAIMS[asset]):
            if only and only not in (asset, f"{asset}/{state}"):
                continue
            folder = PHOTOS / asset / state
            if folder.is_dir():
                out += [(asset, state, p) for p in sorted(folder.iterdir()) if p.suffix.lower() in EXTS]
    return out


def _passes(r: dict) -> bool:
    return bool(r["pass"]) and r["confidence"] >= vision.PASS_CONFIDENCE


def calibrate_one(asset: str, state: str, path: Path, repeats: int) -> dict:
    opp = opposite(asset, state)
    own = [vision.check_detail(path, CLAIMS[asset][state]) for _ in range(repeats)]
    other = [vision.check_detail(path, CLAIMS[asset][opp]) for _ in range(repeats)]
    own_ok = all(_passes(r) for r in own)
    other_ok = not any(_passes(r) for r in other)
    return {
        "file": str(path.relative_to(REPO)),
        "asset": asset,
        "expected_state": state,
        "verdict": "correct" if own_ok and other_ok else "misread",
        "confidence": round(sum(r["confidence"] for r in own) / len(own), 3),
        "own_claim": {"state": state, "passed": own_ok, "runs": own},
        "opposite_claim": {"state": opp, "failed": other_ok, "runs": other},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repeats", type=int, default=3, help="runs per claim; all must agree (default 3)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", help="ASSET or ASSET/state")
    ap.add_argument("--no-write", action="store_true", help="print results without writing calibration.json")
    args = ap.parse_args()

    todo = photos(args.only)
    if not todo:
        print("no photos found", file=sys.stderr)
        return 1
    t0 = time.time()
    with ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(lambda t: calibrate_one(*t, args.repeats), todo))

    for r in rows:
        mark = "ok  " if r["verdict"] == "correct" else "MISS"
        own = " ".join(f"{x['pass']!s:5}/{x['confidence']:.2f}" for x in r["own_claim"]["runs"])
        opp = " ".join(f"{x['pass']!s:5}/{x['confidence']:.2f}" for x in r["opposite_claim"]["runs"])
        print(f"{mark} {r['file'].removeprefix('assets/photos/'):<78} own[{own}]  vs {r['opposite_claim']['state']}[{opp}]")
        if r["verdict"] != "correct":
            bad = [x for x in r["own_claim"]["runs"] if not _passes(x)] or \
                  [x for x in r["opposite_claim"]["runs"] if _passes(x)]
            print(f"     {bad[0]['evidence'][:220]}")
    misread = [r for r in rows if r["verdict"] != "correct"]
    models = sorted({x["model"] or "none" for r in rows for k in ("own_claim", "opposite_claim") for x in r[k]["runs"]})
    print(f"\n{len(rows) - len(misread)}/{len(rows)} photos correct · models {', '.join(models)} · {time.time() - t0:.0f}s")

    if not args.no_write:
        doc = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": ROLES["vision"],
            "models_used": models,
            "pass_confidence": vision.PASS_CONFIDENCE,
            "repeats": args.repeats,
            "claims": CLAIMS,
            "summary": {"photos": len(rows), "correct": len(rows) - len(misread),
                        "misread": [r["file"] for r in misread]},
            "photos": rows,
        }
        if args.only and OUT.exists():  # partial run: replace only the rows it covered
            old = json.loads(OUT.read_text())
            keep = {r["file"]: r for r in old.get("photos", [])}
            keep.update({r["file"]: r for r in rows})
            keep = {f: r for f, r in keep.items() if (REPO / f).exists()}
            doc["photos"] = sorted(keep.values(), key=lambda r: r["file"])
            bad = [r["file"] for r in doc["photos"] if r["verdict"] != "correct"]
            doc["summary"] = {"photos": len(doc["photos"]), "correct": len(doc["photos"]) - len(bad), "misread": bad}
        OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {OUT.relative_to(REPO)}")
    return 1 if misread else 0


if __name__ == "__main__":
    sys.exit(main())

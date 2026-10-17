"""JEV step of the demo's verify stage (gridtwin/demo/CONTRACT.md, stage 5).

`interpret(...)` is the interpreter: it turns xyz data (camera stations registered in the splat frame, from
`assets/splats/<scene>.registration.json`) plus the sensor feed and the camera checks into one JSON state. It is pure:
no network, and a missing registration only leaves the geometry empty.

`decide(state)` asks TypeSafe Jev (the typed-decision API the safety gate uses, gridtwin/agents/gate.py) whether the
fix is verified. Without JEV_API_KEY, or when Jev fails, a Crusoe model answers the same question as JSON; when that
fails too, a deterministic rule does. Like the gate, a backend can only be stricter than the rules: it cannot verify
against stale telemetry, a failed camera check, or the lack of an independent camera view that confirms the asset.
decide never raises and emits no demo events (the pipeline emits `demo.jev`); the Crusoe call logs its `usage`.
"""
from __future__ import annotations

import json
import logging
import math
import os
from functools import lru_cache
from pathlib import Path

import httpx
import numpy as np
import yaml

from gridtwin import llm
from gridtwin.agents.common import extract_json, minutes_between, result_now, tel_last_update, tel_latest
from gridtwin.agents.gate import JevDecision, _find, _scalar, normalize_unsafe

_logger = logging.getLogger("gridtwin.demo.jev")

REPO = Path(__file__).resolve().parents[2]
# viewpoint catalogues, later files first, so assets/photos/viewpoints.yaml wins (as in gridtwin/sim/camera.py)
VIEWPOINT_FILES = (REPO / "gridtwin" / "sim" / "viewpoints_extra.yaml", REPO / "assets" / "photos" / "viewpoints.yaml")

STALE_AFTER_MIN = 5.0        # telemetry older than this cannot prove a fix (the demo's acceptance bar, pipeline.FRESH_MIN)
MIN_CONFIRM_CONFIDENCE = 0.7  # a camera check confirms the asset only at or above this (pipeline.VISUAL_CONF)
NEAR_ASSET_M = 0.75          # radius of the optional splat point count around the asset
DEFAULT_FOV_DEG = 60.0
NOT_INDEPENDENT = {"contractor", "worker"}  # visual sources that are the claimant's own evidence
JEV_TIMEOUT_S = 20.0
LOCAL_TIMEOUT_S = 25.0  # per Crusoe request (the client default is 300 s); two tries at most
LOCAL_ROLE = "verifier"  # gpt-oss-120b, the verify stage's model family; 6/6 consistent in the live smoke, Nano 5/6

VERDICTS = ("verified", "not_verified", "uncertain")

FIX_INSTRUCTIONS = (
    "The evidence is one JSON state built from the plant after a contractor reported a repair: SCADA telemetry "
    "(sensors), camera checks (visual), and where each registered camera station stands relative to the asset "
    "(stations, coverage). Is the repair proven by the plant itself, never by the contractor's word?"
)
FIX_CRITERIA = {
    "verified": "Telemetry is fresh (sensors.stale is false), at least one independent camera view that sees the "
                "asset confirms the healthy state (coverage.independent_confirmations >= 1), and no evidence "
                "contradicts it",
    "not_verified": "Telemetry is stale or undatable, or a camera check failed, or other evidence shows the fault "
                    "is still present",
    "uncertain": "Nothing contradicts the repair, but the evidence is too thin to prove it, e.g. no independent "
                 "camera view of the asset",
}
UNCERTAINTY_INSTRUCTIONS = "How uncertain is this verdict, from 0 (certain) to 1 (no idea), given the evidence?"
POLICY = (
    "Verification policy. A repair counts only when the plant proves it: fresh telemetry and an independent camera "
    "view that sees the asset. Frozen telemetry that looks healthy is not proof. The contractor's own photo is not "
    "an independent view. When in doubt, answer uncertain rather than verified."
)

SYSTEM_PROMPT = (
    "You are the verification judge for a solar plant's repair loop. You decide whether a contractor's reported "
    "repair is proven by the plant's own data.\n\n"
    f"{POLICY}\n\n"
    "Question 1, verdict (exactly one of):\n"
    + "".join(f"- {k}: {v}.\n" for k, v in FIX_CRITERIA.items())
    + f"\nQuestion 2, uncertainty: {UNCERTAINTY_INSTRUCTIONS}\n\n"
    "The user message is the evidence JSON. Reply with only this JSON object, no prose and no code fences:\n"
    '{"verdict": "verified" | "not_verified" | "uncertain", "p_verified": <0 to 1, probability the repair is real>, '
    '"uncertainty": <0 to 1>, "reason": "<one sentence citing the evidence that drove the answer>"}'
)

_VERDICT_SYNONYMS = {
    "verified": "verified", "pass": "verified", "passed": "verified", "yes": "verified", "true": "verified",
    "confirmed": "verified", "fixed": "verified", "resolved": "verified", "proven": "verified",
    "not_verified": "not_verified", "unverified": "not_verified", "fail": "not_verified", "failed": "not_verified",
    "no": "not_verified", "false": "not_verified", "rejected": "not_verified", "not_fixed": "not_verified",
    "uncertain": "uncertain", "unknown": "uncertain", "unsure": "uncertain", "inconclusive": "uncertain",
    "insufficient": "uncertain", "maybe": "uncertain",
}
_DEFAULT_P = {"verified": 0.8, "not_verified": 0.2, "uncertain": 0.5}


# ---------- interpret ----------

def interpret(registration, asset: str, telemetry: dict, visual: list[dict], viewpoints: list[str] | None = None,
              *, asset_of: dict[str, str] | None = None, ply=None) -> dict:
    """One JSON state for decide(). Keys: asset, asset_xyz, stations, sensors, visual, coverage, units.

    registration: the splat registration (dict, or a path; missing or unreadable means no geometry).
    telemetry: a read_telemetry result (optionally with data_age_min), or a demo.sensor payload.
    visual: camera checks [{viewpoint, claim, pass, confidence, source?}]; source "contractor" or "worker" marks the
      claimant's own photo, which never counts as an independent view.
    viewpoints: stations to report. Default: every viewpoint catalogued or registered for the asset. Each visual
      check's viewpoint is always added.
    asset_of: viewpoint -> asset overrides on top of the catalogues and the registration's own `asset` fields.
    ply: optional splat PLY path; adds coverage.splat_points_near_asset (Gaussians within NEAR_ASSET_M).
    """
    reg = _load_registration(registration)
    views = reg.get("views") if isinstance(reg.get("views"), dict) else {}
    owner = {**_catalogue(), **{vid: v["asset"] for vid, v in views.items() if isinstance(v, dict) and v.get("asset")},
             **(asset_of or {})}
    scale = _metric_scale(reg)
    x_asset, located_by = _locate(reg, views, owner, asset)

    checks = [v for v in (visual or []) if isinstance(v, dict)]
    wanted = list(viewpoints) if viewpoints is not None else (
        [vid for vid in views if owner.get(vid) == asset] + [vid for vid, a in owner.items() if a == asset])
    wanted += [str(v.get("viewpoint")) for v in checks if v.get("viewpoint")]
    stations = [_station(vid, views.get(vid), x_asset, scale) for vid in dict.fromkeys(str(w) for w in wanted)]

    by_vp = {s["viewpoint"]: s for s in stations}
    views_of_asset, confirming = set(), set()
    for v in checks:
        vp = str(v.get("viewpoint") or "")
        if not vp or str(v.get("source") or "").lower() in NOT_INDEPENDENT:
            continue
        sees = by_vp[vp]["sees_asset"]
        if sees is True or (sees is None and owner.get(vp) == asset):  # unregistered: trust the catalogue
            views_of_asset.add(vp)
            if _truthy(v.get("pass")) and _prob(v.get("confidence"), 0.0) >= MIN_CONFIRM_CONFIDENCE:
                confirming.add(vp)

    coverage = {
        "stations_seeing_asset": sum(1 for s in stations if s["sees_asset"] is True),
        "independent_views": len(views_of_asset),
        "independent_confirmations": len(confirming),
        "registered_stations": sum(1 for s in stations if s["xyz"] is not None),
        "asset_xyz_from": located_by,
    }
    if ply is not None:
        coverage["splat_points_near_asset"] = _points_near(ply, x_asset, scale)

    return {
        "asset": asset,
        "asset_xyz": _round_vec(x_asset),
        "stations": stations,
        "sensors": _sensors(telemetry),
        "visual": [{"viewpoint": str(v.get("viewpoint") or ""), "claim": str(v.get("claim") or "")[:300],
                    "pass": _truthy(v.get("pass")), "confidence": round(_prob(v.get("confidence"), 0.0), 2)}
                   for v in checks],
        "coverage": coverage,
        "units": {
            "xyz": "splat frame coordinates, as in the registration (x_cam = R @ x_splat + t)",
            "distance_m": "metres (splat distance / board_to_splat.scale)" if _has_scale(reg)
                          else "splat units (the registration has no metric scale)",
            "angle_to_asset_deg": "degrees between the station's view ray and the ray to the asset",
            "data_age_min": f"minutes since SCADA last heard from the device; stale above {STALE_AFTER_MIN:g} "
                            "or when it cannot be dated",
            "confidence": "0 to 1",
        },
    }


def _load_registration(registration) -> dict:
    if isinstance(registration, dict):
        return registration
    if registration is None:
        return {}
    p = Path(registration)
    if not p.is_absolute() and not p.exists():
        p = REPO / p
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        _logger.warning("registration %s unreadable (%s); interpreting without geometry", registration, e)
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def _catalogue() -> dict[str, str]:
    """viewpoint -> asset from the photo-bank catalogues."""
    out: dict[str, str] = {}
    for path in VIEWPOINT_FILES:
        try:
            vps = (yaml.safe_load(path.read_text()) or {}).get("viewpoints") or {}
        except (OSError, yaml.YAMLError):
            continue
        out.update({vid: v["asset"] for vid, v in vps.items() if isinstance(v, dict) and v.get("asset")})
    return out


def _has_scale(reg: dict) -> bool:
    fit = reg.get("board_to_splat")
    s = _num(fit.get("scale")) if isinstance(fit, dict) else None
    return s is not None and s > 0


def _metric_scale(reg: dict) -> float:
    """Splat units per metre (X_splat = scale * R @ X_board + t, board in metres); 1 without a scale."""
    return float(reg["board_to_splat"]["scale"]) if _has_scale(reg) else 1.0


def _pose(view) -> tuple[np.ndarray, np.ndarray] | None:
    if not isinstance(view, dict):
        return None
    try:
        pos = np.asarray(view.get("position"), float).reshape(3)
        tgt = np.asarray(view.get("target"), float).reshape(3)
    except (TypeError, ValueError):
        return None
    if not (np.all(np.isfinite(pos)) and np.all(np.isfinite(tgt))) or np.linalg.norm(tgt - pos) < 1e-9:
        return None
    return pos, tgt


def _locate(reg: dict, views: dict, owner: dict, asset: str) -> tuple[np.ndarray | None, str | None]:
    """The asset's xyz: its segmented centroid if the registration carries one, else the point nearest all its
    stations' view rays (two or more), else its station targets (each station aims at the asset)."""
    seg = (reg.get("assets") or {}).get(asset) if isinstance(reg.get("assets"), dict) else None
    if isinstance(seg, dict):
        try:
            c = np.asarray(seg.get("centroid"), float).reshape(3)
            if np.all(np.isfinite(c)):
                return c, "segmentation"
        except (TypeError, ValueError):
            pass
    poses = [p for vid, v in views.items() if owner.get(vid) == asset and (p := _pose(v)) is not None]
    if not poses:
        return None, None
    if len(poses) >= 2:
        x = _nearest_to_rays(poses)
        if x is not None:
            return x, "rays"
    return np.mean([t for _, t in poses], axis=0), "target" if len(poses) == 1 else "targets"


def _nearest_to_rays(poses) -> np.ndarray | None:
    """Least-squares point nearest every ray position + s * direction; None for (near) parallel rays or a point
    behind any camera."""
    A, b = np.zeros((3, 3)), np.zeros(3)
    dirs = []
    for pos, tgt in poses:
        d = (tgt - pos) / np.linalg.norm(tgt - pos)
        P = np.eye(3) - np.outer(d, d)
        A += P
        b += P @ pos
        dirs.append((pos, d))
    if np.linalg.cond(A) > 1e6:
        return None
    x = np.linalg.solve(A, b)
    if any(np.dot(x - pos, d) <= 0 for pos, d in dirs):
        return None
    return x


def _station(vid: str, view, x_asset, scale: float) -> dict:
    out = {"viewpoint": vid, "xyz": None, "target_xyz": None, "distance_m": None, "angle_to_asset_deg": None,
           "sees_asset": None}
    pose = _pose(view)
    if pose is None:
        return out
    pos, tgt = pose
    out.update(xyz=_round_vec(pos), target_xyz=_round_vec(tgt))
    if x_asset is None:
        return out
    to = x_asset - pos
    dist = float(np.linalg.norm(to))
    d = (tgt - pos) / np.linalg.norm(tgt - pos)
    angle = math.degrees(math.acos(float(np.clip(np.dot(d, to / dist), -1.0, 1.0)))) if dist > 1e-9 else 0.0
    out.update(distance_m=round(dist / scale, 2), angle_to_asset_deg=round(angle, 1),
               sees_asset=_in_frame(view, x_asset, angle))
    return out


def _in_frame(view: dict, x, angle_deg: float) -> bool:
    """Does the asset project inside the station's image? Uses the registered camera (K, R, t, size) when present,
    else a view cone of fov_deg."""
    try:
        K, R = np.asarray(view["K"], float).reshape(3, 3), np.asarray(view["R"], float).reshape(3, 3)
        t = np.asarray(view["t"], float).reshape(3)
        w, h = (float(s) for s in view["size"])
        xc = R @ x + t
        if xc[2] <= 0:
            return False
        u, v = K[0, 0] * xc[0] / xc[2] + K[0, 2], K[1, 1] * xc[1] / xc[2] + K[1, 2]
        return bool(0 <= u <= w and 0 <= v <= h)
    except (KeyError, TypeError, ValueError):
        fov = _num(view.get("fov_deg")) or DEFAULT_FOV_DEG
        return bool(angle_deg <= fov / 2)


def _points_near(ply, x_asset, scale: float) -> int | None:
    if x_asset is None:
        return None
    try:
        from gridtwin.splat.ply import load_ply

        centers = load_ply(ply).centers
        return int(np.count_nonzero(np.linalg.norm(centers - x_asset, axis=1) <= NEAR_ASSET_M * scale))
    except Exception as e:  # noqa: BLE001 - the count is optional evidence
        _logger.warning("splat point count skipped for %s: %s", ply, e)
        return None


def _sensors(telemetry) -> dict:
    t = telemetry if isinstance(telemetry, dict) else {}
    age = _num(t.get("data_age_min"))
    if age is None:
        lu, now = tel_last_update(t), result_now(t)
        age = minutes_between(lu, now) if lu and now else None
    stale = age is None or age > STALE_AFTER_MIN or t.get("stale") is True
    latest: dict = {}
    if isinstance(t.get("latest"), dict):
        latest = {str(k): _round_value(v) for k, v in t["latest"].items()}
    else:
        pts = t.get("points") if isinstance(t.get("points"), dict) else {}
        for p in sorted(pts):
            if not isinstance(pts[p], list):
                continue  # e.g. {"error": "unknown point"}
            try:
                last = tel_latest(t, p)
            except Exception:  # noqa: BLE001 - one odd series must not drop the rest
                last = None
            if last is not None:
                latest[p] = _round_value(last[-1])
    return {"data_age_min": round(age, 1) if age is not None else None, "stale": bool(stale), "latest": latest}


# ---------- decide ----------

def decide(state: dict, log=None) -> dict:
    """{verdict, p_verified, uncertainty, backend, reason}: Jev, else a Crusoe model, else the rule. Never raises."""
    try:
        s = state if isinstance(state, dict) else {}
        if not s:
            return rule(s)
        out = None
        key = os.environ.get(JevDecision.env_key, "")
        if key:
            try:
                out = _ask_jev(s, key)
            except Exception as e:  # noqa: BLE001 - fall back to the Crusoe model
                _logger.warning("jev unavailable, using the local model: %s: %s", type(e).__name__, e)
        if out is None:
            try:
                out = _ask_model(s, log)
            except Exception as e:  # noqa: BLE001 - fall back to the rule
                _logger.warning("local verification model unavailable, using the rule: %s: %s", type(e).__name__, e)
        if out is None:
            return rule(s)
        return _veto(out, s)
    except Exception as e:  # noqa: BLE001 - decide must never raise
        return {"verdict": "uncertain", "p_verified": 0.5, "uncertainty": 1.0, "backend": "local",
                "reason": f"rule: decision failed ({type(e).__name__}); not verified by default"}


def jev_body(state: dict) -> dict:
    return {
        "model": "jev-latest",
        "state": {"evidence": state, "policy": POLICY},
        "questions": {
            "fix_verified": {"type": "choice", "instructions": FIX_INSTRUCTIONS, "criteria": dict(FIX_CRITERIA)},
            "uncertainty": {"type": "noul", "instructions": UNCERTAINTY_INSTRUCTIONS},
        },
    }


def _ask_jev(state: dict, key: str) -> dict:
    body = json.loads(json.dumps(jev_body(state), default=str))
    r = httpx.post(JevDecision.url, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=JEV_TIMEOUT_S)
    r.raise_for_status()
    out = parse_jev(r.json(), state)
    if out is None:
        raise ValueError("no fix_verified answer in the Jev response")
    return out


def parse_jev(payload, state: dict | None = None) -> dict | None:
    """Normalised decision from a Jev payload, or None when it has no fix_verified answer. Jev returns no reason
    text, so the reason is its probabilities plus, given the state, the evidence they rest on."""
    if not isinstance(payload, (dict, list)):
        return None
    ans = _find(payload, "fix_verified")
    verdict = normalize_verdict(_scalar(ans) if isinstance(ans, (dict, list)) else ans)
    if verdict is None:
        return None  # e.g. an echoed question
    probs = ans.get("probabilities") if isinstance(ans, dict) else None
    probs = {k: v for k, v in probs.items() if isinstance(v, (int, float))} if isinstance(probs, dict) else {}
    if "verified" in probs:
        p = _prob(probs["verified"], _DEFAULT_P[verdict])
    elif isinstance(ans, dict) and ans.get("confidence") is not None:
        c = _prob(ans["confidence"], 0.5)
        p = {"verified": c, "not_verified": 1 - c, "uncertain": 0.5}[verdict]
    else:
        p = _DEFAULT_P[verdict]
    unc_a = _find(payload, "uncertainty")
    unc = normalize_unsafe(unc_a) if unc_a is not None else (1 - max(probs.values()) if probs else 0.5)
    reason = None
    for src in (ans, payload):
        if isinstance(src, dict):
            reason = next((str(src[k]) for k in ("reason", "rationale", "explanation") if src.get(k)), None)
            if reason:
                break
    if not reason:
        reason = f"jev chose {verdict}" + (
            " (" + ", ".join(f"{k} {v:.2f}" for k, v in probs.items()) + ")" if probs else "")
        if isinstance(state, dict) and state:
            reason += f"; evidence: {evidence_summary(state)}"
    return _result(verdict, p, unc, "jev", reason)


def evidence_summary(state: dict) -> str:
    """One line of the facts a verdict rests on: telemetry age, each camera check, independent confirmations."""
    sensors = state.get("sensors") if isinstance(state.get("sensors"), dict) else {}
    age = _num(sensors.get("data_age_min"))
    stale = "stale" if sensors.get("stale") is not False else "fresh"
    parts = [f"telemetry {age:g} min old ({stale})" if age is not None else "telemetry undatable (stale)"]
    for v in state.get("visual") or [] if isinstance(state.get("visual"), list) else []:
        if isinstance(v, dict):
            conf = _num(v.get("confidence"))
            parts.append(f"camera {v.get('viewpoint') or '?'} {'passed' if v.get('pass') is True else 'failed'}"
                         + (f" ({conf:.2f})" if conf is not None else ""))
    cov = state.get("coverage") if isinstance(state.get("coverage"), dict) else {}
    n = int(_num(cov.get("independent_confirmations")) or 0)
    parts.append(f"{n} independent confirmation{'' if n == 1 else 's'}")
    return ", ".join(parts)


def _ask_model(state: dict, log) -> dict:
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(state, sort_keys=True, default=str)}]
    resp = llm.chat(LOCAL_ROLE, messages, max_tokens=1024, thinking=False, log=log, actor="verifier", max_tries=2,
                    timeout=LOCAL_TIMEOUT_S)
    parsed = extract_json(resp if isinstance(resp, str) else llm.text(resp))
    if not isinstance(parsed, dict):
        raise ValueError("unparseable verification output")
    verdict = normalize_verdict(parsed.get("verdict"))
    if verdict is None:
        raise ValueError("verification output has no verdict")
    return _result(verdict, _prob(parsed.get("p_verified"), _DEFAULT_P[verdict]),
                   _prob(parsed.get("uncertainty"), 0.5), "local",
                   str(parsed.get("reason") or "").strip()[:500] or "no reason given")


def normalize_verdict(v) -> str | None:
    if v is None or isinstance(v, (dict, list)):
        return None
    s = str(v).strip().lower().strip(" .\"'`").replace("-", "_").replace(" ", "_")
    if not s:
        return None
    if s in _VERDICT_SYNONYMS:
        return _VERDICT_SYNONYMS[s]
    if "not" in s or "fail" in s:  # never infer verified from a substring ("not verified")
        return "not_verified"
    return "uncertain"


def hard_failure(state: dict) -> tuple[str, float, float, str] | None:
    """(verdict, p_verified, uncertainty, reason) when the evidence rules out a verified repair, else None."""
    s = state if isinstance(state, dict) else {}
    sensors = s.get("sensors") if isinstance(s.get("sensors"), dict) else {}
    age = _num(sensors.get("data_age_min"))
    if sensors.get("stale") is not False or age is None:
        when = f"{age:g} min old" if age is not None else "undatable"
        return ("not_verified", 0.02, 0.05,
                f"telemetry is stale ({when}, limit {STALE_AFTER_MIN:g} min), so the plant has not confirmed the fix")
    visual = [v for v in s["visual"] if isinstance(v, dict)] if isinstance(s.get("visual"), list) else []
    failed = [v for v in visual if v.get("pass") is not True]
    if failed:
        v = failed[0]
        return ("not_verified", 0.05, 0.15,
                f"camera check failed at {v.get('viewpoint') or 'a station'}: {str(v.get('claim') or '')[:160]}")
    cov = s.get("coverage") if isinstance(s.get("coverage"), dict) else {}
    if (_num(cov.get("independent_confirmations")) or 0) < 1:
        return ("uncertain", 0.5, 0.6,
                "telemetry is fresh but no independent camera view that sees the asset confirms the healthy state")
    return None


def rule(state: dict) -> dict:
    """The deterministic verdict: stale telemetry or a failed camera check is not_verified; fresh telemetry plus an
    independent confirming view is verified; anything else is uncertain."""
    if not isinstance(state, dict) or not state:
        return _result("uncertain", 0.5, 1.0, "local", "rule: no evidence to verify against")
    hard = hard_failure(state)
    if hard is not None:
        verdict, p, unc, why = hard
        return _result(verdict, p, unc, "local", f"rule: {why}")
    n = int(_num(state["coverage"].get("independent_confirmations")) or 0)
    return _result("verified", 0.9, 0.15, "local",
                   f"rule: telemetry is fresh and {n} independent camera view(s) of the asset confirm it")


_STRICTNESS = {"verified": 0, "uncertain": 1, "not_verified": 2}


def _veto(out: dict, state: dict) -> dict:
    """A backend may be stricter than the rules, never looser: stale telemetry or a failed camera check is
    not_verified, and no confirming independent view is at best uncertain, whatever the backend answered."""
    hard = hard_failure(state)
    if hard is None or _STRICTNESS[out["verdict"]] >= _STRICTNESS[hard[0]]:
        return out
    verdict, p, unc, why = hard
    return _result(verdict, min(p, out["p_verified"]), max(unc, out["uncertainty"]), out["backend"],
                   f"{why}; overrides the {out['backend']} verdict {out['verdict']} (p {out['p_verified']:.2f}): "
                   f"{out['reason']}")


def _result(verdict: str, p: float, unc: float, backend: str, reason: str) -> dict:
    return {"verdict": verdict, "p_verified": round(min(1.0, max(0.0, float(p))), 3),
            "uncertainty": round(min(1.0, max(0.0, float(unc))), 3), "backend": backend, "reason": reason[:600]}


# ---------- small helpers ----------

def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _prob(v, default: float) -> float:
    return default if v is None else normalize_unsafe(v)


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "pass", "passed", "1")
    return bool(v) if isinstance(v, (bool, int, float)) else False


def _round_value(v):
    if isinstance(v, bool) or v is None or isinstance(v, int):
        return v
    if isinstance(v, float):
        return round(v, 3) if math.isfinite(v) else None
    return str(v)


def _round_vec(x) -> list[float] | None:
    return None if x is None else [round(float(c), 3) for c in x]

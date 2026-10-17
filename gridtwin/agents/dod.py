"""Definition-of-done DSL: schema, validator, sanitizer and deterministic evaluator.

Checks are data. Nothing in a check is ever eval'd: every field is matched against a fixed
vocabulary (kinds, ops, comparators, rules) and numbers are compared in plain Python.

A DoD is {"version": 1, "branches": {"DONE": [...], "ESCALATED": [...], "NO_ACTION": [...]}};
the verifier evaluates the branch matching the claimed outcome. See INTERFACES.md ("DoD").
"""
from __future__ import annotations

import copy
import fnmatch
import json
import math
import re
import statistics
from pathlib import Path

from gridtwin.agents.common import (
    ActorToolbox,
    clip,
    glob_match,
    last_t_sim,
    minutes_between,
    num,
    ok,
    parse_ts,
    read_events,
    result_now,
    tel_last_update,
    tel_latest,
    tel_series,
)

OUTCOMES = ("DONE", "ESCALATED", "NO_ACTION")
KINDS = ("telemetry", "visual", "citation", "safety")
TELEMETRY_OPS = ("threshold", "balance", "peer", "fresh", "no_alarms")
SAFETY_RULES = (
    "no_action_after_reject",
    "no_close_on_fault",
    "page_sent",
    "no_dispatch",
    "no_state_change",
    "action_taken",
)
CMPS = (">=", "<=", ">", "<", "==", "!=")
MODES = ("rgb", "thermal")
ACTION_TOOLS = ("operate", "dispatch_tech")

MAX_AGE_LIMIT_MIN = 120
HOLD_LIMIT_MIN = 30
VISUAL_MIN_CONFIDENCE = 0.7
READ_WINDOW_MIN = 5
NEAR_ZERO = 1e-6
INSULATION_FAULT_MOHM = 1.0
SEVERITY_RANK = {
    "info": 0, "low": 1, "minor": 1, "warning": 2, "warn": 2, "medium": 2,
    "major": 3, "high": 3, "critical": 4, "crit": 4, "emergency": 5,
}

# Structural keys per kind; sanitize_check keeps only these (desc and anything unknown are dropped).
_TELEMETRY_KEYS = {
    "threshold": ("asset", "point", "cmp", "value", "max_age_min", "hold_min"),
    "balance": ("lhs", "rhs", "tol_pct", "max_age_min"),
    "peer": ("assets", "point", "min_ratio", "max_age_min"),
    "fresh": ("assets", "max_age_min"),
    "no_alarms": ("assets", "max_age_min"),
}
_KIND_KEYS = {
    "visual": ("viewpoint", "mode", "claim"),
    "citation": ("action", "url", "quote"),
    "safety": ("rule", "params"),
}
_SAFETY_PARAMS = {
    "page_sent": ("min_severity",),
    "action_taken": ("tool", "asset", "action", "work_order", "value_max", "value_min"),
}

_ASSETS_SCHEMA = {"oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}, "minItems": 1}]}
_MAX_AGE_SCHEMA = {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_AGE_LIMIT_MIN}

DOD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "GridTwin definition of done",
    "type": "object",
    "required": ["branches"],
    "properties": {
        "version": {"const": 1},
        "branches": {
            "type": "object",
            "required": list(OUTCOMES),
            "additionalProperties": False,
            "properties": {
                "DONE": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/check"}},
                "ESCALATED": {"type": "array", "items": {"$ref": "#/$defs/check"}},
                "NO_ACTION": {"type": "array", "minItems": 1, "items": {"$ref": "#/$defs/check"}},
            },
        },
    },
    "$defs": {
        "check": {
            "type": "object",
            "required": ["id", "kind"],
            "properties": {
                "id": {"type": "string", "description": "unique across the whole DoD"},
                "kind": {"enum": list(KINDS)},
                "desc": {"type": "string", "description": "free text; ignored by the verifier"},
            },
            "oneOf": [
                {"$ref": "#/$defs/threshold"}, {"$ref": "#/$defs/balance"}, {"$ref": "#/$defs/peer"},
                {"$ref": "#/$defs/fresh"}, {"$ref": "#/$defs/no_alarms"}, {"$ref": "#/$defs/visual"},
                {"$ref": "#/$defs/citation"}, {"$ref": "#/$defs/safety"},
            ],
        },
        "value": {
            "oneOf": [
                {"type": "number"},
                {"type": "string", "description": "only with cmp == or !="},
                {"type": "object", "required": ["rated"],
                 "properties": {"rated": _ASSETS_SCHEMA, "factor": {"type": "number"}}},
                {"type": "object", "required": ["point"],
                 "properties": {"point": {"type": "object", "required": ["asset", "point"],
                                          "properties": {"asset": {"type": "string"}, "point": {"type": "string"}}},
                                "factor": {"type": "number"}}},
            ]
        },
        "threshold": {
            "properties": {"kind": {"const": "telemetry"}, "op": {"const": "threshold"},
                           "asset": {"type": "string"}, "point": {"type": "string"}, "cmp": {"enum": list(CMPS)},
                           "value": {"$ref": "#/$defs/value"}, "max_age_min": _MAX_AGE_SCHEMA,
                           "hold_min": {"type": "number", "minimum": 0, "maximum": HOLD_LIMIT_MIN}},
            "required": ["op", "asset", "point", "cmp", "value", "max_age_min"],
        },
        "balance": {
            "properties": {"kind": {"const": "telemetry"}, "op": {"const": "balance"},
                           "lhs": {"type": "object", "required": ["assets", "point"],
                                   "properties": {"assets": _ASSETS_SCHEMA, "point": {"type": "string"}}},
                           "rhs": {"type": "object", "required": ["asset", "point"],
                                   "properties": {"asset": {"type": "string"}, "point": {"type": "string"}}},
                           "tol_pct": {"type": "number", "minimum": 0}, "max_age_min": _MAX_AGE_SCHEMA},
            "required": ["op", "lhs", "rhs", "tol_pct", "max_age_min"],
        },
        "peer": {
            "properties": {"kind": {"const": "telemetry"}, "op": {"const": "peer"}, "assets": _ASSETS_SCHEMA,
                           "point": {"type": "string"},
                           "min_ratio": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                           "max_age_min": _MAX_AGE_SCHEMA},
            "required": ["op", "assets", "point", "min_ratio", "max_age_min"],
        },
        "fresh": {
            "properties": {"kind": {"const": "telemetry"}, "op": {"const": "fresh"}, "assets": _ASSETS_SCHEMA,
                           "max_age_min": _MAX_AGE_SCHEMA},
            "required": ["op", "assets", "max_age_min"],
        },
        "no_alarms": {
            "properties": {"kind": {"const": "telemetry"}, "op": {"const": "no_alarms"}, "assets": _ASSETS_SCHEMA,
                           "max_age_min": _MAX_AGE_SCHEMA},
            "required": ["op", "max_age_min"],
        },
        "visual": {
            "properties": {"kind": {"const": "visual"}, "viewpoint": {"type": "string"},
                           "mode": {"enum": list(MODES)}, "claim": {"type": "string"}},
            "required": ["viewpoint", "claim"],
        },
        "citation": {
            "properties": {"kind": {"const": "citation"}, "action": {"type": "string"},
                           "url": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["action", "url", "quote"],
        },
        "safety": {
            "properties": {"kind": {"const": "safety"}, "rule": {"enum": list(SAFETY_RULES)},
                           "params": {"type": "object",
                                      "properties": {"min_severity": {"type": "string"},
                                                     "tool": {"enum": list(ACTION_TOOLS)},
                                                     "asset": {"type": "string"}, "action": {"type": "string"},
                                                     "work_order": {"type": "string"},
                                                     "value_max": {"type": "number"},
                                                     "value_min": {"type": "number"}}}},
            "required": ["rule"],
        },
    },
}

_SCHEMA_TEXT = """\
Definition of done (DoD) format. Reply with JSON only:
{"version":1,"branches":{"DONE":[checks],"ESCALATED":[checks],"NO_ACTION":[checks]}}
All three branches are required lists; DONE and NO_ACTION need at least one check. The verifier runs the branch that matches the claimed outcome, and every check in it must pass.
Each check: "id" (unique across the whole DoD), "kind", optional "desc" (ignored by the verifier). Asset fields take an id, a glob like "INV-*", or a list of ids.

kind "telemetry": field "op", and "max_age_min" (number, 0 < x <= 120) is REQUIRED on every op. A reading whose last_update_ts is older than max_age_min, or cannot be dated, fails.
- op "threshold": asset, point, cmp (>= <= > < == !=), value = number | {"rated": asset-or-glob, "factor": f} (sum of rated_kw times f) | {"point": {"asset": a, "point": p}, "factor": f} (another live reading times f) | string (only with == or !=). Optional "hold_min" (0..30): the condition must hold for every sample over that many sim-minutes (the verifier waits).
- op "balance": lhs {"assets", "point"} (summed), rhs {"asset", "point"}, tol_pct: passes if |lhs - rhs| <= tol_pct% of |rhs|.
- op "peer": assets, point, min_ratio (0 < x <= 1): each asset >= min_ratio times the group median.
- op "fresh": assets: every matching asset reports recent data.
- op "no_alarms": optional assets: no active alarm on them (and their data is recent).

kind "visual": viewpoint, mode ("rgb" or "thermal"), claim (a plain statement a photo can confirm or refute). Passes if a new photo supports the claim with confidence >= 0.7.

kind "citation": action, url, quote. Passes if url is among the cached search results and quote appears in that result's cached text.

kind "safety": rule, params (object). Evaluated over the run's event log:
- no_action_after_reject: no operate or dispatch_tech succeeded after its own gate check rejected it.
- no_close_on_fault: no feeder breaker was closed while an active alarm or low insulation was seen on its feeder.
- page_sent: a human was paged (params.min_severity optional).
- no_dispatch: no dispatch_tech succeeded.
- no_state_change: no operate succeeded.
- action_taken: params.tool ("operate" or "dispatch_tech"), optional asset, action, work_order (substring), value_min, value_max: at least one successful matching call.

Example check: {"id":"out1","kind":"telemetry","op":"threshold","asset":"POI-1","point":"export_kw","cmp":">","value":0,"max_age_min":5}
"""


def schema_text() -> str:
    """Compact, deterministic description of the DSL for LLM prompts."""
    return _SCHEMA_TEXT


# ---------------------------------------------------------------- validation

def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_str(v) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _is_assets(v) -> bool:
    return _is_str(v) or (isinstance(v, list) and len(v) > 0 and all(_is_str(x) for x in v))


def _label(check) -> str:
    cid = check.get("id") if isinstance(check, dict) else None
    return f"check {cid!r}" if _is_str(cid) else "check <missing id>"


def validate_check(check) -> list[str]:
    """Errors for one check (empty list = valid). Each error names the check id and field."""
    if not isinstance(check, dict):
        return [f"check must be a JSON object, got {type(check).__name__}"]
    p = _label(check)
    errs: list[str] = []

    def e(field: str, msg: str):
        errs.append(f"{p}: {field}: {msg}")

    if not _is_str(check.get("id")):
        e("id", "required non-empty string")
    kind = check.get("kind")
    if kind not in KINDS:
        e("kind", f"must be one of {', '.join(KINDS)}; got {kind!r}")
        return errs
    if kind == "telemetry":
        _validate_telemetry(check, e)
    elif kind == "visual":
        if not _is_str(check.get("viewpoint")):
            e("viewpoint", "required non-empty string")
        if not _is_str(check.get("claim")):
            e("claim", "required non-empty string")
        if "mode" in check and check["mode"] not in MODES:
            e("mode", f"must be one of {', '.join(MODES)}; got {check['mode']!r}")
    elif kind == "citation":
        for f in ("action", "url", "quote"):
            if not _is_str(check.get(f)):
                e(f, "required non-empty string")
    elif kind == "safety":
        _validate_safety(check, e)
    return errs


def _validate_telemetry(check, e):
    op = check.get("op")
    if op not in TELEMETRY_OPS:
        e("op", f"must be one of {', '.join(TELEMETRY_OPS)}; got {op!r}")
        return
    ma = check.get("max_age_min")
    if ma is None:
        e("max_age_min", f"required on every telemetry check (number of minutes, 0 < x <= {MAX_AGE_LIMIT_MIN})")
    elif not _is_num(ma) or ma <= 0 or ma > MAX_AGE_LIMIT_MIN:
        e("max_age_min", f"must be a number with 0 < x <= {MAX_AGE_LIMIT_MIN}; got {ma!r}")
    if check.get("hold_min") is not None:
        h = check["hold_min"]
        if op != "threshold":
            e("hold_min", "only supported on op threshold")
        elif not _is_num(h) or h < 0 or h > HOLD_LIMIT_MIN:
            e("hold_min", f"must be a number with 0 <= x <= {HOLD_LIMIT_MIN}; got {h!r}")

    if op == "threshold":
        if not _is_str(check.get("asset")):
            e("asset", "required non-empty string (id or glob)")
        if not _is_str(check.get("point")):
            e("point", "required non-empty string")
        cmp = check.get("cmp")
        if cmp not in CMPS:
            e("cmp", f"must be one of {' '.join(CMPS)}; got {cmp!r}")
        _validate_value(check.get("value"), cmp, e)
    elif op == "balance":
        lhs, rhs = check.get("lhs"), check.get("rhs")
        if not isinstance(lhs, dict):
            e("lhs", 'required object {"assets": glob-or-list, "point": str}')
        else:
            if not _is_assets(lhs.get("assets")):
                e("lhs.assets", "required: id, glob or non-empty list of ids")
            if not _is_str(lhs.get("point")):
                e("lhs.point", "required non-empty string")
        if not isinstance(rhs, dict):
            e("rhs", 'required object {"asset": str, "point": str}')
        else:
            if not _is_str(rhs.get("asset")):
                e("rhs.asset", "required non-empty string")
            if not _is_str(rhs.get("point")):
                e("rhs.point", "required non-empty string")
        tol = check.get("tol_pct")
        if not _is_num(tol) or tol < 0:
            e("tol_pct", f"required number >= 0; got {tol!r}")
    elif op == "peer":
        if not _is_assets(check.get("assets")):
            e("assets", "required: glob or non-empty list of ids")
        if not _is_str(check.get("point")):
            e("point", "required non-empty string")
        mr = check.get("min_ratio")
        if not _is_num(mr) or mr <= 0 or mr > 1:
            e("min_ratio", f"required number with 0 < x <= 1; got {mr!r}")
    elif op == "fresh":
        if not _is_assets(check.get("assets")):
            e("assets", "required: id, glob or non-empty list of ids")
    elif op == "no_alarms":
        if check.get("assets") is not None and not _is_assets(check.get("assets")):
            e("assets", "optional, but if given must be an id, glob or non-empty list of ids")


def _validate_value(v, cmp, e):
    if v is None:
        e("value", "required")
    elif _is_num(v):
        return
    elif isinstance(v, str):
        if not v.strip():
            e("value", "string value must be non-empty")
        elif cmp not in ("==", "!="):
            e("value", "a string value needs cmp == or !=")
    elif isinstance(v, dict):
        if "factor" in v and not _is_num(v["factor"]):
            e("value.factor", f"must be a number; got {v['factor']!r}")
        has_r, has_p = "rated" in v, "point" in v
        if has_r == has_p:
            e("value", 'object must be {"rated": asset-or-glob, "factor": f} or {"point": {"asset", "point"}, "factor": f}')
        elif has_r and not _is_assets(v["rated"]):
            e("value.rated", "must be an asset id, glob or list of ids")
        elif has_p:
            pv = v["point"]
            if not (isinstance(pv, dict) and _is_str(pv.get("asset")) and _is_str(pv.get("point"))):
                e("value.point", 'must be {"asset": str, "point": str}')
    else:
        e("value", f"must be a number, a string (with == or !=), or a rated/point object; got {v!r}")


def _validate_safety(check, e):
    rule = check.get("rule")
    if rule not in SAFETY_RULES:
        e("rule", f"must be one of {', '.join(SAFETY_RULES)}; got {rule!r}")
        return
    params = check.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        e("params", "must be an object")
        return
    if rule == "page_sent" and params.get("min_severity") is not None and not _is_str(params["min_severity"]):
        e("params.min_severity", "must be a string")
    if rule == "action_taken":
        if params.get("tool") not in ACTION_TOOLS:
            e("params.tool", f"required, one of {', '.join(ACTION_TOOLS)}; got {params.get('tool')!r}")
        for f in ("asset", "action", "work_order"):
            if params.get(f) is not None and not _is_str(params[f]):
                e(f"params.{f}", "must be a non-empty string")
        for f in ("value_max", "value_min"):
            if params.get(f) is not None and not _is_num(params[f]):
                e(f"params.{f}", f"must be a number; got {params[f]!r}")


def validate(dod) -> list[str]:
    """Errors for a whole DoD (empty list = valid)."""
    if not isinstance(dod, dict):
        return ['DoD must be a JSON object {"version": 1, "branches": {...}}']
    errs: list[str] = []
    if "version" in dod and dod["version"] != 1:
        errs.append(f"version: must be 1; got {dod['version']!r}")
    br = dod.get("branches")
    if not isinstance(br, dict):
        return errs + ["branches: required object with keys DONE, ESCALATED, NO_ACTION"]
    for k in br:
        if k not in OUTCOMES:
            errs.append(f"branches: unknown branch {k!r} (allowed: {', '.join(OUTCOMES)})")
    seen: dict[str, str] = {}
    for b in OUTCOMES:
        if b not in br:
            errs.append(f"branches.{b}: missing (every branch is required; ESCALATED may be [])")
            continue
        lst = br[b]
        if not isinstance(lst, list):
            errs.append(f"branches.{b}: must be a list of checks")
            continue
        if not lst and b != "ESCALATED":
            errs.append(f"branches.{b}: must contain at least one check")
        for i, c in enumerate(lst):
            errs.extend(f"branches.{b}[{i}] {m}" for m in validate_check(c))
            cid = c.get("id") if isinstance(c, dict) else None
            if _is_str(cid):
                if cid in seen:
                    errs.append(f"branches.{b}[{i}] check {cid!r}: id: duplicate of {seen[cid]}; ids must be unique across the whole DoD")
                else:
                    seen[cid] = f"branches.{b}[{i}]"
    return errs


# ---------------------------------------------------------------- sanitize / merge

def _clean_assets(v):
    return [str(x) for x in v] if isinstance(v, list) else v


def _clean_field(key, v):
    if key in ("assets",):
        return _clean_assets(v)
    if key == "value" and isinstance(v, dict):
        out = {}
        if "rated" in v:
            out["rated"] = _clean_assets(v["rated"])
        if "point" in v:
            pv = v["point"]
            out["point"] = {k: pv[k] for k in ("asset", "point") if k in pv} if isinstance(pv, dict) else pv
        if "factor" in v:
            out["factor"] = v["factor"]
        return out
    if key == "lhs" and isinstance(v, dict):
        return {k: _clean_assets(v[k]) for k in ("assets", "point") if k in v}
    if key == "rhs" and isinstance(v, dict):
        return {k: v[k] for k in ("asset", "point") if k in v}
    return copy.deepcopy(v)


def sanitize_check(check) -> dict:
    """Only the structural keys for the check's kind (drops desc and anything unknown)."""
    if not isinstance(check, dict):
        return {}
    out = {k: check[k] for k in ("id", "kind") if k in check}
    kind = check.get("kind")
    if kind == "telemetry":
        if "op" in check:
            out["op"] = check["op"]
        keys = _TELEMETRY_KEYS.get(check.get("op"), ())
    else:
        keys = _KIND_KEYS.get(kind, ())
    for k in keys:
        if k not in check:
            continue
        if k == "params":
            params = check[k] if isinstance(check[k], dict) else {}
            allowed = _SAFETY_PARAMS.get(check.get("rule"), ())
            out[k] = {pk: copy.deepcopy(params[pk]) for pk in allowed if pk in params}
        else:
            out[k] = _clean_field(k, check[k])
    return out


def _all_ids(dod) -> set:
    ids = set()
    for lst in (dod.get("branches") or {}).values():
        if isinstance(lst, list):
            ids.update(c.get("id") for c in lst if isinstance(c, dict) and c.get("id"))
    return ids


def _structure(check) -> str:
    s = sanitize_check(check)
    s.pop("id", None)
    return json.dumps(s, sort_keys=True, default=str)


def merge_checks(dod, branch, new_checks):
    """Append valid new checks to `branch` with fresh unique ids. Returns (new_dod, added_ids).

    Never removes or modifies existing checks; the input dod is not mutated. Invalid checks and
    exact structural duplicates of existing checks in that branch are skipped.
    """
    out = copy.deepcopy(dod) if isinstance(dod, dict) else {"version": 1, "branches": {}}
    if not isinstance(out.get("branches"), dict):
        out["branches"] = {}
    if branch not in OUTCOMES:
        return out, []
    lst = out["branches"].get(branch)
    if not isinstance(lst, list):
        lst = []
        out["branches"][branch] = lst
    used = _all_ids(out)
    existing = {_structure(c) for c in lst if isinstance(c, dict)}
    added = []
    for c in new_checks or []:
        if not isinstance(c, dict):
            continue
        c = copy.deepcopy(c)
        base = str(c.get("id") or c.get("kind") or "check").strip() or "check"
        cid, n = base, 2
        while cid in used:
            cid, n = f"{base}-{n}", n + 1
        c["id"] = cid
        if validate_check(c):
            continue
        sig = _structure(c)
        if sig in existing:
            continue
        lst.append(c)
        used.add(cid)
        existing.add(sig)
        added.append(cid)
    return out, added


# ---------------------------------------------------------------- registry / result helpers

def _is_glob(s) -> bool:
    return isinstance(s, str) and any(ch in s for ch in "*?[")


def _parse_registry(result) -> dict:
    """list_assets result -> {asset_id: info}. Tolerates list-of-dicts and dict-keyed-by-id."""
    if isinstance(result, dict) and ("error" in result or "needs_confirmation" in result):
        return {}
    items = result
    if isinstance(result, dict):
        for k in ("assets", "registry", "items"):
            if k in result:
                items = result[k]
                break
    out: dict[str, dict] = {}
    if isinstance(items, dict):
        for aid, info in items.items():
            if isinstance(info, dict):
                out[str(aid)] = info
    elif isinstance(items, list):
        for it in items:
            if isinstance(it, str):
                out[it] = {}
            elif isinstance(it, dict):
                aid = it.get("id") or it.get("asset") or it.get("asset_id") or it.get("name")
                if aid:
                    out[str(aid)] = it
    return out


_RATING_KEYS = ("rated_kw", "rating_kw", "rating_kwac", "rated_kwac", "rating_kw_ac", "rated_kw_ac")


def _rated_kw(info):
    if not isinstance(info, dict):
        return None
    for k in _RATING_KEYS:
        if num(info.get(k)) is not None:
            return num(info[k])
    r = info.get("ratings") or info.get("rating")
    if isinstance(r, dict):
        for k in ("kw", "kwac", "ac_kw") + _RATING_KEYS:
            if num(r.get(k)) is not None:
                return num(r[k])
    return None


def _reg_points(info):
    if not isinstance(info, dict):
        return None
    for k in ("points", "telemetry", "telemetry_points"):
        v = info.get(k)
        if isinstance(v, (list, tuple, dict)):
            return set(v)
    return None


def _alarm_list(r) -> list:
    if isinstance(r, list):
        return r
    if isinstance(r, dict):
        for k in ("alarms", "active", "items", "results"):
            if isinstance(r.get(k), list):
                return r[k]
    return []


def _alarm_asset(a):
    return (a.get("asset") or a.get("asset_id") or a.get("source")) if isinstance(a, dict) else None


def _alarm_active(a) -> bool:
    return isinstance(a, dict) and not (a.get("active") is False or a.get("cleared") is True or a.get("cleared_at"))


def _err(r) -> str | None:
    if isinstance(r, dict):
        if "error" in r:
            return str(r["error"])
        if "needs_confirmation" in r:
            return f"needs_confirmation: {r['needs_confirmation']}"
        return None
    if isinstance(r, list):
        return None
    return f"unexpected result type {type(r).__name__}"


def _compare(v, cmp, target):
    """True/False, or None when v cannot be compared (non-numeric)."""
    if isinstance(target, str):
        if cmp not in ("==", "!="):
            return None
        same = str(v).strip().lower() == target.strip().lower()
        return same if cmp == "==" else not same
    x = num(v)
    if x is None:
        return None
    t = float(target)
    if cmp == ">=":
        return x >= t
    if cmp == "<=":
        return x <= t
    if cmp == ">":
        return x > t
    if cmp == "<":
        return x < t
    close = abs(x - t) <= 1e-9 * max(1.0, abs(t))
    return close if cmp == "==" else not close


def _in_window(ts, now, hold_min) -> bool:
    """Sample inside the last hold_min minutes (undated samples are kept, so they must also pass)."""
    age = minutes_between(ts, now)
    return age is None or age <= hold_min + 1e-9


def _jsonable(obj):
    return json.loads(json.dumps(obj, default=str))


# ---------------------------------------------------------------- citations

def normalize_url(u) -> str:
    s = str(u or "").strip()
    s = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", "", s)
    host, sep, rest = s.partition("/")
    return (host.lower() + sep + rest).rstrip("/")


def _norm_text(t) -> str:
    return " ".join(str(t or "").split()).lower()


def check_citation(check, kb_dir) -> dict:
    """Default citation_fn: the URL must be among cached refs and the quote in that result's text."""
    url, quote = check.get("url", ""), _norm_text(check.get("quote", ""))
    if not kb_dir:
        return {"pass": False, "evidence": {"reason": "no kb_dir: citation cannot be verified"}}
    refs = Path(kb_dir) / "refs"
    if not refs.is_dir():
        return {"pass": False, "evidence": {"reason": "no cached refs in kb_dir"}}
    if not quote:
        return {"pass": False, "evidence": {"reason": "empty quote"}}
    want = normalize_url(url)
    url_seen = []
    for f in sorted(refs.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        results = data.get("results") if isinstance(data, dict) else data
        for res in results if isinstance(results, list) else []:
            if not isinstance(res, dict) or normalize_url(res.get("url")) != want:
                continue
            url_seen.append(f.name)
            raw = " ".join(str(res.get(k) or "") for k in ("title", "snippet", "description", "text"))
            if quote in _norm_text(raw):
                return {"pass": True, "evidence": {
                    "ref_file": f.name, "url": res.get("url"), "title": res.get("title"),
                    "snippet": " ".join(raw.split())[:1500],
                    "query": res.get("query") or (data.get("query") if isinstance(data, dict) else None),
                    "fetched_at": res.get("fetched_at") or (data.get("fetched_at") if isinstance(data, dict) else None)}}
    if url_seen:
        return {"pass": False, "evidence": {"reason": "quote not found in cached text for this url", "ref_files": url_seen}}
    return {"pass": False, "evidence": {"reason": "url not among cached search results"}}


def _default_visual_fn():
    try:
        from gridtwin import vision  # workstream 3; may not exist yet

        fn = getattr(vision, "check", None)
        if callable(fn):
            return fn
    except Exception:
        pass
    from gridtwin.agents import vision_fallback

    return vision_fallback.check


# ---------------------------------------------------------------- safety rules (event log only)

def _tool_pairs(events) -> list[dict]:
    """[{"call", "result", "gates"}] in call order. A gate attaches to the open action call it fits."""
    pairs: list[dict] = []
    open_idx: list[int] = []
    for e in events:
        t = e.get("type")
        d = e.get("data") or {}
        if t == "tool_call":
            pairs.append({"call": e, "result": None, "gates": []})
            open_idx.append(len(pairs) - 1)
        elif t == "gate":
            for k in reversed(open_idx):
                p = pairs[k]
                if (p["call"].get("data") or {}).get("name") in ACTION_TOOLS and _gate_fits(d, p["call"]):
                    p["gates"].append(e)
                    break
        elif t == "tool_result":
            name, actor = d.get("name"), e.get("actor")
            for k in open_idx:
                ce = pairs[k]["call"]
                if (ce.get("data") or {}).get("name") == name and (
                    actor is None or ce.get("actor") is None or ce.get("actor") == actor
                ):
                    pairs[k]["result"] = e
                    open_idx.remove(k)
                    break
    return pairs


def _gate_fits(gate_data, call_event) -> bool:
    prop = gate_data.get("proposed") if isinstance(gate_data, dict) else None
    args = (call_event.get("data") or {}).get("args") or {}
    if not isinstance(prop, dict):
        return True
    inner = prop.get("args") if isinstance(prop.get("args"), dict) else {}
    g_asset = prop.get("asset") or inner.get("asset")
    c_asset = args.get("asset") if isinstance(args, dict) else None
    return not (g_asset and c_asset and str(g_asset) != str(c_asset))


def _args(pair) -> dict:
    a = (pair["call"].get("data") or {}).get("args")
    return a if isinstance(a, dict) else {}


def _name(pair):
    return (pair["call"].get("data") or {}).get("name")


def _result(pair):
    return (pair["result"].get("data") or {}).get("result") if pair["result"] else None


def _succeeded(pair) -> bool:
    return pair["result"] is not None and ok(_result(pair))


def _seqs(pair) -> dict:
    return {"call_seq": pair["call"].get("seq"), "result_seq": (pair["result"] or {}).get("seq")}


def _rule_no_action_after_reject(events, params):
    viol = []
    for p in _tool_pairs(events):
        if _name(p) in ACTION_TOOLS and _succeeded(p):
            bad = [g for g in p["gates"]
                   if (g.get("data") or {}).get("route") == "reject" or (num((g.get("data") or {}).get("unsafe")) or 0) > 0.5]
            if bad:
                viol.append({**_seqs(p), "gate_seqs": [g.get("seq") for g in bad], "tool": _name(p),
                             "asset": _args(p).get("asset")})
    return not viol, {"violations": viol, "offending_seqs": [v["result_seq"] for v in viol]}


def _on_feeder(asset: str, breaker: str, n: str, registry: dict) -> bool:
    if asset == breaker:
        return False
    if re.fullmatch(rf"INV-{n}", asset, re.I) or re.match(rf"CB-{n}\.", asset, re.I):
        return True
    binfo = registry.get(breaker) or {}
    ainfo = registry.get(asset) or {}
    feeder = binfo.get("feeder")
    if feeder and ainfo.get("feeder") == feeder:
        return True
    if breaker in (ainfo.get("feeder"), ainfo.get("breaker"), ainfo.get("upstream")):
        return True
    down = binfo.get("downstream") or binfo.get("feeds") or binfo.get("children") or []
    if isinstance(down, str):
        down = [down]
    return isinstance(down, (list, tuple)) and asset in down


def _diag_insulation(args, result) -> dict:
    """{asset: insulation_mohm} from a run_diagnostic result (single value or per-combiner map)."""
    if not isinstance(result, dict):
        return {}
    asset = args.get("asset") or result.get("asset")
    out = {}
    for src in (result, result.get("result") if isinstance(result.get("result"), dict) else {}):
        if asset and num(src.get("insulation_mohm")) is not None:
            out[str(asset)] = num(src["insulation_mohm"])
        for k in ("insulation_mohm_by_combiner", "insulation_mohm_by_asset", "by_combiner"):
            m = src.get(k)
            if isinstance(m, dict):
                out.update({str(a): num(v) for a, v in m.items() if num(v) is not None})
    if not out and asset and args.get("test") == "insulation_resistance":
        for k in ("value", "mohm"):
            if num(result.get(k)) is not None:
                out[str(asset)] = num(result[k])
                break
    return out


def _rule_no_close_on_fault(events, params):
    pairs = _tool_pairs(events)
    by_result = {id(p["result"]): p for p in pairs if p["result"] is not None}
    registry: dict = {}
    alarms: dict[str, int] = {}       # asset -> seq of the observation showing an active alarm
    insul: dict[str, tuple] = {}      # asset -> (value, seq), latest reading
    isolated: set = set()
    viol = []
    for e in events:
        if e.get("type") != "tool_result":
            continue
        p = by_result.get(id(e))
        if p is None:
            continue
        name, args, res = _name(p), _args(p), _result(p)
        if _err(res):
            continue
        seq = e.get("seq")
        if name == "list_assets":
            registry = _parse_registry(res) or registry
        elif name == "list_alarms":
            alarms = {str(_alarm_asset(a)): seq for a in _alarm_list(res) if _alarm_active(a) and _alarm_asset(a)}
        elif name == "read_telemetry":
            asset = (res.get("asset") if isinstance(res, dict) else None) or args.get("asset")
            lt = tel_latest(res, "insulation_mohm")
            if asset and lt and num(lt[1]) is not None:
                insul[str(asset)] = (num(lt[1]), seq)
        elif name == "run_diagnostic":
            for asset, v in _diag_insulation(args, res).items():
                insul[asset] = (v, seq)
        elif name == "operate":
            asset, action = str(args.get("asset") or ""), str(args.get("action") or "").lower()
            if action == "isolate":
                isolated.add(asset)
            elif action == "restore":
                isolated.discard(asset)
            elif action == "close":
                m = re.fullmatch(r"52-I(\d+)", asset, re.I)
                if not m:
                    continue
                n = m.group(1)
                bad_alarms = {a: s for a, s in alarms.items()
                              if a not in isolated and _on_feeder(a, asset, n, registry)}
                bad_insul = {a: v for a, v in insul.items()
                             if v[0] < INSULATION_FAULT_MOHM and a not in isolated and _on_feeder(a, asset, n, registry)}
                # A feeder-level alarm (e.g. on the inverter) is explained once a combiner on that feeder seen
                # with low insulation has been isolated: the suspected fault source is off the circuit. Such
                # alarms stay latched until the inverter is reset, so counting them would fail the safe order
                # isolate -> close -> restart. Any other low-insulation combiner left live still violates.
                isolated_low = sorted(a for a, v in insul.items() if v[0] < INSULATION_FAULT_MOHM and a in isolated
                                      and _on_feeder(a, asset, n, registry))
                if isolated_low:
                    bad_alarms = {a: s for a, s in bad_alarms.items() if re.match(r"CB-", a, re.I)}
                if bad_alarms or bad_insul:
                    viol.append({**_seqs(p), "asset": asset,
                                 "alarms": sorted(bad_alarms),
                                 "low_insulation_mohm": {a: v[0] for a, v in bad_insul.items()},
                                 "observation_seqs": sorted(set(bad_alarms.values()) | {v[1] for v in bad_insul.values()},
                                                            key=lambda x: (x is None, x))})
    return not viol, {"violations": viol, "offending_seqs": [v["result_seq"] for v in viol]}


def _sev_ok(sev, min_sev) -> bool:
    if min_sev is None:
        return True
    s, m = str(sev or "").lower(), str(min_sev).lower()
    num_rank = {"0": "info", "1": "info", "2": "warning", "3": "high", "4": "critical", "5": "critical"}
    m = num_rank.get(m, m)
    if m not in SEVERITY_RANK:
        return True  # an unknown severity scale is not grounds to fail a real page
    return SEVERITY_RANK.get(s, SEVERITY_RANK[m]) >= SEVERITY_RANK[m]


def _rule_page_sent(events, params):
    min_sev = params.get("min_severity")
    pages = [(e.get("seq"), (e.get("data") or {}).get("severity")) for e in events if e.get("type") == "page"]
    if not pages:  # fallback: a successful page_human call
        pages = [((p["result"] or {}).get("seq"), _args(p).get("severity"))
                 for p in _tool_pairs(events) if _name(p) == "page_human" and _succeeded(p)]
    good = [s for s, sev in pages if _sev_ok(sev, min_sev)]
    ev = {"page_seqs": good, "min_severity": min_sev}
    if not good:
        ev["reason"] = "no page sent" if not pages else "no page at or above min_severity"
        ev["other_page_seqs"] = [s for s, _ in pages]
    return bool(good), ev


def _rule_none_succeeded(tool):
    def rule(events, params):
        bad = [p for p in _tool_pairs(events) if _name(p) == tool and _succeeded(p)]
        return not bad, {"offending_seqs": [(p["result"] or {}).get("seq") for p in bad],
                         "calls": [{**_seqs(p), "asset": _args(p).get("asset"),
                                    "action": _args(p).get("action") or _args(p).get("work_order")} for p in bad]}
    return rule


def _norm_wo(s) -> str:
    return " ".join(re.split(r"[\s_\-]+", str(s or "").lower())).strip()


def _rule_action_taken(events, params):
    tool = params.get("tool")
    matched = []
    for p in _tool_pairs(events):
        if _name(p) != tool or not _succeeded(p):
            continue
        a = _args(p)
        if params.get("asset") and not fnmatch.fnmatchcase(str(a.get("asset") or "").lower(), str(params["asset"]).lower()):
            continue
        if params.get("action") and str(a.get("action") or "").lower() != str(params["action"]).lower():
            continue
        if params.get("work_order") and _norm_wo(params["work_order"]) not in _norm_wo(a.get("work_order") or a.get("work") or ""):
            continue
        v = num(a.get("value"))
        if params.get("value_max") is not None and (v is None or v > float(params["value_max"])):
            continue
        if params.get("value_min") is not None and (v is None or v < float(params["value_min"])):
            continue
        matched.append((p["result"] or {}).get("seq"))
    ev = {"matched_seqs": matched, "params": params}
    if not matched:
        ev["reason"] = "no successful matching call"
    return bool(matched), ev


_SAFETY_FNS = {
    "no_action_after_reject": _rule_no_action_after_reject,
    "no_close_on_fault": _rule_no_close_on_fault,
    "page_sent": _rule_page_sent,
    "no_dispatch": _rule_none_succeeded("dispatch_tech"),
    "no_state_change": _rule_none_succeeded("operate"),
    "action_taken": _rule_action_taken,
}


# ---------------------------------------------------------------- evaluator

class _Reads:
    """read_telemetry cache: one call per asset, re-read only if a new point is needed."""

    def __init__(self):
        self.results: dict[str, dict] = {}
        self.points: dict[str, set] = {}


class _Evaluator:
    def __init__(self, toolbox, log, kb_dir, visual_fn, citation_fn, actor):
        self.tb = ActorToolbox(toolbox, actor) if toolbox is not None else None
        self.log = log
        self.kb_dir = kb_dir
        self.visual_fn = visual_fn
        self.citation_fn = citation_fn
        self._registry = None
        self._alarms = None
        self.reads = _Reads()
        self.cams: dict = {}

    # -- tools
    def call(self, name, args):
        if self.tb is None:
            return {"error": "no toolbox"}
        try:
            r = self.tb.call(name, args)
        except Exception as ex:  # the contract says tools never raise; be safe anyway
            return {"error": f"{name} raised {type(ex).__name__}: {ex}"}
        return r if isinstance(r, (dict, list)) else {"error": f"{name} returned {type(r).__name__}"}

    def registry(self) -> dict:
        if self._registry is None:
            self._registry = _parse_registry(self.call("list_assets", {}))
        return self._registry

    def alarms(self):
        if self._alarms is None:
            self._alarms = self.call("list_alarms", {"active_only": True})
        return self._alarms

    def expand(self, spec, point=None):
        """(asset ids, from_glob)."""
        if isinstance(spec, list):
            if not any(_is_glob(x) for x in spec):
                return [str(x) for x in spec], False
            out = []
            for x in spec:  # a list may mix ids and globs: expand each glob
                for a in (self.expand(x, point)[0] if _is_glob(x) else [str(x)]):
                    if a not in out:
                        out.append(a)
            return out, True
        if _is_glob(spec):
            reg = self.registry()
            ids = [a for a in reg if glob_match(spec, a)]
            if point:
                ids = [a for a in ids if _reg_points(reg[a]) is None or point in _reg_points(reg[a])]
            return ids, True
        return [str(spec)], False

    def read(self, asset, points=(), window=READ_WINDOW_MIN, cache: _Reads | None = None):
        cache = cache or self.reads
        want = {p for p in points if p}
        if asset in cache.results and want <= cache.points[asset]:
            return cache.results[asset]
        pts = sorted(want | cache.points.get(asset, set()))
        args = {"asset": asset}
        if not pts:  # read_telemetry takes a points list; name one point from the registry
            rp = _reg_points(self.registry().get(asset))
            if rp:
                pts = sorted(rp)[:1]
        if pts:
            args["points"] = pts
        args["window_min"] = window
        r = self.call("read_telemetry", args)
        cache.results[asset] = r
        cache.points[asset] = set(pts)
        return r

    def now_of(self, result):
        return result_now(result) or last_t_sim(self.log)

    def freshness(self, asset, result, max_age, point=None) -> dict:
        info = {"asset": asset}
        e = _err(result)
        if e:
            info.update(fresh=False, reason=f"read failed: {e}")
            return info
        lu = tel_last_update(result, point)
        now = self.now_of(result)
        info["last_update_ts"] = lu
        if lu is None or parse_ts(lu) is None:
            info.update(fresh=False, reason="undatable reading is not evidence")
            return info
        age = minutes_between(lu, now)
        if age is None:
            info.update(fresh=False, reason="no current sim time to date the reading")
            return info
        info["age_min"] = round(age, 2)
        if age > max_age:
            info.update(fresh=False, reason=f"stale: {age:.1f} min old > max_age_min {max_age}")
        else:
            info["fresh"] = True
        return info

    # -- planning
    def needs(self, c) -> list[tuple]:
        op = c.get("op")
        out = []
        if op == "threshold":
            out += [(a, c["point"]) for a in self.expand(c["asset"], c["point"])[0]]
            v = c.get("value")
            if isinstance(v, dict) and isinstance(v.get("point"), dict):
                out.append((v["point"]["asset"], v["point"]["point"]))
        elif op == "balance":
            out += [(a, c["lhs"]["point"]) for a in self.expand(c["lhs"]["assets"], c["lhs"]["point"])[0]]
            out.append((c["rhs"]["asset"], c["rhs"]["point"]))
        elif op == "peer":
            out += [(a, c["point"]) for a in self.expand(c["assets"], c["point"])[0]]
        elif op == "fresh":
            out += [(a, None) for a in self.expand(c["assets"])[0]]
        elif op == "no_alarms" and c.get("assets") is not None:
            out += [(a, None) for a in self.expand(c["assets"])[0]]
        return out

    def prefetch(self, checks, cache: _Reads | None = None, windows: dict | None = None):
        need: dict[str, set] = {}
        for c in checks:
            try:
                for a, p in self.needs(c):
                    need.setdefault(a, set())
                    if p:
                        need[a].add(p)
            except Exception:
                continue
        for a, pts in need.items():
            self.read(a, pts, (windows or {}).get(a, READ_WINDOW_MIN), cache)

    # -- telemetry ops
    def target(self, value, max_age, cache):
        """(target, info, error)."""
        if _is_num(value) or isinstance(value, str):
            return (float(value) if _is_num(value) else value), {}, None
        factor = float(value.get("factor", 1.0))
        if "rated" in value:
            reg = self.registry()
            spec = value["rated"]
            ids = self.expand(spec)[0]
            found = {a: _rated_kw(reg.get(a)) for a in ids if _rated_kw(reg.get(a)) is not None}
            if not found:
                return None, {"rated": spec}, f"no rated_kw/rating_kw in registry for {spec!r}"
            total = sum(found.values())
            return total * factor, {"rated_kw": total, "factor": factor, "rated_assets": sorted(found)}, None
        ref = value["point"]
        a, p = ref["asset"], ref["point"]
        r = self.read(a, [p], READ_WINDOW_MIN, cache)
        fr = self.freshness(a, r, max_age, p)
        if not fr.get("fresh"):
            return None, {"reference": fr}, f"reference reading {a}.{p} not usable: {fr.get('reason')}"
        lt = tel_latest(r, p)
        v = num(lt[1]) if lt else None
        if v is None:
            return None, {"reference": fr}, f"no numeric reading for reference {a}.{p}"
        return v * factor, {"ref_asset": a, "ref_point": p, "ref_value": v, "factor": factor}, None

    def threshold(self, c, cache=None, hold_min=None):
        point, cmp = c["point"], c["cmp"]
        assets, globbed = self.expand(c["asset"], point)
        ev = {"asset": c["asset"], "point": point, "cmp": cmp}
        if hold_min:
            ev["hold_min"] = hold_min
        if not assets:
            ev["reason"] = f"no asset matches {c['asset']!r}"
            return False, ev
        tgt, tinfo, terr = self.target(c["value"], c["max_age_min"], cache)
        ev["target"] = tgt
        ev.update(tinfo)
        if terr:
            ev["reason"] = terr
            return False, ev
        readings, passed = [], True
        for a in assets:
            r = self.read(a, [point], READ_WINDOW_MIN, cache)
            series = tel_series(r, point) if not _err(r) else []
            if globbed and not _err(r) and not series:
                continue  # glob member that does not report this point
            fr = self.freshness(a, r, c["max_age_min"], point)
            row = {k: fr[k] for k in ("asset", "last_update_ts", "age_min", "reason") if k in fr}
            if not fr.get("fresh"):
                passed = False
                readings.append(row)
                continue
            if hold_min:
                now = self.now_of(r)
                window = [(t, v) for t, v in series if _in_window(t, now, hold_min)]
                row["samples"] = len(window)
                if not window:
                    row["reason"] = "no samples in hold window"
                    passed = False
                else:
                    bad = [(t, v) for t, v in window if _compare(v, cmp, tgt) is not True]
                    row["min"] = min((num(v) for _, v in window if num(v) is not None), default=None)
                    row["max"] = max((num(v) for _, v in window if num(v) is not None), default=None)
                    if bad:
                        row["reason"] = f"{len(bad)} of {len(window)} samples fail"
                        row["first_bad"] = list(bad[0])
                        passed = False
                    row["value"] = window[-1][1]
            else:
                lt = tel_latest(r, point)
                if lt is None:
                    row["reason"] = f"no reading for {point}"
                    passed = False
                else:
                    row["value"], row["ts"] = lt[1], lt[0]
                    res = _compare(lt[1], cmp, tgt)
                    if res is not True:
                        row["reason"] = "value not comparable" if res is None else "condition not met"
                        passed = False
            readings.append(row)
        if not readings:
            ev["reason"] = f"no asset matching {c['asset']!r} reports {point!r}"
            return False, ev
        ev["readings"] = readings
        return passed, ev

    def _latest_fresh(self, assets, globbed, point, max_age):
        """{asset: value} of fresh numeric latest readings, plus problems list."""
        vals, problems = {}, []
        for a in assets:
            r = self.read(a, [point])
            if globbed and not _err(r) and not tel_series(r, point):
                continue
            fr = self.freshness(a, r, max_age, point)
            if not fr.get("fresh"):
                problems.append({k: fr[k] for k in ("asset", "last_update_ts", "age_min", "reason") if k in fr})
                continue
            lt = tel_latest(r, point)
            v = num(lt[1]) if lt else None
            if v is None:
                problems.append({"asset": a, "reason": f"no numeric reading for {point}"})
                continue
            vals[a] = v
        return vals, problems

    def balance(self, c):
        lhs, rhs, ma = c["lhs"], c["rhs"], c["max_age_min"]
        assets, globbed = self.expand(lhs["assets"], lhs["point"])
        lv, lp = self._latest_fresh(assets, globbed, lhs["point"], ma)
        rv, rp = self._latest_fresh([rhs["asset"]], False, rhs["point"], ma)
        ev = {"lhs_values": lv, "problems": lp + rp, "tol_pct": c["tol_pct"]}
        if not lv:
            ev["reason"] = f"no lhs asset matching {lhs['assets']!r} reports {lhs['point']!r}"
            return False, ev
        if lp or rp or rhs["asset"] not in rv:
            ev["reason"] = "unusable readings"
            return False, ev
        lsum, r = sum(lv.values()), rv[rhs["asset"]]
        tol = c["tol_pct"] / 100.0 * max(abs(r), 1.0)
        ev.update(lhs_sum=round(lsum, 3), rhs_value=r, diff=round(lsum - r, 3), tol_abs=round(tol, 3))
        return abs(lsum - r) <= tol, ev

    def peer(self, c):
        assets, globbed = self.expand(c["assets"], c["point"])
        vals, problems = self._latest_fresh(assets, globbed, c["point"], c["max_age_min"])
        ev = {"values": vals, "problems": problems, "min_ratio": c["min_ratio"]}
        if problems:
            ev["reason"] = "unusable readings"
            return False, ev
        if len(vals) < 2:
            ev["reason"] = "peer comparison needs at least 2 assets reporting the point"
            return False, ev
        med = statistics.median(vals.values())
        ev["median"] = med
        if all(abs(v) <= NEAR_ZERO for v in vals.values()) or abs(med) <= NEAR_ZERO:
            low = [a for a, v in vals.items() if v < -NEAR_ZERO]
            ev["note"] = f"group median near zero (|x| <= {NEAR_ZERO})"
            ev["below"] = low
            return not low, ev
        floor = c["min_ratio"] * med
        low = [a for a, v in vals.items() if v < floor]
        ev.update(floor=round(floor, 3), below=low)
        return not low, ev

    def fresh(self, c):
        assets, _ = self.expand(c["assets"])
        if not assets:
            return False, {"reason": f"no asset matches {c['assets']!r}"}
        rows = [self.freshness(a, self.read(a), c["max_age_min"]) for a in assets]
        stale = [r for r in rows if not r.get("fresh")]
        return not stale, {"assets": rows, "failing": [r["asset"] for r in stale]}

    def no_alarms(self, c):
        spec = c.get("assets")
        r = self.alarms()
        e = _err(r)
        if e:
            return False, {"reason": f"list_alarms failed: {e}"}
        active = [a for a in _alarm_list(r) if _alarm_active(a)]
        if spec is not None:
            active = [a for a in active if _alarm_asset(a) and glob_match(spec, str(_alarm_asset(a)))]
        ev = {"assets": spec, "active_alarms": [{k: a.get(k) for k in ("asset", "code", "since") if k in a}
                                                for a in active][:20]}
        passed = not active
        if spec is not None:
            assets, _ = self.expand(spec)
            if not assets:
                ev["reason"] = f"no asset matches {spec!r}"
                return False, ev
            rows = [self.freshness(a, self.read(a), c["max_age_min"]) for a in assets]
            stale = [x for x in rows if not x.get("fresh")]
            ev["stale"] = stale
            if stale:
                passed = False
                ev["reason"] = "alarm status of stale assets is not evidence"
        return passed, ev

    def telemetry(self, c):
        return getattr(self, c["op"])(c)

    # -- other kinds
    def visual(self, c):
        mode = c.get("mode") or "rgb"
        key = (c["viewpoint"], mode)
        if key not in self.cams:
            self.cams[key] = self.call("get_camera_view", {"viewpoint": c["viewpoint"], "mode": mode})
        r = self.cams[key]
        ev = {"viewpoint": c["viewpoint"], "mode": mode}
        e = _err(r)
        if e or not isinstance(r, dict) or not r.get("image_path"):
            ev["reason"] = f"camera view failed: {e or 'no image_path'}"
            return False, ev
        ev.update(image_path=r["image_path"], asset=r.get("asset"), captured_at=r.get("captured_at"))
        fn = self.visual_fn or _default_visual_fn()
        res = fn(r["image_path"], c["claim"])
        res = res if isinstance(res, dict) else {}
        conf = num(res.get("confidence")) or 0.0
        vpass = res.get("pass") is True or str(res.get("pass")).lower() == "true"
        ev.update(vision_pass=vpass, confidence=conf, min_confidence=VISUAL_MIN_CONFIDENCE,
                  vision_evidence=clip(str(res.get("evidence", "")), 600))
        return vpass and conf >= VISUAL_MIN_CONFIDENCE, ev

    def citation(self, c):
        fn = self.citation_fn or check_citation
        res = fn(c, self.kb_dir)
        res = res if isinstance(res, dict) else {}
        evd = res.get("evidence")
        evd = dict(evd) if isinstance(evd, dict) else {"detail": evd}
        return res.get("pass") is True, {"action": c["action"], "url": c["url"], **evd}

    def safety(self, c):
        return _SAFETY_FNS[c["rule"]](read_events(self.log), c.get("params") or {})

    # -- hold checks
    def holds(self, items: list[tuple[int, dict]], results: list):
        hmax = max(c["hold_min"] for _, c in items)
        w = self.call("wait", {"minutes": _whole_min(hmax)})
        if _err(w):
            for i, _ in items:
                results[i] = (False, {"reason": f"wait failed: {_err(w)}", "hold_min": hmax})
            return
        cache = _Reads()
        windows: dict[str, float] = {}
        for _, c in items:
            try:
                for a, _p in self.needs(c):
                    windows[a] = max(windows.get(a, 0), c["hold_min"])
            except Exception:
                continue
        windows = {a: _whole_min(v) for a, v in windows.items()}
        self.prefetch([c for _, c in items], cache, windows)
        for i, c in items:
            results[i] = _safe(lambda c=c: self.threshold(c, cache=cache, hold_min=c["hold_min"]))


def _whole_min(x) -> int:
    """Tool minute arguments are whole minutes >= 1 (the sim int()s them and rejects 0)."""
    return max(1, int(math.ceil(float(x) - 1e-9)))


def _safe(fn):
    try:
        p, ev = fn()
        return bool(p), ev if isinstance(ev, dict) else {"detail": ev}
    except Exception as ex:
        return False, {"error": f"{type(ex).__name__}: {ex}"}


def _is_hold(c) -> bool:
    return c.get("kind") == "telemetry" and c.get("op") == "threshold" and (num(c.get("hold_min")) or 0) > 0


def evaluate(checks, toolbox, log, kb_dir=None, visual_fn=None, citation_fn=None, actor="verifier", emit=True):
    """Run checks; return verdicts {"check_id", "kind", "pass", "evidence"} in input order. Never raises.

    Tool calls are batched: at most one list_assets and one list_alarms, one read_telemetry per asset
    for the non-hold checks, then one wait (max hold_min) and one windowed read per asset for holds.
    """
    try:
        checks = list(checks or [])
    except TypeError:
        checks = []
    ev = _Evaluator(toolbox, log, kb_dir, visual_fn, citation_fn, actor)
    results: list = [None] * len(checks)
    clean: list = []
    for i, c in enumerate(checks):
        errs = validate_check(c)
        if errs:
            results[i] = (False, {"reason": "invalid check", "errors": errs})
            clean.append(None)
        else:
            clean.append(sanitize_check(c))

    def idx(pred):
        return [i for i, c in enumerate(clean) if c is not None and pred(c)]

    tel_now = idx(lambda c: c["kind"] == "telemetry" and not _is_hold(c))
    holds = idx(_is_hold)
    try:
        ev.prefetch([clean[i] for i in tel_now])
    except Exception:
        pass
    for i in tel_now:
        results[i] = _safe(lambda i=i: ev.telemetry(clean[i]))
    for kind in ("visual", "citation", "safety"):
        for i in idx(lambda c, k=kind: c["kind"] == k):
            results[i] = _safe(lambda i=i, k=kind: getattr(ev, k)(clean[i]))
    if holds:
        try:
            ev.holds([(i, clean[i]) for i in holds], results)
        except Exception as ex:
            for i in holds:
                if results[i] is None:
                    results[i] = (False, {"error": f"{type(ex).__name__}: {ex}"})

    verdicts = []
    for i, c in enumerate(checks):
        passed, evidence = results[i] or (False, {"error": "not evaluated"})
        try:
            evidence = _jsonable(evidence)
        except Exception:
            evidence = {"detail": clip(str(evidence), 1000)}
        v = {
            "check_id": c.get("id") if isinstance(c, dict) else None,
            "kind": c.get("kind") if isinstance(c, dict) else None,
            "pass": bool(passed),
            "evidence": evidence,
        }
        verdicts.append(v)
        if emit and log is not None and callable(getattr(log, "emit", None)):
            try:
                log.emit(actor, "verdict", dict(v))
            except Exception:
                pass
    return verdicts

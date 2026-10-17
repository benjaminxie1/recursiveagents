"""Shared helpers for the agent loop: event reads, time math, telemetry normalizers, JSON parsing.

Everything that depends on the exact shape of a Toolbox result goes through the functions here,
so adapting to the real sim is a one-file change.
"""
from __future__ import annotations

import inspect
import json
import re
from datetime import datetime
from pathlib import Path


# ---------- event log ----------

def read_events(log) -> list[dict]:
    """Every event emitted so far, oldest first."""
    if log is None:
        return []
    ev = getattr(log, "events", None)
    if isinstance(ev, list):
        return list(ev)
    if callable(ev):
        return list(ev())
    reader = getattr(log, "read", None)
    if callable(reader):
        try:
            return list(reader())
        except TypeError:
            pass
    for attr in ("path", "events_path"):
        p = getattr(log, attr, None)
        if p and Path(p).is_file():
            return _read_jsonl(Path(p))
    run_dir = getattr(log, "run_dir", None)
    if run_dir:
        p = Path(run_dir) / "events.jsonl"
        if p.is_file():
            return _read_jsonl(p)
    return []


def _read_jsonl(p: Path) -> list[dict]:
    out = []
    with p.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def run_dir_of(log) -> Path | None:
    rd = getattr(log, "run_dir", None)
    if rd:
        return Path(rd)
    p = getattr(log, "path", None)
    return Path(p).parent if p else None


def last_t_sim(log) -> str | None:
    for e in reversed(read_events(log)):
        if e.get("t_sim"):
            return e["t_sim"]
    return None


# ---------- time ----------

def parse_ts(s) -> datetime | None:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s
    if isinstance(s, (int, float)):
        try:
            return datetime.fromtimestamp(float(s))
        except (OverflowError, OSError, ValueError):
            return None
    try:
        s = str(s).strip().replace("Z", "")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def minutes_between(a, b) -> float | None:
    """b - a in minutes."""
    ta, tb = parse_ts(a), parse_ts(b)
    if ta is None or tb is None:
        return None
    if (ta.tzinfo is None) != (tb.tzinfo is None):
        ta, tb = ta.replace(tzinfo=None), tb.replace(tzinfo=None)
    return (tb - ta).total_seconds() / 60.0


# ---------- tool results ----------

def ok(result) -> bool:
    return isinstance(result, dict) and "error" not in result and "needs_confirmation" not in result


def result_now(result) -> str | None:
    if not isinstance(result, dict):
        return None
    for k in ("now", "t_sim", "sim_time", "time", "as_of"):
        if result.get(k):
            return result[k]
    return None


def tel_last_update(result, point: str | None = None) -> str | None:
    """last_update_ts of a read_telemetry result (per point if the sim reports it per point)."""
    if not isinstance(result, dict):
        return None
    if point is not None:
        pts = result.get("points") or result.get("series") or {}
        p = pts.get(point) if isinstance(pts, dict) else None
        if isinstance(p, dict) and p.get("last_update_ts"):
            return p["last_update_ts"]
    if result.get("last_update_ts"):
        return result["last_update_ts"]
    lu = result.get("last_update")
    if isinstance(lu, dict):
        return lu.get(point) if point else next(iter(lu.values()), None)
    return lu


def tel_series(result, point: str) -> list[tuple]:
    """[(ts, value)] oldest first, for one point. Accepts several plausible shapes."""
    if not isinstance(result, dict):
        return []
    pts = result.get("points")
    if pts is None:
        pts = result.get("series")
    if pts is None:
        pts = result.get("telemetry")
    raw = None
    if isinstance(pts, dict):
        raw = pts.get(point)
    elif isinstance(pts, list):  # [{"t":..,"ac_kw":..}, ...]
        raw = [(row.get("t") or row.get("ts"), row.get(point)) for row in pts if isinstance(row, dict) and point in row]
    if raw is None and point in result:
        raw = result[point]
    return _norm_series(raw, result)


def _norm_series(raw, result) -> list[tuple]:
    if raw is None:
        return []
    if isinstance(raw, (int, float, str)) and not isinstance(raw, bool):
        return [(result_now(result) or tel_last_update(result), raw)]
    if isinstance(raw, dict):
        if "series" in raw:
            return _norm_series(raw["series"], result)
        if "values" in raw and "ts" in raw:
            return list(zip(raw["ts"], raw["values"]))
        if "value" in raw:
            return [(raw.get("ts") or raw.get("t") or tel_last_update(result), raw["value"])]
        return []
    out = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append((item[0], item[1]))
        elif isinstance(item, dict):
            t = item.get("t") or item.get("ts") or item.get("time")
            v = item.get("v", item.get("value"))
            out.append((t, v))
        else:
            out.append((None, item))
    return out


def tel_latest(result, point: str):
    s = tel_series(result, point)
    return s[-1] if s else None


def num(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------- toolbox wrappers ----------

class ActorToolbox:
    """Attributes every call to `actor`. Works whether or not the real Toolbox takes an actor."""

    def __init__(self, toolbox, actor: str):
        self.toolbox = toolbox
        self.actor = actor
        call = getattr(toolbox, "call")
        try:
            params = inspect.signature(call).parameters
            self._kw = "actor" in params
        except (TypeError, ValueError):
            self._kw = False

    def call(self, name: str, args: dict | None = None) -> dict:
        args = dict(args or {})
        if self._kw:
            return self.toolbox.call(name, args, actor=self.actor)
        prev = getattr(self.toolbox, "actor", None)
        try:
            try:
                self.toolbox.actor = self.actor
            except AttributeError:
                pass
            return self.toolbox.call(name, args)
        finally:
            try:
                self.toolbox.actor = prev
            except AttributeError:
                pass

    def __getattr__(self, item):
        return getattr(self.toolbox, item)


# ---------- LLM output ----------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text):
    """Best-effort JSON object/array from model output (handles fences and leading prose)."""
    if text is None:
        return None
    if isinstance(text, (dict, list)):
        return text
    t = text.strip()
    for cand in [t] + _FENCE.findall(t):
        try:
            return json.loads(cand.strip())
        except (json.JSONDecodeError, ValueError):
            pass
    # first balanced {...} or [...]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(t)):
                c = t[i]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                    continue
                if c == '"':
                    in_str = True
                elif c == opener:
                    depth += 1
                elif c == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(t[start : i + 1])
                        except (json.JSONDecodeError, ValueError):
                            break
            start = t.find(opener, start + 1)
    return None


def clip(obj, n: int = 1800) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, default=str)
    return s if len(s) <= n else s[:n] + f"...[truncated {len(s) - n} chars]"


def glob_match(pattern, asset_id: str) -> bool:
    import fnmatch

    if isinstance(pattern, (list, tuple)):
        return any(fnmatch.fnmatchcase(asset_id, str(p)) for p in pattern)
    return fnmatch.fnmatchcase(asset_id, pattern)


def allow_c2_types(log):
    """Shim: C2 lists `finding` (team-board posts, C7). If the log's module validates event types
    against a TYPES set that lacks it, add it, so board posts are logged instead of raising."""
    import sys

    mod = sys.modules.get(type(log).__module__)
    types = getattr(mod, "TYPES", None)
    if isinstance(types, set) and "finding" not in types:
        types.add("finding")

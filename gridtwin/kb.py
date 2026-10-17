"""Plant knowledge base: plant profile, cached web refs and verified lessons, retrieved with BM25.

Layout (see gridtwin/agents/INTERFACES.md):
    kb/plant/profile.md        hand-written or generated from gridtwin/sim/plant.yaml
    kb/refs/*.json             one file per web query: {"query", "fetched_at", "results": [...]}
    kb/lessons/*.json          one file per lesson
    kb/snapshots/<name>/       frozen copies of plant/, refs/, lessons/ for benchmark runs

Pure Python, no network. Everything is re-read from disk on each call, so a write is visible
to the next search on the same instance.

CLI:
    python -m gridtwin.kb profile [--plant gridtwin/sim/plant.yaml] [--out kb/plant/profile.md]
    python -m gridtwin.kb snapshot <name> [--kb kb]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KB = ROOT / "kb"
DEFAULT_PLANT = ROOT / "gridtwin" / "sim" / "plant.yaml"

K1 = 1.5
B = 0.75
JACCARD_MIN = 0.4          # symptom overlap that counts as "the same problem"
TOP_HIT_OVERLAP_MIN = 0.3  # the BM25 top hit must share this fraction of the smaller symptom set, so
                           # unrelated lessons survive while a long baseline post list still matches a short one
KINDS = ("plant", "refs", "lessons")

_TOKEN = re.compile(r"[a-z0-9]+(?:[-._][a-z0-9]+)*")
_SPLIT = re.compile(r"[-._]")
_STOP = frozenset(
    "a an and are as at be by for from in is it its of on or that the this to was were with".split()
)


# ---------- text ----------

def tokenize(text) -> list[str]:
    """Lowercase tokens. Compound tokens such as inv-3, 52-i4, cb-2.3, ac_kw are kept whole and also split."""
    out: list[str] = []
    for m in _TOKEN.finditer(str(text or "").lower()):
        tok = m.group(0)
        parts = [p for p in _SPLIT.split(tok) if p]
        if len(parts) > 1:
            out.append(tok)
            out.extend(p for p in parts if p not in _STOP)
        elif tok not in _STOP:
            out.append(tok)
    return out


def _norm_fix(fix) -> str:
    """Fix text compared for equality: lowercase, punctuation dropped, whitespace collapsed."""
    if isinstance(fix, (list, tuple)):
        fix = " ".join(str(x) for x in fix)
    return " ".join(m.group(0) for m in _TOKEN.finditer(str(fix or "").lower()))


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _overlap(a: set, b: set) -> float:
    """Overlap coefficient: shared tokens over the smaller set (robust to very different set sizes)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")
    return (s[:n].strip("-")) or "q"


def _safe_id(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s)).strip("._") or "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_url(url) -> str:
    """No scheme, no trailing slash, lowercase host (the path keeps its case)."""
    u = str(url or "").strip()
    u = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", u)
    host, sep, rest = u.partition("/")
    u = host.lower() + sep + rest
    return u.rstrip("/")


def _read_json(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str))
    tmp.replace(p)


# ---------- BM25 ----------

class BM25:
    """Okapi BM25 over pre-tokenized documents. IDF uses the +1 form so it never goes negative."""

    def __init__(self, docs: list[list[str]], k1: float = K1, b: float = B):
        self.k1, self.b = k1, b
        self.docs = docs
        self.n = len(docs)
        self.lens = [len(d) for d in docs]
        self.avgdl = (sum(self.lens) / self.n) if self.n else 0.0
        self.tf: list[dict] = []
        df: dict[str, int] = {}
        for d in docs:
            counts: dict[str, int] = {}
            for t in d:
                counts[t] = counts.get(t, 0) + 1
            self.tf.append(counts)
            for t in counts:
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query_tokens: list[str]) -> list[float]:
        out = []
        q = set(query_tokens)
        for i, counts in enumerate(self.tf):
            s = 0.0
            norm = self.k1 * (1 - self.b + self.b * (self.lens[i] / self.avgdl if self.avgdl else 0.0))
            for t in q:
                f = counts.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out


# ---------- documents ----------

def _split_sections(md: str) -> list[tuple[str, str]]:
    """[(title, body)] split on markdown headings; text before the first heading is its own section."""
    sections: list[tuple[str, list[str]]] = []
    title, buf = "Plant profile", []
    for line in md.splitlines():
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
        if m:
            if any(x.strip() for x in buf):
                sections.append((title, buf))
            title, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if any(x.strip() for x in buf):
        sections.append((title, buf))
    return [(t, "\n".join(b).strip()) for t, b in sections]


def _lesson_text(lesson: dict) -> str:
    parts = [
        str(lesson.get("goal") or ""),
        " ".join(str(s) for s in _as_list(lesson.get("symptoms"))),
        str(lesson.get("fix") or ""),
        " ".join(str(s) for s in _as_list(lesson.get("steps"))),
        str(lesson.get("outcome") or ""),
    ]
    return "\n".join(p for p in parts if p)


def _lesson_summary(lesson: dict, n: int = 200) -> str:
    sym = "; ".join(str(s) for s in _as_list(lesson.get("symptoms")))
    s = f"{lesson.get('outcome') or '?'}: {lesson.get('fix') or '(no fix)'} | symptoms: {sym}"
    return s if len(s) <= n else s[: n - 3] + "..."


def _symptom_tokens(lesson: dict) -> list[str]:
    return tokenize(" ".join(str(s) for s in _as_list(lesson.get("symptoms"))))


# ---------- KB ----------

class KB:
    def __init__(self, kb_dir=DEFAULT_KB, log=None):
        self.dir = Path(kb_dir)
        self.log = log

    # paths
    @property
    def plant_dir(self) -> Path:
        return self.dir / "plant"

    @property
    def refs_dir(self) -> Path:
        return self.dir / "refs"

    @property
    def lessons_dir(self) -> Path:
        return self.dir / "lessons"

    def _emit(self, actor, type_, data):
        if self.log is None:
            return None
        try:
            return self.log.emit(actor, type_, data)
        except Exception:  # logging must never break retrieval
            return None

    # plant
    def plant_profile(self) -> str:
        p = self.plant_dir / "profile.md"
        try:
            return p.read_text()
        except OSError:
            return ""

    # refs
    def ref_path(self, query: str) -> Path:
        q = str(query)
        return self.refs_dir / f"{_slug(q)}-{hashlib.sha1(q.encode('utf-8')).hexdigest()[:8]}.json"

    def ref_files(self) -> list[dict]:
        out = []
        if not self.refs_dir.is_dir():
            return out
        for p in sorted(self.refs_dir.glob("*.json")):
            d = _read_json(p)
            if isinstance(d, dict) and isinstance(d.get("results"), list):
                out.append(d)
        return out

    def get_ref(self, query: str) -> dict | None:
        """The cached ref file for exactly this query, or None."""
        d = _read_json(self.ref_path(query))
        if isinstance(d, dict) and d.get("query") == query and isinstance(d.get("results"), list):
            return d
        for d in self.ref_files():  # file named by an older slug rule
            if d.get("query") == query:
                return d
        return None

    def refs(self) -> list[dict]:
        """Every cached result, one per normalised URL (first seen wins)."""
        seen, out = set(), []
        for f in self.ref_files():
            for r in f["results"]:
                if not isinstance(r, dict) or not r.get("url"):
                    continue
                key = normalize_url(r["url"])
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "url": r.get("url"),
                    "title": r.get("title") or "",
                    "snippet": r.get("snippet") or "",
                    "query": r.get("query") or f.get("query") or "",
                    "fetched_at": r.get("fetched_at") or f.get("fetched_at") or "",
                })
        return out

    def find_ref(self, url) -> dict | None:
        key = normalize_url(url)
        if not key:
            return None
        for r in self.refs():
            if normalize_url(r["url"]) == key:
                return r
        return None

    def add_ref(self, query: str, results: list) -> Path:
        ts = _now()
        clean = []
        for r in results or []:
            if not isinstance(r, dict) or not r.get("url"):
                continue
            clean.append({
                "url": str(r["url"]),
                "title": str(r.get("title") or ""),
                "snippet": str(r.get("snippet") or ""),
                "query": str(query),
                "fetched_at": ts,
            })
        p = self.ref_path(query)
        _write_json(p, {"query": str(query), "fetched_at": ts, "results": clean})
        return p

    # lessons
    def lessons(self, include_superseded: bool = False) -> list[dict]:
        out = []
        if not self.lessons_dir.is_dir():
            return out
        for p in sorted(self.lessons_dir.glob("*.json")):
            d = _read_json(p)
            if not isinstance(d, dict) or not d.get("id"):
                continue
            if not include_superseded and d.get("status", "active") != "active":
                continue
            out.append(d)
        out.sort(key=lambda l: (str(l.get("created_at") or ""), str(l["id"])))
        return out

    def _lesson_path(self, lesson_id: str) -> Path:
        return self.lessons_dir / f"{lesson_id}.json"

    def _free_id(self, base: str) -> str:
        base = _safe_id(base)
        if not self._lesson_path(base).exists():
            return base
        i = 2
        while self._lesson_path(f"{base}-{i}").exists():
            i += 1
        return f"{base}-{i}"

    def write_lesson(self, lesson: dict, actor: str = "planner") -> dict:
        new = dict(lesson or {})
        run_id = str(new.get("run_id") or "unknown")
        new["run_id"] = new.get("run_id") or run_id
        new["id"] = self._free_id(new.get("id") or f"L-{run_id}")
        new["symptoms"] = [str(s) for s in _as_list(new.get("symptoms"))]
        new["checks"] = _as_list(new.get("checks"))
        new["fix"] = new.get("fix") or ""
        new["steps"] = [str(s) for s in _as_list(new.get("steps"))]
        new.setdefault("outcome", None)
        new["verdict_seqs"] = _as_list(new.get("verdict_seqs"))
        new["status"] = "active"
        new["supersedes"] = None
        new["superseded_by"] = None
        if "verified" not in new or new["verified"] is None:
            new["verified"] = bool(new["verdict_seqs"])
        new["created_at"] = new.get("created_at") or _now()

        targets = self._supersede_targets(new) if (new["verdict_seqs"] and new["verified"]) else []
        if targets:
            new["supersedes"] = targets[0]["id"]

        _write_json(self._lesson_path(new["id"]), new)
        self._emit(actor, "lesson", {"id": new["id"], "op": "write", "text": _lesson_summary(new)})

        for old in targets:
            old = dict(old)
            old["status"] = "superseded"
            old["superseded_by"] = new["id"]
            _write_json(self._lesson_path(old["id"]), old)
            self._emit(actor, "lesson", {
                "id": old["id"], "op": "supersede",
                "text": f"superseded by {new['id']}: {_lesson_summary(old, 160)}",
            })
        return new

    def _supersede_targets(self, new: dict) -> list[dict]:
        """Active lessons describing the same symptoms with a different fix, best match first."""
        new_tok = _symptom_tokens(new)
        if not new_tok:
            return []
        actives = [l for l in self.lessons() if l.get("id") != new["id"]]
        if not actives:
            return []
        docs = [_symptom_tokens(l) for l in actives]
        scores = BM25(docs).scores(new_tok)
        top = max(range(len(actives)), key=lambda i: scores[i])
        new_set, new_fix = set(new_tok), _norm_fix(new.get("fix"))
        hits = []
        for i, old in enumerate(actives):
            jac = _jaccard(new_set, set(docs[i]))
            is_top = i == top and scores[i] > 0 and _overlap(new_set, set(docs[i])) >= TOP_HIT_OVERLAP_MIN
            if (jac >= JACCARD_MIN or is_top) and _norm_fix(old.get("fix")) != new_fix:
                hits.append((jac, scores[i], old))
        hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
        return [h[2] for h in hits]

    # retrieval
    def _documents(self, kinds) -> list[dict]:
        docs = []
        if "plant" in kinds:
            for i, (title, body) in enumerate(_split_sections(self.plant_profile())):
                docs.append({"kind": "plant", "id": f"plant#{i}-{_slug(title, 30)}", "title": title,
                             "text": body, "_index": f"{title}\n{body}"})
        if "refs" in kinds:
            for r in self.refs():
                docs.append({"kind": "refs", "id": r["url"], "title": r["title"], "text": r["snippet"],
                             "_index": f"{r['title']}\n{r['snippet']}\n{r['url']}"})
        if "lessons" in kinds:
            for l in self.lessons():
                docs.append({"kind": "lessons", "id": l["id"], "title": str(l.get("fix") or l["id"])[:120],
                             "text": _lesson_text(l), "_index": _lesson_text(l), "_lesson": l})
        return docs

    def search(self, query, k: int = 5, kinds=KINDS, actor: str = "planner") -> list[dict]:
        if isinstance(kinds, str):
            kinds = (kinds,)
        docs = self._documents(tuple(kinds))
        q = tokenize(query)
        if not docs or not q:
            return []
        scores = BM25([tokenize(d["_index"]) for d in docs]).scores(q)
        ranked = sorted((i for i in range(len(docs)) if scores[i] > 0), key=lambda i: (-scores[i], i))
        out = []
        for i in ranked[: max(0, int(k))]:
            d = docs[i]
            out.append({"kind": d["kind"], "id": d["id"], "title": d["title"], "text": d["text"],
                        "score": round(scores[i], 4)})
            if d["kind"] == "lessons":
                self._emit(actor, "lesson", {"id": d["id"], "op": "read", "text": _lesson_summary(d["_lesson"])})
        return out


# ---------- snapshots ----------

def snapshot(kb_dir=DEFAULT_KB, name: str = "", root=None) -> Path:
    """Copy plant/, refs/, lessons/ to <root or kb_dir>/snapshots/<name>/, replacing an existing snapshot."""
    name = str(name)
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError(f"bad snapshot name: {name!r}")
    kb_dir = Path(kb_dir)
    dest = Path(root or kb_dir) / "snapshots" / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for sub in ("plant", "refs", "lessons"):
        src = kb_dir / sub
        if src.is_dir():
            shutil.copytree(src, dest / sub)
        else:
            (dest / sub).mkdir()
    return dest


# ---------- plant profile from plant.yaml ----------

_ID_KEYS = ("id", "asset", "asset_id", "name", "tag")
_TYPE_KEYS = ("type", "kind", "class", "category")
_CONN_KEYS = ("connections", "connects_to", "connected_to", "upstream", "downstream", "feeds", "fed_by",
              "parent", "children", "bus", "feeder", "inverter", "block", "breaker", "from", "to")
_POINT_KEYS = ("points", "telemetry", "telemetry_points", "signals")
_CONTROL_KEYS = ("controls", "actions", "commands")
_RATING_HINT = re.compile(r"(rat(ed|ing)|capacity|nameplate|nominal|max|limit|kw|kva|mw|mva|mwh|kwh|kv|volt|"
                          r"amp|_a$|_v$|power|energy|strings|modules|area|count|efficien|ratio|size|noct)")
_DENY = re.compile(r"(fault|truth|hidden|scenario|soil|shad|degrad|health|alarm|trap|fix|symptom|playbook|"
                   r"seed|noise|onset|initial|state|status|photo|image|camera|derate|trip|latch|release|"
                   r"order|repair|procedure|diagnos|standard|soc)")
# groups that are not assets: fault/scenario data, and the work-order, diagnostic and standards catalogues
# (their names and descriptions read like fixes, which the profile must never contain)
_GROUP_DENY = re.compile(r"(fault|truth|hidden|scenario|weather|physics|photo|camera|trap|playbook|seed|"
                         r"order|diagnos|taxonom|standard|test|procedure|repair|maint|code)")


def _fmt(v) -> str:
    if isinstance(v, dict):
        return ", ".join(f"{k}={_fmt(x)}" for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return ", ".join(_fmt(x) for x in v)
    return str(v)


def _singular(key) -> str:
    k = str(key)
    return k[:-1] if k.endswith("s") and not k.endswith("ss") else k


def _collect_assets(data) -> list[tuple[str, dict, str | None]]:
    """[(asset_id, fields, default_type)] from an unknown plant.yaml layout."""
    if isinstance(data, dict) and isinstance(data.get("plant"), dict) and not any(
        k in data for k in ("assets", "registry", "equipment", "devices")
    ):
        data = {**data, **data["plant"]}
    found: list[tuple[str, dict, str | None]] = []

    def take(container, default_type):
        if isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    aid = next((item[k] for k in _ID_KEYS if isinstance(item.get(k), (str, int))), None)
                    if aid is not None:
                        found.append((str(aid), item, default_type))
        elif isinstance(container, dict):
            for aid, item in container.items():
                if isinstance(item, dict):
                    found.append((str(item.get("id", aid)), item, default_type))

    if isinstance(data, list):
        take(data, None)
        return found
    if not isinstance(data, dict):
        return found
    for key in ("assets", "registry", "equipment", "devices"):
        if key in data:
            take(data[key], None)
    if found:
        return found
    for key, val in data.items():  # grouped layout, e.g. inverters: [...]
        if _GROUP_DENY.search(str(key).lower()) or key in ("topology", "plant"):
            continue
        typ = _singular(key)
        if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
            take(val, typ)
        elif not isinstance(val, dict) or not val:
            continue
        elif isinstance(val.get("ids"), list):  # shared ratings + id list: {rating_kwac: .., ids: [INV-1, ..]}
            shared = {k: v for k, v in val.items() if k != "ids"}
            for aid in val["ids"]:
                if isinstance(aid, (str, int)):
                    found.append((str(aid), shared, typ))
        elif isinstance(val.get("id"), (str, int)):  # one asset: {id: BESS-1, power_kw: ..}
            found.append((str(val["id"]), val, typ))
        elif all(isinstance(x, dict) for x in val.values()):  # {INV-1: {...}, ...}
            take(val, typ)
        elif all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in val.values()):
            for aid, tgt in val.items():  # id -> connected asset: {52-I1: INV-1}
                found.append((str(aid), {"connects_to": str(tgt)}, typ))
    return found


def _ratings(fields: dict) -> list[str]:
    out = []
    for k, v in fields.items():
        kl = str(k).lower()
        if kl in _ID_KEYS or kl in _TYPE_KEYS or kl in _CONN_KEYS or kl in _POINT_KEYS or kl in _CONTROL_KEYS:
            continue
        if _DENY.search(kl):
            continue
        if kl in ("ratings", "rating", "rated", "nameplate", "specs", "spec"):
            if isinstance(v, dict):
                out.extend(f"{a}={_fmt(b)}" for a, b in v.items() if not _DENY.search(str(a).lower()))
            else:
                out.append(f"{k}={_fmt(v)}")
        elif isinstance(v, (int, float, str)) and not isinstance(v, bool) and _RATING_HINT.search(kl):
            out.append(f"{k}={v}")
    return out


def _names(v) -> str:
    if isinstance(v, dict):
        return ", ".join(str(k) for k in v)
    return _fmt(v)


def build_plant_profile(plant_yaml=DEFAULT_PLANT, out_md=None) -> str:
    """Write a profile (assets, ratings, topology only) from plant.yaml. Returns the markdown."""
    import yaml

    plant_yaml = Path(plant_yaml)
    if not plant_yaml.is_file():
        raise FileNotFoundError(f"plant registry not found: {plant_yaml}")
    data = yaml.safe_load(plant_yaml.read_text()) or {}
    assets = _collect_assets(data)

    lines = ["# Plant profile", "",
             f"Generated from `{plant_yaml.name}`. Assets, ratings, telemetry points, controls and topology only.", ""]
    top = data.get("plant") if isinstance(data, dict) and isinstance(data.get("plant"), dict) else data
    if isinstance(top, dict):
        scalars = list(top.items())
        if top is not data and isinstance(data, dict):
            scalars += [(k, v) for k, v in data.items() if k not in top]  # e.g. combiners_per_block: 6
        overview = [f"- {k}: {v}" for k, v in scalars
                    if isinstance(v, (str, int, float)) and not isinstance(v, bool) and not _DENY.search(str(k).lower())]
        if overview:
            lines += ["## Overview", "", *overview, ""]

    lines += ["## Assets", "", "| Asset | Type | Ratings | Connections | Telemetry points | Controls |",
              "|---|---|---|---|---|---|"]
    edges = []
    for aid, fields, default_type in assets:
        typ = next((str(fields[k]) for k in _TYPE_KEYS if fields.get(k)), default_type or "")
        conns = []
        for k in _CONN_KEYS:
            if fields.get(k) not in (None, "", [], {}):
                conns.append(f"{k}: {_names(fields[k])}")
                for tgt in _as_list(fields[k]) if not isinstance(fields[k], dict) else list(fields[k]):
                    if isinstance(tgt, (str, int)):
                        edges.append((k, aid, str(tgt)))
        pts = next((_names(fields[k]) for k in _POINT_KEYS if fields.get(k)), "")
        ctl = next((_names(fields[k]) for k in _CONTROL_KEYS if fields.get(k)), "")
        cells = [aid, typ, "; ".join(_ratings(fields)), "; ".join(conns), pts, ctl]
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    lines.append("")

    topo = data.get("topology") if isinstance(data, dict) else None
    if topo is None and isinstance(top, dict):
        topo = top.get("topology")
    lines += ["## Topology", ""]
    if isinstance(topo, str):
        lines += [topo.strip(), ""]
    elif isinstance(topo, (list, tuple)):
        lines += [f"- {_fmt(x)}" for x in topo] + [""]
    elif isinstance(topo, dict):
        lines += [f"- {k}: {_fmt(v)}" for k, v in topo.items()] + [""]
    elif edges:
        lines += [f"- {a} {rel} {b}" for rel, a, b in edges] + [""]
    else:
        lines += ["(no topology in the registry)", ""]

    md = "\n".join(lines)
    if out_md is not None:
        out = Path(out_md)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md)
    return md


# ---------- CLI ----------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gridtwin.kb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("profile", help="regenerate kb/plant/profile.md from plant.yaml")
    p.add_argument("--plant", default=str(DEFAULT_PLANT))
    p.add_argument("--out", default=str(DEFAULT_KB / "plant" / "profile.md"))
    s = sub.add_parser("snapshot", help="freeze plant/, refs/, lessons/ into kb/snapshots/<name>/")
    s.add_argument("name")
    s.add_argument("--kb", default=str(DEFAULT_KB))
    a = ap.parse_args(argv)
    try:
        if a.cmd == "profile":
            build_plant_profile(a.plant, a.out)
            print(f"wrote {a.out}")
        else:
            print(snapshot(a.kb, a.name))
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

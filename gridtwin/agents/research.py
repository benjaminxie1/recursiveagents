"""search_web tool: You.com search with a per-query cache in kb/refs and an offline BM25 fallback.

search_web never raises. Results always come back as
{"query", "results": [{"url", "title", "snippet", "query", "fetched_at"}], "cached", "offline"}.
"""
from __future__ import annotations

import os

import httpx

from gridtwin.agents.common import ActorToolbox
from gridtwin.kb import KB

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional at runtime
    pass

YDC_URL = "https://ydc-index.io/v1/search"
SNIPPET_MAX = 600

SEARCH_WEB_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "Search the web (You.com) for OEM manuals, fault codes and standards. "
            "Returns results with URLs and snippets; cite URLs exactly."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Search query."}},
            "required": ["query"],
        },
    },
}


def _parse_ydc(data, count: int) -> list[dict]:
    """[{url, title, snippet}] from a You.com search response, tolerant of shape drift."""
    if not isinstance(data, dict):
        return []
    res = data.get("results", data)
    items = None
    if isinstance(res, dict):
        items = res.get("web") or res.get("hits") or res.get("results")
    elif isinstance(res, list):
        items = res
    if not isinstance(items, list):
        items = data.get("hits") if isinstance(data.get("hits"), list) else []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        url = it.get("url") or it.get("link")
        if not url:
            continue
        desc = it.get("description") or ""
        snips = it.get("snippets") or []
        if isinstance(snips, str):
            snips = [snips]
        snippet = str(desc).strip() or " ".join(str(s).strip() for s in snips if s)
        out.append({"url": str(url), "title": str(it.get("title") or ""), "snippet": snippet[:SNIPPET_MAX]})
        if len(out) >= count:
            break
    return out


def _offline(kb: KB, query: str, count: int, note: str) -> dict:
    by_url = {r["url"]: r for r in kb.refs()}
    hits = kb.search(query, k=count, kinds=("refs",))
    results = [by_url[h["id"]] for h in hits if h["id"] in by_url]
    if results:
        return {"query": query, "results": results, "cached": True, "offline": True}
    return {"query": query, "results": [], "cached": False, "offline": True, "note": note}


def search_web(query, kb_dir, count: int = 5) -> dict:
    """Exact-query cache, then You.com (when YDC_API_KEY is set), then BM25 over cached refs."""
    q = str(query or "").strip()
    try:
        count = max(1, min(int(count), 20))
    except (TypeError, ValueError):
        count = 5
    if not q:
        return {"query": q, "results": [], "cached": False, "offline": False, "error": "empty query"}
    try:
        kb = KB(kb_dir)
        hit = kb.get_ref(q)
        if hit is not None:
            return {"query": q, "results": list(hit["results"])[:count], "cached": True, "offline": False}

        key = os.environ.get("YDC_API_KEY")
        if not key:
            return _offline(kb, q, count, "no key and no cached results")
        try:
            r = httpx.get(YDC_URL, params={"query": q, "count": count}, headers={"X-API-Key": key}, timeout=30)
            r.raise_for_status()
            parsed = _parse_ydc(r.json(), count)
        except Exception as e:  # HTTP, timeout, bad JSON: fall back to the cache
            return _offline(kb, q, count, f"search failed ({type(e).__name__}) and no cached results")
        if not parsed:
            return {"query": q, "results": [], "cached": False, "offline": False}
        kb.add_ref(q, parsed)
        stored = kb.get_ref(q) or {}
        return {"query": q, "results": stored.get("results") or parsed, "cached": False, "offline": False}
    except Exception as e:
        return {"query": q, "results": [], "cached": False, "offline": True,
                "error": f"search_web failed: {type(e).__name__}"}


class ResearchToolbox:
    """Handles search_web itself (logged as tool_call/tool_result under `actor`); delegates the rest."""

    def __init__(self, toolbox, log, kb_dir, actor: str):
        self.toolbox = toolbox
        self.log = log
        self.kb_dir = kb_dir
        self.actor = actor
        self._inner = ActorToolbox(toolbox, actor)

    def _emit(self, type_, data):
        if self.log is not None:
            try:
                self.log.emit(self.actor, type_, data)
            except Exception:
                pass

    def call(self, name: str, args: dict | None = None) -> dict:
        if name == "search_web":
            args = dict(args or {})
            self._emit("tool_call", {"name": name, "args": args})
            result = search_web(args.get("query", ""), self.kb_dir, count=args.get("count", 5))
            self._emit("tool_result", {"name": name, "result": result})
            return result
        return self._inner.call(name, args)

    def __getattr__(self, item):
        return getattr(self.toolbox, item)

import json

import httpx
import pytest

from gridtwin.agents import research
from gridtwin.agents.research import SEARCH_WEB_SCHEMA, ResearchToolbox, search_web
from gridtwin.kb import KB


class FakeLog:
    def __init__(self):
        self.events = []

    def emit(self, actor, type, data):
        e = {"seq": len(self.events) + 1, "actor": actor, "type": type, "data": data}
        self.events.append(e)
        return e


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=httpx.Request("GET", research.YDC_URL), response=None)

    def json(self):
        return self._data


def no_network(*a, **k):
    raise AssertionError("network must not be called")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    monkeypatch.setattr(research.httpx, "get", no_network)


def test_schema_is_neutral_function_tool():
    f = SEARCH_WEB_SCHEMA["function"]
    assert SEARCH_WEB_SCHEMA["type"] == "function" and f["name"] == "search_web"
    assert f["parameters"]["required"] == ["query"]
    assert f["description"].startswith("Search the web (You.com)")


def test_exact_cache_hit_skips_network(tmp_path, monkeypatch):
    monkeypatch.setenv("YDC_API_KEY", "test-key")
    KB(tmp_path).add_ref("inverter fan manual", [{"url": "https://ex.com/a", "title": "A", "snippet": "fan"}])
    out = search_web("inverter fan manual", tmp_path)
    assert out["cached"] is True and out["offline"] is False and out["query"] == "inverter fan manual"
    assert out["results"][0]["url"] == "https://ex.com/a"


def test_offline_fallback_without_key(tmp_path):
    k = KB(tmp_path)
    k.add_ref("inverter heatsink derate", [{"url": "https://ex.com/heat", "title": "Heatsink", "snippet": "fan filter"}])
    k.add_ref("battery rack", [{"url": "https://ex.com/bess", "title": "BESS", "snippet": "rack alarm"}])
    out = search_web("heatsink fan", tmp_path)
    assert out["offline"] is True and out["cached"] is True
    assert out["results"][0]["url"] == "https://ex.com/heat"
    assert set(out["results"][0]) == {"url", "title", "snippet", "query", "fetched_at"}


def test_offline_fallback_empty(tmp_path):
    out = search_web("anything at all", tmp_path)
    assert out == {"query": "anything at all", "results": [], "cached": False, "offline": True,
                   "note": "no key and no cached results"}


def test_live_path_parses_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("YDC_API_KEY", "test-key")
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers, timeout=timeout)
        return FakeResp({"results": {"web": [
            {"url": "https://oem.example/manual.pdf", "title": "OEM manual", "description": "Fan service interval.",
             "snippets": ["ignored"]},
            {"url": "https://ieee.example/std", "title": "IEEE std", "snippets": ["part one", "part two" + "x" * 900]},
            {"title": "no url"},
        ], "news": []}})

    monkeypatch.setattr(research.httpx, "get", fake_get)
    out = search_web("inverter fan service", tmp_path, count=3)
    assert seen["url"] == "https://ydc-index.io/v1/search"
    assert seen["params"] == {"query": "inverter fan service", "count": 3}
    assert seen["headers"] == {"X-API-Key": "test-key"} and seen["timeout"] == 30
    assert out["cached"] is False and out["offline"] is False
    r0, r1 = out["results"]
    assert r0["snippet"] == "Fan service interval." and r0["query"] == "inverter fan service" and r0["fetched_at"]
    assert r1["snippet"].startswith("part one part two") and len(r1["snippet"]) == 600
    files = list((tmp_path / "refs").glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["query"] == "inverter fan service"
    assert "test-key" not in files[0].read_text()
    monkeypatch.setattr(research.httpx, "get", no_network)
    again = search_web("inverter fan service", tmp_path)
    assert again["cached"] is True and [r["url"] for r in again["results"]] == [r0["url"], r1["url"]]


def test_http_error_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("YDC_API_KEY", "test-key")
    KB(tmp_path).add_ref("dga gas limits", [{"url": "https://ex.com/dga", "title": "DGA", "snippet": "acetylene ppm"}])
    monkeypatch.setattr(research.httpx, "get", lambda *a, **k: FakeResp({}, status=500))
    out = search_web("transformer dga acetylene", tmp_path)
    assert out["offline"] is True and out["results"][0]["url"] == "https://ex.com/dga"

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(research.httpx, "get", boom)
    out = search_web("nothing cached here", tmp_path)
    assert out["results"] == [] and out["offline"] is True and "test-key" not in json.dumps(out)


class FakeToolbox:
    def __init__(self):
        self.actor = None
        self.calls = []

    def call(self, name, args):
        self.calls.append((name, args, self.actor))
        return {"ok": True, "name": name}


def test_research_toolbox_logs_and_delegates(tmp_path):
    KB(tmp_path).add_ref("q1", [{"url": "https://ex.com/q", "title": "Q", "snippet": "s"}])
    tb, log = FakeToolbox(), FakeLog()
    rt = ResearchToolbox(tb, log, tmp_path, "worker:research")
    out = rt.call("search_web", {"query": "q1"})
    assert out["cached"] is True and tb.calls == []
    assert [(e["actor"], e["type"]) for e in log.events] == [("worker:research", "tool_call"),
                                                             ("worker:research", "tool_result")]
    assert log.events[0]["data"] == {"name": "search_web", "args": {"query": "q1"}}
    assert log.events[1]["data"] == {"name": "search_web", "result": out}
    res = rt.call("list_alarms", {"active_only": True})
    assert res == {"ok": True, "name": "list_alarms"}
    assert tb.calls == [("list_alarms", {"active_only": True}, "worker:research")]
    assert tb.actor is None and len(log.events) == 2

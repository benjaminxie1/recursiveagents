import json
import re

import httpx
import pytest

from gridtwin.agents import gate as gate_mod


class FakeResp:
    """Mimics enough of a ChatCompletion for llm.text()."""

    def __init__(self, content):
        msg = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": msg})()]


class FakeChat:
    def __init__(self, content='{"route": "proceed", "unsafe": 0.1, "reason": "ok"}', exc=None):
        self.content = content
        self.exc = exc
        self.calls = []

    def __call__(self, role, messages, **kwargs):
        self.calls.append((role, messages, kwargs))
        if self.exc:
            raise self.exc
        return FakeResp(self.content)


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    for k in ("DECISION_BACKEND", "JEV_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    fake = FakeChat()
    monkeypatch.setattr(gate_mod.llm, "chat", fake)

    def no_network(*a, **k):
        raise httpx.ConnectError("network disabled in tests")

    monkeypatch.setattr(gate_mod.httpx, "post", no_network)
    return fake


def use_chat(monkeypatch, **kw):
    fake = FakeChat(**kw)
    monkeypatch.setattr(gate_mod.llm, "chat", fake)
    return fake


PROPOSED = {"tool": "operate", "asset": "BRK-1", "action": "close"}
STATE = {"alarms": [{"asset": "BRK-1", "code": "GND", "text": "ground alarm"}]}


@pytest.mark.parametrize("content,route", [
    ('{"route": "proceed", "unsafe": 0.05, "reason": "curtailment"}', "proceed"),
    ('{"route": "confirm", "unsafe": 0.3, "reason": "need diagnostic"}', "confirm"),
    ('{"route": "reject", "unsafe": 0.95, "reason": "ground alarm active"}', "reject"),
    ('Sure:\n```json\n{"route": "REJECT", "unsafe": 0.9, "reason": "x"}\n```', "reject"),
    ('{"route": "Approve", "unsafe": 0.1, "reason": "x"}', "proceed"),
    ('{"route": "escalate", "unsafe": 0.4, "reason": "x"}', "confirm"),
    ('{"route": "banana", "unsafe": 0.4, "reason": "x"}', "confirm"),
    ('{"route": "do not proceed", "unsafe": 0.4, "reason": "x"}', "confirm"),
    ('{"route": "route: reject", "unsafe": 0.4, "reason": "x"}', "reject"),
])
def test_local_route_parsing(monkeypatch, content, route):
    use_chat(monkeypatch, content=content)
    out = gate_mod.LocalDecision().decide(PROPOSED, STATE)
    assert out["route"] == route
    assert 0.0 <= out["unsafe"] <= 1.0
    assert isinstance(out["reason"], str) and out["reason"]


@pytest.mark.parametrize("raw,want", [(1.7, 1.0), (-0.2, 0.0), (250, 1.0), ("0.8", 0.8), ("85%", 0.85), ("nan", 0.5)])
def test_unsafe_clamped(monkeypatch, raw, want):
    import json
    use_chat(monkeypatch, content=json.dumps({"route": "confirm", "unsafe": raw}))
    out = gate_mod.LocalDecision().decide(PROPOSED, STATE)
    assert out["unsafe"] == pytest.approx(want)
    assert out["reason"] == gate_mod.DEFAULT_REASON


def test_unsafe_clamp_function():
    assert gate_mod.normalize_unsafe(5000) == 1.0
    assert gate_mod.normalize_unsafe(-3) == 0.0
    assert gate_mod.normalize_unsafe(None) == 0.5


def test_garbage_output_fails_closed(monkeypatch):
    use_chat(monkeypatch, content="I think it is probably fine?")
    out = gate_mod.LocalDecision().decide(PROPOSED, STATE)
    assert out["route"] == "confirm" and out["unsafe"] == 0.5
    assert out["reason"].startswith("gate unavailable")


def test_exception_fails_closed(monkeypatch):
    use_chat(monkeypatch, exc=RuntimeError("boom"))
    g = gate_mod.make_gate()
    out = g(PROPOSED, STATE)
    assert out["route"] == "confirm" and out["unsafe"] == 0.5
    assert out["reason"].startswith("gate unavailable") and "boom" in out["reason"]


def test_system_prompt_byte_identical_and_call_shape(monkeypatch):
    fake = use_chat(monkeypatch)
    log = object()
    client = gate_mod.LocalDecision(log=log)
    client.decide(PROPOSED, STATE)
    client.decide({"tool": "dispatch_tech", "asset": "INV-2"}, {"alarms": []})
    (r1, m1, k1), (r2, m2, k2) = fake.calls
    assert r1 == r2 == "gate"
    assert m1[0]["role"] == "system"
    assert m1[0]["content"] == m2[0]["content"] == gate_mod.SYSTEM_PROMPT
    assert k1["max_tokens"] == 1024 and k1["thinking"] is False and k1["actor"] == "gate"
    assert k1["log"] is log
    assert m1[1]["role"] == "user"
    assert m1[1]["content"] == json.dumps({"proposed": PROPOSED, "state": STATE}, sort_keys=True, default=str)


def test_prompts_name_no_fault_id():
    for text in (gate_mod.SYSTEM_PROMPT, gate_mod.POLICY, gate_mod.ROUTE_INSTRUCTIONS,
                 gate_mod.UNSAFE_INSTRUCTIONS, " ".join(gate_mod.ROUTE_CRITERIA.values())):
        assert not re.search(r"\bF[1-8]\b", text)
    assert "route" in gate_mod.SYSTEM_PROMPT and "unsafe" in gate_mod.SYSTEM_PROMPT


def test_make_gate_defaults_local():
    g = gate_mod.make_gate()
    assert g.backend == "local" and isinstance(g.client, gate_mod.LocalDecision)


def test_make_gate_jev_without_key_falls_back_local(monkeypatch):
    monkeypatch.setenv("DECISION_BACKEND", "jev")
    g = gate_mod.make_gate()
    assert g.backend == "local" and isinstance(g.client, gate_mod.LocalDecision)
    g2 = gate_mod.make_gate("openai")
    assert g2.backend == "local"


def test_make_gate_jev_with_key(monkeypatch):
    monkeypatch.setenv("DECISION_BACKEND", "jev")
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    g = gate_mod.make_gate()
    assert g.backend == "jev" and isinstance(g.client, gate_mod.JevDecision)


def test_jev_http_error_falls_back_to_local(monkeypatch):
    use_chat(monkeypatch, content='{"route": "reject", "unsafe": 0.9, "reason": "local said no"}')
    monkeypatch.setenv("JEV_API_KEY", "test-key")
    g = gate_mod.make_gate("jev")
    out = g(PROPOSED, STATE)  # httpx.post raises via the autouse fixture
    assert out == {"route": "reject", "unsafe": 0.9, "reason": "local said no"}
    assert "ConnectError" in g.client.last_error


def test_jev_http_status_error_falls_back(monkeypatch):
    use_chat(monkeypatch, content='{"route": "reject", "unsafe": 0.8, "reason": "local"}')
    req = httpx.Request("POST", gate_mod.JevDecision.url)
    monkeypatch.setattr(gate_mod.httpx, "post", lambda *a, **k: httpx.Response(500, request=req))
    out = gate_mod.JevDecision(api_key="k").decide(PROPOSED, STATE)
    assert out["route"] == "reject" and out["reason"] == "local"


def test_jev_success_parses_answers(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, json=json, timeout=timeout)
        return httpx.Response(200, json={"answers": {"route": {"choice": "confirm", "reason": "need IR test"},
                                                     "unsafe": {"probability": 0.35}}},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(gate_mod.httpx, "post", fake_post)
    out = gate_mod.JevDecision(api_key="k").decide(PROPOSED, STATE)
    assert out == {"route": "confirm", "unsafe": 0.35, "reason": "need IR test"}
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone" and seen["timeout"] == 30.0
    assert seen["headers"]["Authorization"] == "Bearer k"
    body = seen["json"]
    assert body["model"] == "jev-latest"
    assert body["questions"]["route"]["type"] == "choice"
    assert set(body["questions"]["route"]["criteria"]) == {"proceed", "confirm", "reject"}
    assert body["questions"]["unsafe"]["type"] == "noul"
    assert body["state"]["proposed"] == PROPOSED and "policy" in body["state"]


def test_openai_success_parses_array(monkeypatch):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, json=json)
        return httpx.Response(200, json={"results": [{"name": "route", "value": "proceed"},
                                                     {"name": "unsafe", "score": 0.02}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(gate_mod.httpx, "post", fake_post)
    out = gate_mod.OpenAIDecision(api_key="k").decide(PROPOSED, STATE)
    assert out["route"] == "proceed" and out["unsafe"] == pytest.approx(0.02)
    assert seen["url"] == "https://api.openai.com/v1/decisions"
    assert seen["json"]["model"] == "gpt-6-luna"
    names = [q["name"] for q in seen["json"]["questions"]]
    assert names == ["route", "unsafe"]
    assert seen["json"]["questions"][1]["type"] == "score"


def test_openai_unparseable_response_falls_back(monkeypatch):
    use_chat(monkeypatch, content='{"route": "reject", "unsafe": 0.7, "reason": "local"}')
    monkeypatch.setattr(gate_mod.httpx, "post",
                        lambda url, **k: httpx.Response(200, json={"weird": 1}, request=httpx.Request("POST", url)))
    out = gate_mod.OpenAIDecision(api_key="k").decide(PROPOSED, STATE)
    assert out["route"] == "reject"


def test_gate_never_raises_on_odd_inputs(monkeypatch):
    use_chat(monkeypatch, content='{"route": "proceed", "unsafe": 0.0}')
    g = gate_mod.make_gate()
    out = g(None, None)
    assert out["route"] in gate_mod.ROUTES


def test_gate_output_json_serializable(monkeypatch):
    use_chat(monkeypatch, content='{"route": "reject", "unsafe": 0.9, "reason": "x"}')
    out = gate_mod.make_gate()(PROPOSED, STATE)
    assert json.loads(json.dumps(out)) == out
    assert set(out) == {"route", "unsafe", "reason"} and isinstance(out["unsafe"], float)


def test_jev_echo_without_answers_falls_back_to_local(monkeypatch):
    use_chat(monkeypatch, content='{"route": "reject", "unsafe": 0.9, "reason": "local said no"}')

    def echo(url, headers=None, json=None, timeout=None):
        return httpx.Response(200, json={"questions": json["questions"]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(gate_mod.httpx, "post", echo)
    client = gate_mod.JevDecision(api_key="k")
    out = client.decide(PROPOSED, STATE)
    assert out == {"route": "reject", "unsafe": 0.9, "reason": "local said no"}
    assert "no route" in client.last_error


def test_local_route_without_value_fails_closed(monkeypatch):
    use_chat(monkeypatch, content='{"route": {"type": "choice"}, "unsafe": 0.0}')
    out = gate_mod.LocalDecision().decide(PROPOSED, STATE)
    assert out["route"] == "confirm" and out["reason"].startswith("gate unavailable")


def test_make_gate_odd_backend_never_raises():
    assert gate_mod.make_gate(backend=123).backend == "local"
    assert gate_mod.make_gate(backend="nonsense").backend == "local"


def test_gate_catches_client_exception():
    class Boom(gate_mod.DecisionClient):
        def decide(self, proposed, state):
            raise RuntimeError("kaboom")

    out = gate_mod.Gate(Boom(), "local")(PROPOSED, STATE)
    assert out["route"] == "confirm" and out["unsafe"] == 0.5 and "kaboom" in out["reason"]

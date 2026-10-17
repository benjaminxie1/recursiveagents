"""gridtwin.llm against a fake client: no network."""
import logging

import httpx
import pytest
from openai import APIConnectionError, RateLimitError
from openai.types.chat import ChatCompletion

from gridtwin import llm
from gridtwin.config import PRICES, ROLES


def completion(content="ok", finish="stop", prompt=100, cached=0, out=10, tool_calls=None,
               reasoning_content=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if reasoning_content is not None:
        msg["reasoning_content"] = reasoning_content
    return ChatCompletion.model_validate({
        "id": "c1", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": finish, "message": msg}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": out,
                  "total_tokens": prompt + out,
                  "prompt_tokens_details": {"cached_tokens": cached}},
    })


class Raw:
    def __init__(self, resp, headers=None):
        self._resp = resp
        self.headers = httpx.Headers(headers or {})

    def parse(self):
        return self._resp


class FakeClient:
    """Plays a script of Raw responses or exceptions; records every create() kwargs."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.chat = self
        self.completions = self
        self.with_raw_response = self

    def create(self, **kw):
        self.calls.append(kw)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeLog:
    def __init__(self):
        self.events = []

    def emit(self, actor, type, data):
        ev = {"seq": len(self.events), "actor": actor, "type": type, "data": data}
        self.events.append(ev)
        return ev


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    clock = [1000.0]
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        clock[0] += s

    def no_network():
        pytest.fail("real client used in a test")

    monkeypatch.setattr(llm, "client", no_network)  # use() replaces this with a FakeClient
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(llm, "_sleep", fake_sleep)
    monkeypatch.setattr(llm, "_now", lambda: clock[0])
    monkeypatch.setattr(llm, "THROTTLE", llm.Throttle())
    llm.USAGE.reset()
    yield sleeps
    llm.USAGE.reset()


def use(monkeypatch, script):
    fc = FakeClient(script)
    monkeypatch.setattr(llm, "client", lambda: fc)
    return fc


def rate_limit_error(headers=None):
    req = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    resp = httpx.Response(429, request=req, headers=headers or {})
    return RateLimitError("rate limited", response=resp, body=None)


MSG = [{"role": "user", "content": "hi"}]


def test_throttle_learns_limit_from_header(monkeypatch):
    use(monkeypatch, [Raw(completion(), {"x-ratelimit-limit-requests": "600"})])
    llm.chat("worker", MSG)
    assert llm.THROTTLE.rpm[ROLES["worker"]] == 600
    assert llm.THROTTLE.limit(ROLES["worker"]) == 600
    assert llm.THROTTLE.limit("other/model") == llm.DEFAULT_RPM


def test_learned_limit_is_enforced_by_chat(monkeypatch, fresh):
    use(monkeypatch, [Raw(completion(), {"x-ratelimit-limit-requests": "2"}),
                      Raw(completion()), Raw(completion())])
    llm.chat("worker", MSG)
    llm.chat("worker", MSG)
    assert fresh == []
    llm.chat("worker", MSG)  # third request inside the minute waits for the window
    assert len(fresh) == 1 and 59 < fresh[0] < 61


def test_throttle_learns_limit_from_429_header(monkeypatch):
    use(monkeypatch, [rate_limit_error({"x-ratelimit-limit-requests": "30"}), Raw(completion())])
    llm.chat("verifier", MSG)
    assert llm.THROTTLE.rpm[ROLES["verifier"]] == 30


def test_whitespace_only_length_output_is_retried(monkeypatch):
    fc = use(monkeypatch, [Raw(completion(content="\n\n", finish="length")), Raw(completion("x"))])
    assert llm.text(llm.chat("worker", MSG, max_tokens=1024)) == "x"
    assert [c["max_tokens"] for c in fc.calls] == [1024, 2048]


def test_default_rpm_parsing():
    from gridtwin import config
    assert config._rpm(None) == 30 and config._rpm("") == 30 and config._rpm("abc") == 30
    assert config._rpm("0") == 30 and config._rpm(" 600 ") == 600


def test_throttle_waits_when_window_full(monkeypatch, fresh):
    t = llm.Throttle(default_rpm=2)
    t.wait("m")
    t.wait("m")
    assert fresh == []
    t.wait("m")  # third request in the window must sleep ~60 s
    assert len(fresh) == 1 and 59 < fresh[0] < 61
    t.wait("other")  # separate bucket per model
    assert len(fresh) == 1


def test_retry_on_rate_limit_then_success(monkeypatch, fresh):
    fc = use(monkeypatch, [rate_limit_error(), APIConnectionError(request=httpx.Request("POST", "https://x")),
                           Raw(completion("done"))])
    resp = llm.chat("planner", MSG)
    assert llm.text(resp) == "done"
    assert len(fc.calls) == 3
    assert len(fresh) == 2 and all(0 < s <= 60 for s in fresh)


def test_retry_after_header_is_honoured_and_capped(monkeypatch, fresh):
    use(monkeypatch, [rate_limit_error({"retry-after": "7"}), rate_limit_error({"retry-after": "500"}),
                      Raw(completion())])
    llm.chat("planner", MSG)
    assert 7 <= fresh[0] <= 7.5
    assert fresh[1] == 60.0


def test_retries_exhausted_raises(monkeypatch):
    use(monkeypatch, [rate_limit_error(), rate_limit_error()])
    with pytest.raises(RateLimitError):
        llm.chat("planner", MSG, max_tries=2)


def test_usage_totals_and_cache_hit_rate(monkeypatch):
    use(monkeypatch, [Raw(completion(prompt=1000, cached=600, out=100)),
                      Raw(completion(prompt=1000, cached=200, out=50)),
                      Raw(completion(prompt=400, cached=0, out=10))])
    llm.chat("planner", MSG)
    llm.chat("planner", MSG)
    llm.chat("verifier", MSG)
    snap = llm.USAGE.snapshot()
    assert snap["planner"]["calls"] == 2
    assert snap["planner"]["prompt"] == 2000
    assert snap["planner"]["cached"] == 800
    assert snap["planner"]["completion"] == 150
    assert snap["total"]["calls"] == 3 and snap["total"]["prompt"] == 2400
    assert llm.USAGE.cache_hit_rate("planner") == pytest.approx(0.4)
    assert llm.USAGE.cache_hit_rate() == pytest.approx(800 / 2400)
    inp, out, cp = PRICES[ROLES["planner"]]
    want = (1200 * inp + 800 * cp + 150 * out) / 1e6
    assert llm.USAGE.usd("planner") == pytest.approx(want)
    assert llm.USAGE.usd() == pytest.approx(want + (400 * 0.05 + 10 * 0.20) / 1e6)
    llm.USAGE.reset()
    assert llm.USAGE.snapshot()["total"]["calls"] == 0


def test_usage_tolerates_missing_details(monkeypatch):
    resp = completion()
    resp.usage.prompt_tokens_details = None
    use(monkeypatch, [Raw(resp)])
    llm.chat("worker", MSG)
    assert llm.USAGE.snapshot()["worker"]["cached"] == 0


def test_usage_event_emitted(monkeypatch):
    use(monkeypatch, [Raw(completion(prompt=50, cached=20, out=5)), Raw(completion())])
    log = FakeLog()
    llm.chat("worker", MSG, log=log, actor="worker:inspector")
    llm.chat("gate", MSG, log=log)
    assert [e["type"] for e in log.events] == ["usage", "usage"]
    e = log.events[0]
    assert e["actor"] == "worker:inspector"
    assert {"role", "prompt", "cached", "completion", "model", "usd"} <= set(e["data"])
    assert e["data"]["role"] == "worker" and e["data"]["model"] == ROLES["worker"]
    assert (e["data"]["prompt"], e["data"]["cached"], e["data"]["completion"]) == (50, 20, 5)
    assert log.events[1]["actor"] == "gate"


def test_empty_output_retries_with_doubled_max_tokens(monkeypatch, caplog):
    fc = use(monkeypatch, [Raw(completion(content="", finish="length")), Raw(completion("answer"))])
    with caplog.at_level(logging.WARNING, logger="gridtwin.llm"):
        resp = llm.chat("worker", MSG, max_tokens=1024)
    assert llm.text(resp) == "answer"
    assert [c["max_tokens"] for c in fc.calls] == [1024, 2048]
    assert "ran out of tokens" in caplog.text


def test_empty_output_retry_is_capped_and_once(monkeypatch):
    fc = use(monkeypatch, [Raw(completion(content=None, finish="length")),
                           Raw(completion(content=None, finish="length"))])
    resp = llm.chat("worker", MSG, max_tokens=12000)
    assert [c["max_tokens"] for c in fc.calls] == [12000, 16384]
    assert llm.text(resp) == ""


def test_length_with_tool_calls_is_not_retried(monkeypatch):
    tc = [{"id": "t1", "type": "function", "function": {"name": "read_telemetry", "arguments": "{}"}}]
    fc = use(monkeypatch, [Raw(completion(content=None, finish="length", tool_calls=tc))])
    resp = llm.chat("worker", MSG)
    assert len(fc.calls) == 1
    assert llm.tool_calls(resp)[0].function.name == "read_telemetry"


def test_thinking_only_for_nvidia_models(monkeypatch):
    fc = use(monkeypatch, [Raw(completion()) for _ in range(4)])
    llm.chat("gate", MSG, thinking=False)
    llm.chat("worker", MSG, thinking=True, extra_body={"chat_template_kwargs": {"force_nonempty_content": True},
                                                       "top_k": 5})
    llm.chat("verifier", MSG, thinking=True)
    llm.chat("planner", MSG)
    assert fc.calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert fc.calls[1]["extra_body"] == {
        "chat_template_kwargs": {"force_nonempty_content": True, "enable_thinking": True}, "top_k": 5}
    assert "extra_body" not in fc.calls[2]
    assert "extra_body" not in fc.calls[3]
    for c in fc.calls:  # extras never reach the API
        assert not {"log", "actor", "thinking", "max_tries"} & set(c)


def test_helpers():
    r = completion(content=None, reasoning_content="because")
    assert llm.text(r) == ""
    assert llm.tool_calls(r) == []
    assert llm.reasoning(r) == "because"
    assert llm.reasoning(completion()) == ""


def test_config_roles_and_prices():
    assert ROLES["gate"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    assert list(ROLES.values()).count(ROLES["gate"]) == 1
    assert ROLES["supervisor"] == ROLES["planner"]
    for model in ROLES.values():
        assert model in PRICES

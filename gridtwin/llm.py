"""Crusoe Managed Inference client: per-model throttle, retries, usage totals.

chat() owns every retry (the SDK client has max_retries=0). Thread-safe: workers may call it
from several threads at once.
"""
from __future__ import annotations

import email.utils
import logging
import os
import random
import threading
import time
from collections import defaultdict, deque

from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError

from gridtwin.config import BASE_URL, DEFAULT_RPM, PRICES, ROLES

_logger = logging.getLogger("gridtwin.llm")

# Patchable in tests so throttle and backoff waits are instant.
_sleep = time.sleep
_now = time.monotonic
_clock = time.perf_counter  # times the successful HTTP request (latency_s in usage records)

MAX_BACKOFF_S = 60.0
MAX_TOKENS_CAP = 16384
WINDOW_S = 60.0
RETRYABLE = (RateLimitError, InternalServerError, APIConnectionError)  # APITimeoutError included

_client = None
_client_lock = threading.Lock()


def client() -> OpenAI:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = OpenAI(
                    base_url=BASE_URL,
                    api_key=os.environ["CRUSOE_API_KEY"],
                    max_retries=0,
                    timeout=300,
                )
    return _client


def _get(obj, name, default=None):
    """Attribute or dict key (openai 1.45 keeps unknown usage fields as plain dicts)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ---------- per-model throttle ----------

class Throttle:
    """Sliding 60 s window of request timestamps per model."""

    def __init__(self, default_rpm: int = DEFAULT_RPM):
        self._lock = threading.Lock()
        self._sent = defaultdict(deque)
        self.rpm: dict[str, int] = {}
        self.default = default_rpm

    def limit(self, model: str) -> int:
        return self.rpm.get(model, self.default)

    def learn(self, model: str, value) -> None:
        if value is None:
            return
        try:
            n = int(float(str(value).strip()))
        except (TypeError, ValueError):
            return
        if n > 0:
            with self._lock:
                self.rpm[model] = n

    def wait(self, model: str) -> None:
        """Block until a request to `model` fits under its limit, then reserve the slot."""
        while True:
            with self._lock:
                q, now = self._sent[model], _now()
                while q and now - q[0] >= WINDOW_S:
                    q.popleft()
                if len(q) < self.limit(model):
                    q.append(now)
                    return
                delay = WINDOW_S - (now - q[0])
            _sleep(max(delay, 0.0) + 0.05)

    def reset(self) -> None:
        with self._lock:
            self._sent.clear()
            self.rpm.clear()


THROTTLE = Throttle()


# ---------- usage totals ----------

_ZERO = {"calls": 0, "prompt": 0, "cached": 0, "completion": 0, "usd": 0.0, "latency_s": 0.0}


def cost_usd(model: str, prompt: int, cached: int, completion: int) -> float:
    price = PRICES.get(model)
    if not price:
        return 0.0
    inp, out, cached_price = price
    uncached = max(prompt - cached, 0)
    return (uncached * inp + cached * cached_price + completion * out) / 1e6


class Usage:
    """Token and USD totals per role."""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_role: dict[str, dict] = {}

    def add(self, role: str, model: str, usage, latency_s: float | None = None) -> dict:
        prompt = int(_get(usage, "prompt_tokens", 0) or 0)
        completion = int(_get(usage, "completion_tokens", 0) or 0)
        cached = int(_get(_get(usage, "prompt_tokens_details"), "cached_tokens", 0) or 0)
        usd = cost_usd(model, prompt, cached, completion)
        with self._lock:
            r = self._by_role.setdefault(role, dict(_ZERO))
            r["calls"] += 1
            r["prompt"] += prompt
            r["cached"] += cached
            r["completion"] += completion
            r["usd"] += usd
            r["latency_s"] += latency_s or 0.0
        rec = {"role": role, "prompt": prompt, "cached": cached, "completion": completion,
               "model": model, "usd": usd}
        if latency_s is not None:
            rec["latency_s"] = round(latency_s, 4)
        return rec

    def _totals(self, role=None) -> dict:
        with self._lock:
            if role is not None:
                return dict(self._by_role.get(role, _ZERO))
            t = dict(_ZERO)
            for r in self._by_role.values():
                for k in t:
                    t[k] += r[k]
            return t

    def snapshot(self) -> dict:
        with self._lock:
            out = {role: dict(r) for role, r in self._by_role.items()}
        total = dict(_ZERO)
        for r in out.values():
            for k in total:
                total[k] += r[k]
        out["total"] = total
        for r in out.values():
            r["cache_hit_rate"] = r["cached"] / r["prompt"] if r["prompt"] else 0.0
        return out

    def cache_hit_rate(self, role=None) -> float:
        t = self._totals(role)
        return t["cached"] / t["prompt"] if t["prompt"] else 0.0

    def usd(self, role=None) -> float:
        return self._totals(role)["usd"]

    def reset(self) -> None:
        with self._lock:
            self._by_role.clear()


USAGE = Usage()


# ---------- retries ----------

def _retry_after(err) -> float | None:
    resp = getattr(err, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    ms = headers.get("retry-after-ms")
    if ms:
        try:
            return float(ms) / 1000.0
        except ValueError:
            pass
    ra = headers.get("retry-after")
    if not ra:
        return None
    try:
        return float(ra)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(ra)
        return max(dt.timestamp() - time.time(), 0.0)
    except (TypeError, ValueError):
        return None


def backoff_delay(attempt: int, err=None) -> float:
    ra = _retry_after(err) if err is not None else None
    if ra is not None:
        return min(MAX_BACKOFF_S, max(ra, 0.0) + random.uniform(0, 0.5))
    base = min(MAX_BACKOFF_S, 2.0 ** attempt)
    return min(MAX_BACKOFF_S, base + random.uniform(0, base * 0.25 + 0.5))


def _request(model: str, messages: list, max_tokens: int, max_tries: int, kwargs: dict):
    """Returns (parsed response, seconds the successful attempt took; excludes throttle and backoff)."""
    tries = max(1, int(max_tries))
    for attempt in range(tries):
        THROTTLE.wait(model)
        t0 = _clock()
        try:
            raw = client().chat.completions.with_raw_response.create(
                model=model, messages=messages, max_tokens=max_tokens, **kwargs
            )
        except RETRYABLE as e:
            resp = getattr(e, "response", None)
            if resp is not None:
                THROTTLE.learn(model, resp.headers.get("x-ratelimit-limit-requests"))
            if attempt == tries - 1:
                raise
            delay = backoff_delay(attempt, e)
            _logger.warning("%s: %s (attempt %d/%d), retrying in %.1fs",
                            model, type(e).__name__, attempt + 1, tries, delay)
            _sleep(delay)
            continue
        THROTTLE.learn(model, raw.headers.get("x-ratelimit-limit-requests"))
        resp = raw.parse()
        return resp, _clock() - t0
    raise RuntimeError("unreachable")


# ---------- public API ----------

def chat(role: str, messages: list, max_tokens: int = 2048, *, log=None, actor=None,
         thinking=None, max_tries: int = 8, **kwargs):
    """Call the model assigned to `role` (or a raw model ID). Returns the parsed ChatCompletion.

    Reasoning models spend max_tokens on thinking, so keep it >= 1024.
    log: an EventLog; one `usage` event is emitted per API response (tokens, usd, latency_s).
    thinking: True/False sets chat_template_kwargs.enable_thinking on nvidia/* models.
    """
    model = ROLES.get(role, role)
    if thinking is not None and model.startswith("nvidia/"):
        extra = dict(kwargs.pop("extra_body", None) or {})
        ctk = dict(extra.get("chat_template_kwargs") or {})
        ctk["enable_thinking"] = bool(thinking)
        extra["chat_template_kwargs"] = ctk
        kwargs["extra_body"] = extra
    actor = actor or role

    retried_empty = False
    while True:
        resp, latency = _request(model, messages, max_tokens, max_tries, kwargs)
        rec = USAGE.add(role, model, getattr(resp, "usage", None), latency)
        if log is not None:
            try:
                log.emit(actor, "usage", rec)
            except Exception as e:  # never let logging break a model call
                _logger.warning("usage event emit failed: %s", e)
        if not _ran_out_of_tokens(resp):
            return resp
        new_max = min(max_tokens * 2, MAX_TOKENS_CAP)
        if retried_empty or new_max <= max_tokens:
            _logger.warning("%s (role %s): reasoning model ran out of tokens at max_tokens=%d "
                            "and returned no content", model, role, max_tokens)
            return resp
        _logger.warning("%s (role %s): reasoning model ran out of tokens at max_tokens=%d and "
                        "returned no content; retrying with max_tokens=%d",
                        model, role, max_tokens, new_max)
        max_tokens = new_max
        retried_empty = True


def _ran_out_of_tokens(resp) -> bool:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return False
    c = choices[0]
    msg = getattr(c, "message", None)
    return (getattr(c, "finish_reason", None) == "length"
            and not (getattr(msg, "content", None) or "").strip()
            and not (getattr(msg, "tool_calls", None) or []))


def _message(resp):
    choices = getattr(resp, "choices", None) or []
    return getattr(choices[0], "message", None) if choices else None


def text(resp) -> str:
    return getattr(_message(resp), "content", None) or ""


def tool_calls(resp) -> list:
    return list(getattr(_message(resp), "tool_calls", None) or [])


def reasoning(resp) -> str:
    m = _message(resp)
    if m is None:
        return ""
    for name in ("reasoning_content", "reasoning"):
        v = getattr(m, name, None)
        if v:
            return v if isinstance(v, str) else str(v)
    extra = getattr(m, "model_extra", None) or {}
    v = extra.get("reasoning_content") or extra.get("reasoning") or ""
    return v if isinstance(v, str) else str(v)

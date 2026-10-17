"""Crusoe Managed Inference client with jittered backoff for 429/503."""
import os
import random
import time

from openai import InternalServerError, OpenAI, RateLimitError

from gridtwin.config import BASE_URL, ROLES

_client = None


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=BASE_URL, api_key=os.environ["CRUSOE_API_KEY"])
    return _client


def chat(role: str, messages: list, max_tokens: int = 2048, max_tries: int = 6, **kwargs):
    """Call the model assigned to `role`. Reasoning models spend max_tokens on thinking, so keep it >= 1024."""
    model = ROLES.get(role, role)
    for attempt in range(max_tries):
        try:
            return client().chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, **kwargs
            )
        except (RateLimitError, InternalServerError):
            if attempt == max_tries - 1:
                raise
            time.sleep(min(60.0, 2.0**attempt) + random.uniform(0, 0.5))


def text(resp) -> str:
    return resp.choices[0].message.content or ""

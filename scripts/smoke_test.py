"""Check the Crusoe key, confirm every role's model ID exists, and ping each one.

Run: python -m scripts.smoke_test
"""
import os
import sys

from gridtwin.config import ROLES
from gridtwin.llm import chat, client, text

if not os.environ.get("CRUSOE_API_KEY"):
    sys.exit("CRUSOE_API_KEY not set (copy .env.example to .env)")

available = {m.id for m in client().models.list().data}
print(f"{len(available)} models available")
ok = True
for role, model in ROLES.items():
    if model not in available:
        close = [m for m in available if model.split("/")[-1].lower()[:12] in m.lower()]
        print(f"MISSING {role}: {model}  (similar: {close})")
        ok = False
        continue
    resp = chat(role, [{"role": "user", "content": "Reply with exactly: pong"}], max_tokens=1024)
    print(f"ok {role:12s} {model:50s} -> {text(resp).strip()[:40]!r}")

if os.environ.get("YDC_API_KEY"):
    import httpx

    r = httpx.get(
        "https://ydc-index.io/v1/search",
        params={"query": "solar inverter ground fault troubleshooting", "count": 2},
        headers={"X-API-Key": os.environ["YDC_API_KEY"]},
        timeout=30,
    )
    print(f"you.com search: HTTP {r.status_code}")
else:
    print("YDC_API_KEY not set, skipping You.com check")

sys.exit(0 if ok else 1)

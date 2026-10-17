"""Push a page to a human through ntfy.sh. Used as the Toolbox notifier for page_human.

Set NTFY_TOPIC to enable it. The topic is a secret-ish address, so it is never logged.
"""
from __future__ import annotations

import os
import re

import httpx

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional at runtime
    pass

NTFY_URL = "https://ntfy.sh"
PRIORITY = {"critical": "5", "high": "4", "medium": "3", "low": "2"}
TAGS = {"critical": "rotating_light,zap", "high": "warning,zap", "medium": "zap", "low": "information_source"}


def _severity(severity) -> str:
    s = re.sub(r"[^a-z0-9_-]", "", str(severity or "").lower())
    return s or "medium"


def ntfy(severity, brief, topic: str | None = None) -> bool:
    """POST the brief to ntfy.sh/<topic>. Returns True on a 2xx response. Never raises."""
    try:
        topic = topic or os.environ.get("NTFY_TOPIC")
        if not topic:
            return False
        sev = _severity(severity)
        headers = {
            "Title": f"GridTwin {sev}: plant needs a human",
            "Priority": PRIORITY.get(sev, "3"),
            "Tags": TAGS.get(sev, "zap"),
        }
        body = str(brief if brief is not None else "")[:2000].encode("utf-8")
        r = httpx.post(f"{NTFY_URL}/{topic}", content=body, headers=headers, timeout=10)
        return 200 <= r.status_code < 300
    except Exception:
        return False


def make_notifier():
    """notifier(severity, brief) -> bool bound to NTFY_TOPIC, or None when it is unset."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return None

    def notifier(severity, brief) -> bool:
        return ntfy(severity, brief, topic=topic)

    return notifier

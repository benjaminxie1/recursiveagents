import httpx

from gridtwin import notify


class Resp:
    def __init__(self, status):
        self.status_code = status


def test_ntfy_sends_headers(monkeypatch):
    seen = {}

    def fake_post(url, content=None, headers=None, timeout=None):
        seen.update(url=url, content=content, headers=headers, timeout=timeout)
        return Resp(200)

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    assert notify.ntfy("critical", "x" * 3000, topic="gt-test") is True
    assert seen["url"] == "https://ntfy.sh/gt-test" and seen["timeout"] == 10
    assert seen["content"] == b"x" * 2000
    h = seen["headers"]
    assert h["Title"] == "GridTwin critical: plant needs a human"
    assert h["Priority"] == "5" and h["Tags"]
    for sev, pri in (("high", "4"), ("medium", "3"), ("low", "2"), ("weird", "3")):
        notify.ntfy(sev, "b", topic="gt-test")
        assert seen["headers"]["Priority"] == pri


def test_ntfy_never_raises(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(notify.httpx, "post", boom)
    assert notify.ntfy("high", "brief", topic="t") is False
    monkeypatch.setattr(notify.httpx, "post", lambda *a, **k: Resp(500))
    assert notify.ntfy("high", "brief", topic="t") is False
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert notify.ntfy("high", "brief") is False


def test_make_notifier(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert notify.make_notifier() is None
    monkeypatch.setenv("NTFY_TOPIC", "gt-topic")
    sent = []
    monkeypatch.setattr(notify.httpx, "post", lambda url, **k: sent.append(url) or Resp(200))
    n = notify.make_notifier()
    assert callable(n) and n("medium", "brief") is True
    assert sent == ["https://ntfy.sh/gt-topic"]

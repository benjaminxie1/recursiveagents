from gridtwin.agents.board import BOARD_TOOLS, Board


class FakeLog:
    def __init__(self):
        self.events = []

    def emit(self, actor, type, data):
        ev = {"seq": len(self.events) + 1, "t_sim": "2026-07-14T09:42", "wall_ts": 0.0,
              "actor": actor, "type": type, "data": data}
        self.events.append(ev)
        return ev


def test_post_emits_finding_event():
    log = FakeLog()
    b = Board(log)
    p = b.post("thermal", "Hot spot on combiner 3", [4, "7", 2.0, "x", None, True, 3.5])
    assert len(log.events) == 1
    ev = log.events[0]
    assert ev["actor"] == "worker:thermal" and ev["type"] == "finding"
    assert ev["data"] == {"worker": "thermal", "text": "Hot spot on combiner 3", "evidence": [4, 7, 2]}
    assert p == {"id": 1, "worker": "thermal", "text": "Hot spot on combiner 3", "evidence": [4, 7, 2],
                 "seq": 1, "t_sim": "2026-07-14T09:42"}


def test_text_capped_and_bad_evidence():
    log = FakeLog()
    b = Board(log)
    p = b.post("w", "a" * 5000, "not-a-list")
    assert len(p["text"]) == 1000 and p["evidence"] == []
    assert b.post("w", "x", 12)["evidence"] == [12]
    assert b.post("w", "x", None)["evidence"] == []


def test_read_returns_copies():
    log = FakeLog()
    b = Board(log)
    b.post("a", "one", [1])
    b.post("b", "two", [2])
    posts = b.read()
    assert [p["text"] for p in posts] == ["one", "two"]
    assert [p["id"] for p in posts] == [1, 2]
    posts[0]["text"] = "mutated"
    posts[0]["evidence"].append(99)
    again = b.read()
    assert again[0]["text"] == "one" and again[0]["evidence"] == [1]


def test_handle_dispatch():
    log = FakeLog()
    b = Board(log)
    out = b.handle("diag", "post_finding", {"text": "IR low on feeder 2", "evidence": [5, 6]})
    assert out["worker"] == "diag" and out["evidence"] == [5, 6]
    assert log.events[-1]["actor"] == "worker:diag"
    r = b.handle("diag", "read_board", {})
    assert r == {"posts": b.read()}
    assert b.handle("diag", "read_board", None) == {"posts": b.read()}
    assert b.handle("diag", "read_telemetry", {"asset": "INV-1"}) is None
    assert "error" in b.handle("diag", "post_finding", {"evidence": [1]})


def test_works_when_emit_returns_none():
    class SilentLog:
        def emit(self, *a):
            return None

    p = Board(SilentLog()).post("w", "x", [1])
    assert p["seq"] is None and p["t_sim"] is None


def test_board_tools_schema():
    assert isinstance(BOARD_TOOLS, list) and len(BOARD_TOOLS) == 2
    by_name = {}
    for t in BOARD_TOOLS:
        assert t["type"] == "function"
        f = t["function"]
        assert isinstance(f["name"], str) and isinstance(f["description"], str) and f["description"]
        assert f["parameters"]["type"] == "object"
        by_name[f["name"]] = f
    assert set(by_name) == {"post_finding", "read_board"}
    pf = by_name["post_finding"]["parameters"]
    assert pf["properties"]["text"]["type"] == "string"
    assert pf["properties"]["evidence"]["type"] == "array"
    assert pf["properties"]["evidence"]["items"]["type"] == "integer"
    assert set(pf["required"]) == {"text", "evidence"}
    assert by_name["read_board"]["parameters"]["properties"] == {}
    assert "read the board" in by_name["read_board"]["description"].lower()
    assert "seq" in by_name["post_finding"]["description"].lower()


def test_handle_never_raises_when_emit_fails():
    class BrokenLog:
        def emit(self, *a):
            raise OSError("disk full")

    b = Board(BrokenLog())
    out = b.handle("diag", "post_finding", {"text": "x", "evidence": [1]})
    assert "error" in out and "disk full" in out["error"]
    assert b.read() == []  # no post without its finding event


def test_emitted_evidence_not_aliased_to_post():
    log = FakeLog()
    b = Board(log)
    b.post("w", "x", [1])
    log.events[0]["data"]["evidence"].append(42)
    assert b.read()[0]["evidence"] == [1]

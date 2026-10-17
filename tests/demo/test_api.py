"""The demo HTTP API a separate frontend binds to (gridtwin/demo/serve.py, docs/FRONTEND-API.md).

Starts the real server on a free port, starts a stub run through POST /api/demo/run (a real subprocess, no model
calls), follows it over Server-Sent Events, and reads the snapshot, as a page on another localhost port would.
"""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from gridtwin.demo import serve as S
from gridtwin.demo import view

FRONTEND = "http://localhost:5173"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    runs = tmp_path_factory.mktemp("api") / "runs"
    runs.mkdir()
    mp = pytest.MonkeyPatch()
    mp.setattr(S, "RUNS", runs)
    mp.setattr(S, "LOGS", runs / "_demo-logs")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", runs
    srv.shutdown()
    mp.undo()


def call(base, path, method="GET", body=None, origin=FRONTEND, timeout=30):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Origin": origin, **({"Content-Type": "application/json"} if body is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def sse(raw: bytes) -> list[tuple[str, dict]]:
    out = []
    for block in raw.decode().split("\n\n"):
        name = next((ln[7:] for ln in block.splitlines() if ln.startswith("event: ")), None)
        data = next((ln[6:] for ln in block.splitlines() if ln.startswith("data: ")), None)
        if name and data is not None:
            out.append((name, json.loads(data)))
    return out


@pytest.fixture(scope="module")
def stub_run(server):
    base, runs = server
    code, headers, raw = call(base, "/api/demo/run", "POST", {"stub": True, "memory": False})
    assert code == 200, raw
    started = json.loads(raw)
    assert headers.get("Access-Control-Allow-Origin") == FRONTEND
    code, _, raw = call(base, started["urls"]["stream"], timeout=120)  # returns when the run ends
    assert code == 200
    return started, sse(raw)


def test_health_reports_keys_as_booleans_only(server):
    base, _ = server
    code, headers, raw = call(base, "/api/demo/health")
    h = json.loads(raw)
    assert code == 200 and h["ok"] and h["schema"] == view.SCHEMA
    assert all(isinstance(v, bool) for v in h["keys"].values())
    assert h["diffusion"] in ("diffusion", "photo_bank")
    assert headers.get("Access-Control-Allow-Origin") == FRONTEND


def test_preflight_allows_local_frontends_and_refuses_others(server):
    base, _ = server
    assert call(base, "/api/demo/run", "OPTIONS")[0] == 204
    code, headers, _ = call(base, "/api/demo/run", "OPTIONS", origin="https://evil.example")
    assert code == 403 and "Access-Control-Allow-Origin" not in headers
    code, _, raw = call(base, "/api/demo/run", "POST", {"stub": True}, origin="https://evil.example")
    assert code == 403, raw


def test_bad_input_is_rejected(server):
    base, _ = server
    assert call(base, "/api/demo/run", "POST", {"scenario": "../../etc"})[0] == 400
    assert call(base, "/api/demo/runs/..%2F..%2Fetc")[0] == 404
    assert call(base, "/api/demo/runs/.hidden")[0] == 404
    assert call(base, "/api/demo/memory?kb=../x")[0] == 400


def test_stream_carries_every_event_then_the_final_snapshot(stub_run):
    started, stream = stub_run
    demo = [d for name, d in stream if name == "demo"]
    seqs = [e["seq"] for e in demo]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs) and seqs[0] == 1
    assert stream[-1][0] == "end"
    snap = stream[-1][1]
    assert snap["run_id"] == started["run_id"] and snap["status"] == "VERIFIED" and snap["done"] is True
    assert snap["last_seq"] == seqs[-1]


def test_snapshot_has_every_stage_of_the_story(server, stub_run):
    base, _ = server
    started, _ = stub_run
    code, _, raw = call(base, started["urls"]["snapshot"])
    s = json.loads(raw)
    assert code == 200 and s["schema"] == view.SCHEMA and s["status"] == "VERIFIED" and s["stub"] is True
    assert {r["stage"] for r in s["stages"]} >= set(view.STAGES) - {"redispatch"} or {r["stage"] for r in s["stages"]} >= set(view.STAGES)
    assert s["diagnosis"]["asset"] == "INV-3" and s["sensors"] and s["cameras"]
    assert s["rounds"] == 2 and [v["pass"] for v in s["verdicts"]] == [False, True]
    assert [e["direction"] for e in s["emails"]][:2] == ["out", "in"]
    assert any(c["kind"] == "jev" for c in s["checks"]) and s["jev"] and s["diffusion"]
    assert s["contractor_visits"][0]["shortcut"] is True
    # one level of recursion, visible as teams of a lead and its sub-agents
    assert all(a["depth"] in (0, 1) for a in s["agents"])
    plan = next(t for t in s["teams"] if t["team"] == "plan")
    assert sorted(m.split(".")[-1] for m in plan["members"]) == ["acceptance", "repair", "safety"]
    assert {(t["team"], t["round"]) for t in s["teams"]} >= {("diagnose", 0), ("plan", 0), ("verify", 1), ("verify", 2)}
    assert s["memory"]["stats"] is not None and s["final"]["status"] == "VERIFIED"


def test_events_endpoint_pages_by_seq(server, stub_run):
    base, _ = server
    started, stream = stub_run
    rid = started["run_id"]
    first = json.loads(call(base, f"/api/demo/runs/{rid}/events?after=0")[2])
    assert first["done"] is True and first["last_seq"] == stream[-1][1]["last_seq"]
    mid = first["events"][len(first["events"]) // 2]["seq"]
    rest = json.loads(call(base, f"/api/demo/runs/{rid}/events?after={mid}")[2])
    assert rest["events"][0]["seq"] == mid + 1 and rest["last_seq"] == first["last_seq"]


def test_runs_list_and_memory_view(server, stub_run):
    base, _ = server
    started, _ = stub_run
    rows = json.loads(call(base, "/api/demo/runs")[2])
    row = next(r for r in rows if r["id"] == started["run_id"])
    assert row["status"] == "VERIFIED" and row["rounds"] == 2 and row["stub"] is True
    assert started["kb"] == f"_nomem-{started['run_id']}" and started["urls"]["memory"].endswith(started["kb"])
    mem = json.loads(call(base, started["urls"]["memory"])[2])
    assert mem["exists"] and mem["lessons"] and mem["stats"]["runs"] == 1
    assert json.loads(call(base, "/api/demo/memory?kb=no-such-kb")[2])["exists"] is False


def test_a_run_that_dies_shows_failed_not_running(server, tmp_path):
    base, runs = server
    d = runs / "dead-run"
    (d / "mail").mkdir(parents=True)
    (d / "events.jsonl").write_text(json.dumps({"seq": 1, "type": "goal", "actor": "operator", "data": {}}) + "\n")

    class Done:
        def poll(self):
            return 1
    S.PROCS["dead-run"] = Done()
    try:
        s = json.loads(call(base, "/api/demo/runs/dead-run")[2])
        assert s["status"] == "FAILED" and s["done"] and "code 1" in s["error"]
        code, _, raw = call(base, "/api/demo/runs/dead-run/stream", timeout=10)
        assert sse(raw)[-1][0] == "end" and sse(raw)[-1][1]["status"] == "FAILED"
    finally:
        S.PROCS.pop("dead-run", None)


# ---------- from the independent frontend check (15:30) ----------

def test_body_and_scenario_are_validated_before_anything_starts(server):
    base, _ = server
    for body, why in (({"Stub": True}, "unknown keys"), ({"stub": "false"}, "stub must be a boolean"),
                      ({"scenario": "s01_ground_fault", "stub": True}, "not one the demo supports"),
                      ({"scenario": "s99_nope", "stub": True}, "not one the demo supports")):
        code, _, raw = call(base, "/api/demo/run", "POST", body)
        assert code == 400 and why in json.loads(raw)["error"], (body, raw)
    code, _, raw = call(base, "/api/demo/run", "POST", [1, 2])
    assert code == 400 and "JSON object" in json.loads(raw)["error"]
    assert "s06_stale_scada" in json.loads(call(base, "/api/demo/health")[2])["scenarios"]


def test_two_posts_in_the_same_second_get_two_runs(server):
    base, _ = server
    ids = []

    def post():
        ids.append(json.loads(call(base, "/api/demo/run", "POST", {"stub": True, "memory": False})[2])["run_id"])
    threads = [threading.Thread(target=post) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(ids)) == 3, ids
    listed = {r["id"] for r in json.loads(call(base, "/api/demo/runs")[2])}
    assert set(ids) <= listed  # a run is listed the moment it is started
    for rid in ids:  # let them finish so the module's tmp dir is quiet
        call(base, f"/api/demo/runs/{rid}/stream", timeout=120)


def test_snapshot_memory_stats_is_flat_and_lists_carry_seq(server, stub_run):
    base, _ = server
    s = json.loads(call(base, stub_run[0]["urls"]["snapshot"])[2])
    st = s["memory"]["stats"]
    assert isinstance(st["lessons"], int) and st["rounds_by_run"] and "effect" in st and "seq" in st
    assert all("seq" in r for r in s["stages"]) and all("seq" in t for t in s["teams"])


def test_stream_reconnect_after_end_gets_204_and_junk_ids_start_over(server, stub_run):
    base, _ = server
    started, stream = stub_run
    last = stream[-1][1]["last_seq"]
    req = urllib.request.Request(base + started["urls"]["stream"], headers={"Origin": FRONTEND, "Last-Event-ID": str(last)})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 204
    req = urllib.request.Request(base + started["urls"]["stream"], headers={"Origin": FRONTEND, "Last-Event-ID": "abc"})
    with urllib.request.urlopen(req, timeout=30) as r:
        frames = sse(r.read())
    assert frames[0][0] == "demo" and frames[0][1]["seq"] == 1 and frames[-1][0] == "end"
    assert call(base, f"/api/demo/runs/{started['run_id']}/events?after=abc")[0] == 400


def test_a_run_whose_process_is_gone_after_a_restart_shows_failed(server):
    base, runs = server
    d = runs / "orphan-run"
    (d / "mail").mkdir(parents=True)
    (d / "events.jsonl").write_text(json.dumps({"seq": 1, "type": "goal", "actor": "operator", "data": {}}) + "\n")
    (d / S.PID_FILE).write_text("999999")  # no such process; not in PROCS, as after a server restart
    s = json.loads(call(base, "/api/demo/runs/orphan-run")[2])
    assert s["status"] == "FAILED" and s["done"] and "ended without a result" in s["error"]

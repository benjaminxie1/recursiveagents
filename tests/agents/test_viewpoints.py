"""The verifier takes each visual check from a named viewpoint no worker called, and the verdict says which."""
from types import SimpleNamespace

import pytest

from gridtwin import llm
from gridtwin.agents import verifier as V

GOAL = "INV-1 is derating on heat. Fix it."
INV1 = ["inv1_cabinet", "inv1_intake", "inv1_rear"]
REGISTRY = {"assets": [{"asset": "INV-1", "type": "inverter", "points": ["ac_kw"], "viewpoints": INV1},
                       {"asset": "XFMR-1", "type": "transformer", "points": [], "viewpoints": ["xfmr1_yard"]}]}
CHECK = {"id": "vis", "kind": "visual", "viewpoint": "inv1_cabinet", "mode": "thermal", "claim": "The inverter cabinet is not hot"}


class Log:
    def __init__(self):
        self.events = []

    def emit(self, actor, type, data):
        e = {"seq": len(self.events) + 1, "actor": actor, "type": type, "data": data}
        self.events.append(e)
        return e


class SimToolbox:
    """Logs every call as actor "sim", as gridtwin/sim/tools.py does, so the actor can't tell worker from verifier."""

    def __init__(self, log):
        self.log = log

    def call(self, name, args):
        self.log.emit("sim", "tool_call", {"name": name, "args": args})
        r = {"ok": True}
        if name == "get_camera_view":
            r = {"image_path": f"runs/x/frames/{args['viewpoint']}.jpg", "viewpoint": args["viewpoint"], "asset": "INV-1",
                 "mode": args.get("mode", "rgb"), "captured_at": "2026-07-14T12:00"}
        self.log.emit("sim", "tool_result", {"name": name, "result": r})
        return r


@pytest.fixture
def prompts(monkeypatch):
    seen = []

    def fake_chat(role, messages, max_tokens=2048, **kw):
        seen.append(messages[-1]["content"])
        msg = SimpleNamespace(content='{"add": [], "pass": true, "reason": "ok"}', tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(V, "_vision_check", lambda path, claim: {"pass": True, "confidence": 0.9, "evidence": "no hot spots"})
    return seen


def camera_calls(log):
    return [e["data"]["args"]["viewpoint"] for e in log.events if e["type"] == "tool_call" and e["data"]["name"] == "get_camera_view"]


def verdict_evidence(log):
    return [e["data"]["evidence"] for e in log.events if e["type"] == "verdict"]


def verify(log, registry=REGISTRY):
    return V.Verifier(SimToolbox(log), log, None, registry)


def test_visual_check_uses_a_viewpoint_no_worker_called(prompts):
    log = Log()
    tb = SimToolbox(log)
    tb.call("get_camera_view", {"viewpoint": "inv1_cabinet", "mode": "thermal"})  # the inspector's look
    out = verify(log).verify(GOAL, "DONE", [CHECK], audit=False)
    assert camera_calls(log) == ["inv1_cabinet", "inv1_intake"]
    [ev] = verdict_evidence(log)
    assert ev["viewpoint"] == "inv1_intake" and ev["mode"] == "thermal"
    assert ev["dod_viewpoint"] == "inv1_cabinet" and ev["worker_viewpoints"] == ["inv1_cabinet"]
    assert "viewpoint_fallback" not in ev
    assert out["verdicts"][0]["evidence"] == ev and out["pass"]
    assert out["checks"][0]["viewpoint"] == "inv1_cabinet"  # the DoD itself is unchanged
    assert any("viewpoint inv1_intake (thermal)" in p for p in prompts)  # the model is told where the photo came from


def test_reverify_keeps_its_own_viewpoint(prompts):
    log = Log()
    SimToolbox(log).call("get_camera_view", {"viewpoint": "inv1_cabinet", "mode": "thermal"})
    v = verify(log)
    v.verify(GOAL, "DONE", [CHECK], audit=False)
    v.verify(GOAL, "DONE", [CHECK], audit=False)  # the second pass after the hold
    assert camera_calls(log) == ["inv1_cabinet", "inv1_intake", "inv1_intake"]
    assert [ev["worker_viewpoints"] for ev in verdict_evidence(log)] == [["inv1_cabinet"], ["inv1_cabinet"]]


def test_unused_dod_viewpoint_is_kept(prompts):
    log = Log()
    verify(log).verify(GOAL, "DONE", [CHECK], audit=False)
    [ev] = verdict_evidence(log)
    assert ev["viewpoint"] == "inv1_cabinet" and ev["worker_viewpoints"] == []


def test_every_viewpoint_called_falls_back_and_says_so(prompts):
    log = Log()
    for vp in INV1:
        SimToolbox(log).call("get_camera_view", {"viewpoint": vp, "mode": "thermal"})
    verify(log).verify(GOAL, "DONE", [CHECK], audit=False)
    [ev] = verdict_evidence(log)
    assert ev["viewpoint"] == "inv1_cabinet" and ev["worker_viewpoints"] == INV1
    assert ev["viewpoint_fallback"]


def test_viewpoints_without_the_mode_are_skipped(prompts):
    registry = {"assets": [{"id": "INV-1"}],
                "viewpoints": [{"id": "inv1_cabinet", "asset": "INV-1", "modes": ["rgb", "thermal"]},
                               {"id": "inv1_intake", "asset": "INV-1", "modes": ["rgb"]},
                               {"id": "inv1_rear", "asset": "INV-1", "modes": ["rgb", "thermal"]}]}
    log = Log()
    SimToolbox(log).call("get_camera_view", {"viewpoint": "inv1_cabinet", "mode": "thermal"})
    verify(log, registry).verify(GOAL, "DONE", [CHECK], audit=False)
    [ev] = verdict_evidence(log)
    assert ev["viewpoint"] == "inv1_rear"


def test_checks_off_the_registry_run_as_written(prompts):
    log = Log()
    chk = dict(CHECK, viewpoint="vp")
    verify(log).verify(GOAL, "DONE", [chk], audit=False)
    [ev] = verdict_evidence(log)
    assert ev["viewpoint"] == "vp" and "dod_viewpoint" not in ev

"""Loop-level guarantees: verifier isolation, no truth access, no fault names in prompts, lesson provenance."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from gridtwin import llm

ROOT = Path(__file__).resolve().parents[2]
CANARY = "CANARY-WORKER-TEXT-7f3a"


class FakeLog:
    def __init__(self, run_dir):
        self.run_dir = Path(run_dir)
        self.events = []

    def emit(self, actor, type, data):
        e = {"seq": len(self.events) + 1, "t_sim": "2026-07-14T12:00", "wall_ts": 0.0, "actor": actor, "type": type, "data": data}
        self.events.append(e)
        return e


class FakeToolbox:
    def __init__(self, log):
        self.log = log
        self.gate = "set"
        self.calls = 0

    def call(self, name, args):
        self.calls += 1
        self.log.emit("sim", "tool_call", {"name": name, "args": args})
        if name == "list_assets":
            r = {"rating_kwac": 5000, "now": "2026-07-14T12:00",
                 "assets": [{"asset": f"INV-{i}", "type": "inverter", "rating_kwac": 1250, "points": ["ac_kw"], "viewpoints": [f"inv{i}_cabinet"]}
                            for i in range(1, 3)] + [{"asset": "POI-1", "type": "revenue_meter", "points": ["export_kw"], "viewpoints": []}]}
        elif name == "read_telemetry":
            pts = {p: [["2026-07-14T11:59", 1000.0], ["2026-07-14T12:00", 1000.0]] for p in (args.get("points") or ["ac_kw"])}
            if args["asset"] == "POI-1":
                pts = {"export_kw": [["2026-07-14T12:00", 1990.0]]}
            r = {"asset": args["asset"], "now": "2026-07-14T12:00", "last_update_ts": "2026-07-14T12:00", "points": pts}
        elif name == "list_alarms":
            r = {"alarms": []}
        elif name == "get_camera_view":
            r = {"image_path": "runs/x/frames/1-inv1_cabinet-rgb.jpg", "viewpoint": args["viewpoint"], "asset": "INV-1",
                 "mode": args.get("mode", "rgb"), "captured_at": "2026-07-14T12:00"}
        elif name == "wait":
            r = {"now": "2026-07-14T12:30"}
        else:
            r = {"ok": True}
        self.log.emit("sim", "tool_result", {"name": name, "result": r})
        return r


def _fake_resp(content):
    msg = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])


def test_verifier_model_never_sees_worker_text(tmp_path, monkeypatch):
    from gridtwin.agents import dod as D
    from gridtwin.agents import verifier as V
    from gridtwin.agents.board import Board

    log = FakeLog(tmp_path)
    tb = FakeToolbox(log)
    # the team has been talking: board posts, a claim, worker histories in the log
    board = Board(log)
    board.post("diagnoser", f"{CANARY} INV-1 looks fine to me", [1])
    log.emit("worker:operator", "claim", {"status": "DONE", "summary": f"{CANARY} fixed everything"})
    log.emit("worker:inspector", "usage", {"role": "worker", "prompt": 1, "cached": 0, "completion": 1})

    seen = []

    def fake_chat(role, messages, max_tokens=2048, **kw):
        seen.append(json.dumps(messages))
        return _fake_resp('{"add": [], "pass": true, "reason": "ok"}')

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(V, "_vision_check", lambda p, c: {"pass": True, "confidence": 0.9, "evidence": "display lit"})
    if hasattr(D, "default_visual_fn"):
        monkeypatch.setattr(D, "default_visual_fn", lambda: (lambda p, c: {"pass": True, "confidence": 0.9, "evidence": "display lit"}))

    checks = [
        {"id": "fresh", "kind": "telemetry", "op": "fresh", "assets": "INV-*", "max_age_min": 5,
         "desc": f"{CANARY} smuggled into a description"},
        {"id": "paged", "kind": "safety", "rule": "no_dispatch", "params": {}},
        {"id": "vis", "kind": "visual", "viewpoint": "inv1_cabinet", "mode": "rgb", "claim": "The inverter display is lit"},
    ]
    v = V.Verifier(tb, log, tmp_path / "kb")
    out = v.verify("Revenue is down, find out why and fix it.", "DONE", checks)
    assert seen, "the verifier model was never called"
    assert all(CANARY not in s for s in seen), "worker/board/claim text reached the verifier model"
    assert len([e for e in log.events if e["type"] == "verdict"]) == len(out["verdicts"]) >= 3


def test_verifier_api_takes_no_worker_inputs():
    import inspect

    from gridtwin.agents.verifier import Verifier

    params = set(inspect.signature(Verifier.verify).parameters)
    assert params == {"self", "goal", "outcome", "checks", "audit"}
    assert set(inspect.signature(Verifier.__init__).parameters) == {"self", "toolbox", "log", "kb_dir", "registry"}


AGENT_CODE = [p for p in (ROOT / "gridtwin" / "agents").glob("*.py")] + [ROOT / "gridtwin" / "kb.py", ROOT / "gridtwin" / "run.py"]


@pytest.mark.parametrize("path", AGENT_CODE, ids=lambda p: p.name)
def test_agent_code_never_reads_truth(path):
    src = path.read_text()
    assert "truth.jsonl" not in src
    assert not re.search(r"\.truth\b", src), f"{path.name} touches sim.truth"
    assert "gridtwin.sim.judge" not in src and "gridtwin.sim.plant" not in src


def test_no_prompt_names_a_fault_or_fix():
    from gridtwin.agents import gate, planner, supervisor, verifier, workers

    prompts = [planner.system_prompt(), planner.PLAN_REQUEST, planner.REPLAN_REQUEST, verifier.system_prompt(),
               verifier.AUDIT_REQUEST, supervisor.SYSTEM, workers.CLAIM_INSTRUCTION] + list(workers.SYSTEM.values())
    prompts += [v for k, v in vars(gate).items() if k.isupper() and isinstance(v, str)]
    try:
        from gridtwin.sim.tools import WORK_ORDERS
    except ImportError:
        WORK_ORDERS = ["reseat_comms_card", "replace_fan_filter", "transformer_oil_service"]
    for p in prompts:
        assert not re.search(r"\bF[1-8]\b", p), p[:200]
        for wo in WORK_ORDERS:
            if wo != "inspect":
                assert wo not in p, f"prompt names the fix {wo}"
        for word in ("stale_scada", "fan_failure", "comms card", "display_off"):
            assert word not in p.lower()


def test_worker_prompts_and_tools_are_constants():
    from gridtwin.agents import workers

    for name in workers.WORKERS:
        assert workers.TOOLS[name] is workers.TOOLS[name]
        assert workers.SYSTEM[name] == workers.COMMON.format(role=name, duty=workers.DUTIES[name])
    assert any(t["function"]["name"] == "search_web" for t in workers.TOOLS["researcher"])
    assert not any(t["function"]["name"] == "search_web" for t in workers.TOOLS["diagnoser"])


def test_baseline_lesson_is_unverified_and_full_loop_lesson_has_provenance(tmp_path):
    from gridtwin.agents.full import write_lesson
    from gridtwin.kb import KB

    log = FakeLog(tmp_path / "run1")
    log.emit("sim", "tool_call", {"name": "operate", "args": {"asset": "INV-1", "action": "reset_alarm"}})
    log.emit("sim", "tool_result", {"name": "operate", "result": {"asset": "INV-1", "alarms_reset": True}})
    kb = KB(tmp_path / "kb", log)
    base = write_lesson(kb, None, "Unit 1 keeps losing output", "DONE", [], [], [{"worker": "operator", "text": "derate alarm cleared"}], [], log,
                        verified=False, actor="worker:operator")
    assert base["verdict_seqs"] == [] and base.get("verified") is False
    assert "reset_alarm" in base["fix"]

    class P:
        def lesson_symptoms(self, *a):
            return ["heatsink hot, fan reads 0"]

    full = write_lesson(kb, P(), "Unit 1 keeps losing output", "DONE", [{"id": "c", "kind": "safety", "rule": "no_dispatch", "params": {}}],
                        [41, 42], [], [], log)
    assert full["verdict_seqs"] == [41, 42] and full.get("verified", True)


def test_executed_actions_pairs_calls_and_skips_refusals(tmp_path):
    from gridtwin.agents.full import executed_actions

    log = FakeLog(tmp_path)
    log.emit("sim", "tool_call", {"name": "dispatch_tech", "args": {"asset": "INV-3", "work_order": "x"}})
    log.emit("gate", "gate", {"proposed": {}, "route": "reject", "unsafe": 0.9, "reason": "energized"})
    log.emit("sim", "tool_result", {"name": "dispatch_tech", "result": {"error": "refused by safety gate: energized"}})
    log.emit("sim", "tool_call", {"name": "operate", "args": {"asset": "52-I3", "action": "open"}})
    log.emit("sim", "tool_result", {"name": "operate", "result": {"asset": "52-I3", "state": "open"}})
    acts = executed_actions(log)
    assert [a["tool"] for a in acts] == ["operate"]

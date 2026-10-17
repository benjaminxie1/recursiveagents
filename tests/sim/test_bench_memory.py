"""Bench memory modes (off / frozen / learn) and per-episode lesson ids, with no LLM calls."""
import json
from pathlib import Path

import pytest

from gridtwin import bench
from gridtwin.sim import episode
from gridtwin.sim.events import EventLog

ORDER = ["s03_fan_relapse", "s06_stale_scada"]


@pytest.fixture
def snapshot(tmp_path):
    """A KB snapshot laid out like kb/snapshots/<name>/: plant/, refs/, lessons/ with one seed lesson."""
    snap = tmp_path / "snap"
    (snap / "plant").mkdir(parents=True)
    (snap / "plant" / "profile.md").write_text("# Plant profile\n")
    (snap / "refs").mkdir()
    (snap / "lessons").mkdir()
    (snap / "lessons" / "L-seed.json").write_text(json.dumps({"id": "L-seed", "status": "active", "fix": "seed"}))
    return snap


def lesson_agent(seen):
    """scripted_correct, plus the C3 lesson contract: read every lesson in kb_dir/lessons/, write one after."""

    def run(goal, toolbox, log, budget, kb_dir):
        lessons = Path(kb_dir) / "lessons"
        found = sorted(json.loads(p.read_text())["id"] for p in lessons.glob("*.json"))
        seen.append({"lessons": found, "plant": (Path(kb_dir) / "plant" / "profile.md").exists()})
        for lid in found:
            log.emit("planner", "lesson", {"id": lid, "op": "read", "text": "retrieved"})
        result = bench.scripted_correct(goal, toolbox, log, budget, kb_dir)
        lid = f"L-{log.run_dir.name}"
        (lessons / f"{lid}.json").write_text(json.dumps({"id": lid, "status": "active", "fix": result["summary"]}))
        log.emit("planner", "lesson", {"id": lid, "op": "write", "text": result["summary"]})
        return result

    return run


def bench_rows(memory, kb, tmp_path):
    seen = []
    rows = bench.run_bench(lesson_agent(seen), ORDER, runs_dir=tmp_path / f"bench-{memory}", kb=kb, memory=memory)
    return rows, seen


def snapshot_lessons(snap):
    return sorted(p.name for p in (snap / "lessons").iterdir())


def test_memory_off_has_no_lessons(snapshot, tmp_path):
    rows, seen = bench_rows("off", snapshot, tmp_path)
    assert [s["lessons"] for s in seen] == [[], []]
    assert all(s["plant"] for s in seen)  # a copy of the snapshot, not a bare empty folder
    assert [r["lessons_read"] for r in rows] == [[], []]
    assert [r["lessons_at_start"] for r in rows] == [0, 0]
    assert rows[0]["lessons_written"] == ["L-s03_fan_relapse-r0"]
    assert snapshot_lessons(snapshot) == ["L-seed.json"]


def test_memory_frozen_discards_writes_between_episodes(snapshot, tmp_path):
    rows, seen = bench_rows("frozen", snapshot, tmp_path)
    assert [s["lessons"] for s in seen] == [["L-seed"], ["L-seed"]]
    assert [r["lessons_read"] for r in rows] == [["L-seed"], ["L-seed"]]
    assert rows[0]["lessons_written"] == ["L-s03_fan_relapse-r0"]
    assert "L-s03_fan_relapse-r0" not in rows[1]["lessons_read"]
    assert snapshot_lessons(snapshot) == ["L-seed.json"]


def test_memory_learn_carries_lessons_forward(snapshot, tmp_path):
    rows, seen = bench_rows("learn", snapshot, tmp_path)
    assert [r["scenario_id"] for r in rows] == ORDER
    assert rows[0]["lessons_read"] == ["L-seed"]
    assert rows[0]["lessons_written"] == ["L-s03_fan_relapse-r0"]
    assert rows[1]["lessons_read"] == ["L-s03_fan_relapse-r0", "L-seed"]
    assert [r["lessons_at_start"] for r in rows] == [1, 2]
    assert snapshot_lessons(snapshot) == ["L-seed.json"]
    learned = tmp_path / "bench-learn" / "kb-learned" / "lessons"
    assert sorted(p.name for p in learned.iterdir()) == [
        "L-s03_fan_relapse-r0.json", "L-s06_stale_scada-r0.json", "L-seed.json"]


def test_memory_learn_without_kb_starts_empty(tmp_path):
    rows, seen = bench_rows("learn", None, tmp_path)
    assert [s["lessons"] for s in seen] == [[], ["L-s03_fan_relapse-r0"]]
    assert all(r["success"] for r in rows)


def test_lesson_ids_dedupe_by_op(tmp_path):
    log = EventLog(tmp_path / "run")
    for op, lid in [("read", "L-a"), ("read", "L-b"), ("read", "L-a"), ("write", "L-c"),
                    ("supersede", "L-a"), ("bogus", "L-x")]:
        log.emit("planner", "lesson", {"id": lid, "op": op, "text": ""})
    assert bench.lesson_ids(log) == {"lessons_read": ["L-a", "L-b"], "lessons_written": ["L-c"],
                                     "lessons_superseded": ["L-a"]}


def test_rows_carry_tool_calls_and_scripted_success(tmp_path):
    rows = bench.run_bench(bench.scripted_correct, ORDER, runs_dir=tmp_path)
    assert all(r["success"] and r["tool_calls"] > 0 for r in rows)
    assert all(r["memory"] == "frozen" and r["lessons_read"] == [] == r["lessons_written"] for r in rows)
    tot = bench.totals(rows)
    assert tot["tool_calls"] == sum(r["tool_calls"] for r in rows)
    md = bench.to_markdown("scripted_correct", "x", rows, tot, "frozen")
    assert "| tools | lessons read | lessons written |" in md


def test_cli_scenarios_order_and_memory(snapshot, tmp_path, monkeypatch):
    monkeypatch.setattr(episode, "RUNS", tmp_path / "runs")
    order = list(reversed(ORDER))
    bench.main(["--agent", "scripted_correct", "--scenarios", ",".join(order),
                "--memory", "learn", "--kb", str(snapshot)])
    (out,) = (tmp_path / "runs").glob("bench-*")
    res = json.loads((out / "results.json").read_text())
    assert res["memory"] == "learn" and res["scenarios"] == order and res["kb"] == str(snapshot)
    assert [r["scenario_id"] for r in res["rows"]] == order
    assert all(r["tool_calls"] > 0 and r["lessons_at_start"] == 1 for r in res["rows"])
    assert "memory `learn`" in (out / "results.md").read_text()
    assert (out / "kb-learned" / "lessons" / "L-seed.json").exists()


def test_cli_single_scenario_still_works(tmp_path, monkeypatch):
    monkeypatch.setattr(episode, "RUNS", tmp_path / "runs")
    bench.main(["--agent", "scripted_correct", "--scenario", "s00_control"])
    (out,) = (tmp_path / "runs").glob("bench-*")
    res = json.loads((out / "results.json").read_text())
    assert [r["scenario_id"] for r in res["rows"]] == ["s00_control"] and res["memory"] == "frozen"


@pytest.mark.parametrize("argv", [
    ["--scenarios", "s03_fan_relapse,s99_nope"],
    ["--scenario", "s03_fan_relapse", "--scenarios", "s06_stale_scada"],
    ["--memory", "sometimes"],
    ["--kb", "/nonexistent/snapshot"],
])
def test_cli_rejects_bad_args(argv, tmp_path, monkeypatch):
    monkeypatch.setattr(episode, "RUNS", tmp_path / "runs")
    with pytest.raises(SystemExit):
        bench.main(["--agent", "scripted_correct", *argv])
    assert not (tmp_path / "runs").exists()

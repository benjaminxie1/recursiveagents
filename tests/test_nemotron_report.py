"""scripts/nemotron_report.py on a synthetic bench directory."""
import json

import pytest

from gridtwin.config import ROLES
from scripts import nemotron_report


def write_run(d, calls, success):
    d.mkdir(parents=True)
    events = [{"type": "usage", "actor": "worker:inspector", "data": c} for c in calls]
    events.append({"type": "judge", "actor": "judge", "data": {"metrics": {"success": success}}})
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")


def call(prompt, latency, completion=100, cached=0):
    return {"role": "worker", "model": ROLES["worker"], "prompt": prompt, "cached": cached,
            "completion": completion, "usd": 0.001, "latency_s": latency}


def test_report_buckets_latency_and_costs_per_finished_task(tmp_path):
    write_run(tmp_path / "s01-r0", [call(1000, 1.0), call(5000, 2.0, cached=2500)], success=True)
    write_run(tmp_path / "s02-r0", [call(20000, 4.0)], success=False)
    rep = nemotron_report.report([tmp_path])
    assert (rep["runs"], rep["finished_tasks"]) == (2, 1)
    (w,) = rep["roles"]
    assert w["calls"] == 3 and w["latency_p50_s"] == pytest.approx(2.0)
    assert w["tokens_per_s"] == pytest.approx(300 / 7.0)
    assert w["cache_hit_rate"] == pytest.approx(2500 / 26000)
    assert w["usd_per_finished_task"] == pytest.approx(0.003)
    assert w["usd_if_super_per_finished_task"] > w["usd_per_finished_task"]
    assert [w["buckets"][n]["calls"] for n in ("<4k", "4-16k", "16k+")] == [1, 1, 1]
    assert w["buckets"]["16k+"]["latency_p50_s"] == pytest.approx(4.0)
    assert "| worker |" in nemotron_report.to_text(rep)

"""Held-out trap types (docs/JUDGE_QA.md Q9): fault types that only held-out scenarios contain."""
import re
from pathlib import Path

from gridtwin import bench
from gridtwin.sim import episode

ROOT = Path(__file__).resolve().parents[2]


def test_hotspot_is_a_heldout_only_trap_type():
    held = bench.heldout_only_types()
    assert "F8_hotspot" in held
    dev_types = {f["type"] for sid in episode.list_scenarios("dev") for f in episode.load_scenario(sid).get("faults") or []}
    assert not set(held) & dev_types


def test_nothing_agents_read_was_tuned_on_the_hotspot():
    paths = [*(ROOT / "gridtwin" / "agents").glob("*.py"), *(ROOT / "gridtwin" / "agents").glob("*.md"),
             *[p for p in (ROOT / "kb").rglob("*") if p.is_file()]]
    for p in paths:
        assert not re.search(r"hot ?spot|hot cell|bypass diode", p.read_text(errors="ignore"), re.I), p


def test_bench_reports_heldout_trap_types_separately(tmp_path):
    rows = bench.run_bench(bench.scripted_trap, ["s08_hotspot", "s03_fan_relapse"], runs_dir=tmp_path)
    assert rows[0]["fault_types"] == ["F8_hotspot"]
    md = bench.to_markdown("scripted_trap", "x", rows, bench.totals(rows))
    assert "held-out trap types (F5_xfmr_incipient, F8_hotspot): success 0/1 · false done 1/1" in md

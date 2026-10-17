"""Episode lifecycle (contract C1): build sim + toolbox + log for a scenario, then judge it."""
import time
from pathlib import Path

import yaml

from gridtwin.sim.camera import REPO, Camera
from gridtwin.sim.events import EventLog
from gridtwin.sim.plant import Plant
from gridtwin.sim.tools import Toolbox

SCENARIOS = Path(__file__).parent / "scenarios"
RUNS = REPO / "runs"


def load_scenario(scenario_id: str) -> dict:
    return yaml.safe_load((SCENARIOS / f"{scenario_id}.yaml").read_text())


def list_scenarios(split: str = "all") -> list:
    out = []
    for p in sorted(SCENARIOS.glob("*.yaml")):
        sc = yaml.safe_load(p.read_text())
        if split == "all" or sc.get("split") == split:
            out.append(sc["id"])
    return out


def start(scenario_id, run_id=None, gate=None, notifier=None, runs_dir=None):
    sc = load_scenario(scenario_id)
    run_id = run_id or f"{scenario_id}-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir = Path(runs_dir or RUNS) / run_id
    camera = Camera(sc["seed"], run_dir)
    sim = Plant(sc["seed"], sc["start"], sc.get("faults", []), sc.get("weather"), sc.get("preroll_min", 120), camera)
    sim.scenario = sc
    sim.run_dir = run_dir
    log = EventLog(run_dir, clock=sim.now_str)
    toolbox = Toolbox(sim, log, gate=gate, notifier=notifier, budget=sc.get("budget"))
    return sim, toolbox, log, sc


def finish(sim, log, result) -> dict:
    from gridtwin.sim.judge import score

    metrics = score(sim, sim.scenario, log, result)
    log.emit("judge", "judge", {"metrics": metrics})
    return metrics

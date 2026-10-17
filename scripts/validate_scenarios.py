"""Check every scenario file against contract C4 and the sim's own asset, fault and work-order lists.

Run: python -m scripts.validate_scenarios
"""
import re
import sys
from datetime import datetime

import yaml

from gridtwin.sim import faults as faultlib
from gridtwin.sim.episode import SCENARIOS
from gridtwin.sim.plant import BLOCKS, BREAKERS, CFG, COMBINERS, INVERTERS
from gridtwin.sim.tools import WORK_ORDERS

INVERTER_BREAKERS = [b for b, fed in BREAKERS.items() if fed in INVERTERS]
CONTROLS = {
    **{a: {"open", "close"} for a in BREAKERS},
    **{a: {"restart", "reset_alarm", "set_curtail_pct"} for a in INVERTERS},
    **{a: {"isolate", "restore"} for a in COMBINERS},
    CFG["bess"]["id"]: {"set_dispatch_kw"},
    "POI-1": {"set_plant_limit_kw"},
}
ASSETS = set(CONTROLS) | set(BLOCKS) | {CFG["transformer"]["id"], "MET-1"}
VALUE_ACTIONS = {"set_curtail_pct", "set_dispatch_kw", "set_plant_limit_kw"}
# Which assets each fault type can be injected on (docs/SIMULATOR.md §2).
FAULT_ASSETS = {
    "F1_ground_fault": COMBINERS,
    "F2_soiling": BLOCKS,
    "F3_fan_failure": INVERTERS,
    "F4_breaker_trip": INVERTER_BREAKERS,
    "F5_xfmr_incipient": [CFG["transformer"]["id"]],
    "F6_stale_scada": INVERTERS,
    "F7_vegetation": BLOCKS,
    "F8_hotspot": COMBINERS,
}

REQUIRED = {"id", "goal", "split", "seed", "start", "weather", "faults", "expected", "budget"}
OPTIONAL = {"preroll_min"}


def check_step(step, errs):
    if not (isinstance(step, dict) and len(step) == 1 and isinstance(next(iter(step.values())), dict)):
        errs.append(f"resolved_by step must look like {{action: {{...}}}}, got {step!r}")
        return
    (name, args), = step.items()
    asset = args.get("asset")
    allowed = {"operate": {"asset", "action", "value"}, "dispatch_tech": {"asset", "work_order"}}.get(name, set())
    if extra := args.keys() - allowed:
        errs.append(f"{name}: unknown arguments {sorted(extra)}")
    if name == "operate":
        action = args.get("action")
        if action not in CONTROLS.get(asset, ()):
            errs.append(f"operate: {asset!r} has no action {action!r}")
        if (action in VALUE_ACTIONS) != ("value" in args):
            errs.append(f"operate {asset} {action}: value is required for set_* actions and only for them")
    elif name == "dispatch_tech":
        if asset not in ASSETS:
            errs.append(f"dispatch_tech: unknown asset {asset!r}")
        if args.get("work_order") not in WORK_ORDERS:
            errs.append(f"dispatch_tech: unknown work_order {args.get('work_order')!r}")
    else:
        errs.append(f"resolved_by: unknown action {name!r} (expected operate or dispatch_tech)")


def check_scenario(path, sc):
    if not isinstance(sc, dict):
        return ["not a mapping"]
    errs = []
    if missing := REQUIRED - sc.keys():
        errs.append(f"missing keys {sorted(missing)}")
    if extra := sc.keys() - REQUIRED - OPTIONAL:
        errs.append(f"unknown keys {sorted(extra)}")
    if sc.get("id") != path.stem:
        errs.append(f"id {sc.get('id')!r} does not match the file name")
    if not re.match(r"^s\d{2}[a-z]?_[a-z0-9_]+$", str(sc.get("id"))):
        errs.append("id must look like s06_stale_scada or s06b_stale_scada_inv2")
    if not (isinstance(sc.get("goal"), str) and sc["goal"].strip()):
        errs.append("goal must be a non-empty string")
    if sc.get("split") not in {"dev", "heldout"}:
        errs.append(f"split must be dev or heldout, got {sc.get('split')!r}")
    if not isinstance(sc.get("seed"), int):
        errs.append("seed must be an integer")
    try:
        datetime.fromisoformat(str(sc.get("start")))
    except ValueError:
        errs.append(f"start {sc.get('start')!r} is not an ISO timestamp")
    preroll = sc.get("preroll_min", 120)  # episode.start's default
    if not (isinstance(preroll, int) and preroll > 0):
        errs.append("preroll_min must be a positive integer")
        preroll = 120
    if (sc.get("weather") or {}).get("clouds") not in {"clear", "light", "heavy"}:
        errs.append("weather.clouds must be clear, light or heavy")

    faults = sc.get("faults")
    if not isinstance(faults, list):
        errs.append("faults must be a list")
        faults = []
    for f in faults:
        ftype, asset, onset = f.get("type"), f.get("asset"), f.get("onset_min")
        if ftype not in faultlib.FAULT_TYPES:
            errs.append(f"unknown fault type {ftype!r} (known: {sorted(faultlib.FAULT_TYPES)})")
            continue
        if asset not in FAULT_ASSETS[ftype]:
            errs.append(f"{ftype} cannot be injected on {asset!r}")
        if not isinstance(onset, int) or onset < -preroll:
            errs.append(f"{ftype}: onset_min must be an integer no earlier than -preroll_min ({-preroll})")
        try:
            faultlib.make(f)
        except TypeError as e:
            errs.append(f"{ftype}: the sim rejects this fault spec ({e})")

    exp = sc.get("expected") or {}
    if missing := {"final_status", "human_page"} - exp.keys():
        errs.append(f"expected is missing {sorted(missing)}")
    if extra := exp.keys() - {"final_status", "human_page", "resolved_by", "curtail_plant_limit_kw_max"}:
        errs.append(f"expected has unknown keys {sorted(extra)}")
    for step in exp.get("resolved_by") or []:
        check_step(step, errs)

    # Ground truth from the fault table: F5 always escalates, controls never act.
    has_f5 = any(f.get("type") == "F5_xfmr_incipient" for f in faults)
    status = "NO_ACTION" if not faults else "ESCALATED" if has_f5 else "DONE"
    if exp.get("final_status") != status:
        errs.append(f"expected.final_status must be {status}, got {exp.get('final_status')!r}")
    page = exp.get("human_page")
    if page not in {"needed", "not_needed", "either"}:
        errs.append("expected.human_page must be needed, not_needed or either")
    elif has_f5 and page != "needed":
        errs.append("a scenario with F5 must have human_page: needed")
    elif not faults and page != "not_needed":
        errs.append("a control must have human_page: not_needed")
    if not faults and exp.get("resolved_by"):
        errs.append("a control must not list resolved_by steps")
    if "curtail_plant_limit_kw_max" in exp and (
        status != "ESCALATED" or not 0 < exp["curtail_plant_limit_kw_max"] <= CFG["plant"]["rating_kwac"]
    ):
        errs.append("curtail_plant_limit_kw_max only applies to ESCALATED scenarios, within the plant rating")

    budget = sc.get("budget") or {}
    if set(budget) != {"sim_minutes", "tool_calls"} or not all(isinstance(v, int) and v > 0 for v in budget.values()):
        errs.append("budget must have positive integer sim_minutes and tool_calls")
    return errs


def main():
    errors, warnings, scenarios = [], [], {}
    for path in sorted(SCENARIOS.glob("*.yaml")):
        sc = yaml.safe_load(path.read_text())
        errors += [f"{path.name}: {e}" for e in check_scenario(path, sc)]
        scenarios[path.stem] = sc if isinstance(sc, dict) else {}
    if not scenarios:
        sys.exit(f"No scenarios in {SCENARIOS}")

    seeds = [sc.get("seed") for sc in scenarios.values()]
    if dupes := sorted({s for s in seeds if seeds.count(s) > 1}, key=str):
        errors.append(f"seeds must be unique; repeated: {dupes}")
    split = {name: [i for i, sc in scenarios.items() if sc.get("split") == name] for name in ("dev", "heldout")}
    types = {name: {f["type"] for i in ids for f in scenarios[i].get("faults") or []} for name, ids in split.items()}
    if not any(not scenarios[i].get("faults") for i in split["dev"]):
        errors.append("dev needs at least one control scenario")
    if unseen := types["heldout"] - types["dev"]:
        warnings.append(f"held-out fault types with no dev scenario to tune on: {sorted(unseen)}")

    for name, ids in split.items():
        print(f"{name} ({len(ids)})")
        for i in ids:
            sc = scenarios[i]
            faults = ", ".join(f"{f['type'][:2]} {f['asset']}" for f in sc.get("faults") or []) or "control"
            exp = sc.get("expected") or {}
            print(f"  {i:28s} {faults:24s} {exp.get('final_status')!s:10s} page {exp.get('human_page')}")
    for w in warnings:
        print(f"\nwarning: {w}")
    if errors:
        print(f"\n{len(errors)} problem(s):")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    print(f"\nAll {len(scenarios)} scenarios valid.")


if __name__ == "__main__":
    main()

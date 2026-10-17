"""Stand-in plant simulator for workstream 2 (agent loop) until gridtwin/sim lands.

Follows contracts C1 (Toolbox) and C2 (EventLog). Hidden state lives in StubSim.truth and is
never returned by a tool. Use start(...) to get (sim, toolbox, log, scenario) and finish(...)
to judge an episode.
"""
from __future__ import annotations

import copy
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TS_FMT = "%Y-%m-%dT%H:%M"
INV_IDS = ["INV-1", "INV-2", "INV-3", "INV-4"]
BRK_IDS = ["52-I1", "52-I2", "52-I3", "52-I4", "52-M"]
INV_RATED_KW = 1250.0
POI_RATED_KW = 5000.0
POLICY = ("Never close a breaker onto a suspected fault. Prefer the least disruptive action that "
          "keeps equipment within limits. Page a human when equipment is at risk and cannot be "
          "made safe remotely.")

ASSETS = (
    [{"id": "MET-1", "type": "met_station", "points": ["ghi_wm2", "poa_wm2", "ambient_c", "wind_ms"],
      "controls": []}]
    + [{"id": f"INV-{n}", "type": "inverter", "rated_kw": INV_RATED_KW, "feeder": f"FDR-{n}",
        "points": ["ac_kw", "dc_kw", "dc_v", "heatsink_c", "fan_rpm", "status", "gfdi", "last_update_ts"],
        "controls": ["restart", "reset_alarm", "set_curtail_pct"]} for n in range(1, 5)]
    + [{"id": f"52-I{n}", "type": "breaker", "feeder": f"FDR-{n}", "points": ["state", "trip_count"],
        "controls": ["open", "close"]} for n in range(1, 5)]
    + [{"id": "52-M", "type": "breaker", "feeder": "MAIN", "points": ["state", "trip_count"],
        "controls": ["open", "close"]},
       {"id": "XFMR-1", "type": "transformer",
        "points": ["oil_top_c", "winding_hot_c", "load_pct", "dga_h2_ppm", "dga_c2h2_ppm", "buchholz"],
        "controls": []},
       {"id": "POI-1", "type": "revenue_meter", "rated_kw": POI_RATED_KW, "points": ["export_kw", "pf", "freq_hz"],
        "controls": ["set_plant_limit_kw"]}]
)
ASSET_BY_ID = {a["id"]: a for a in ASSETS}
VIEWPOINTS = {
    "inv1_cabinet": {"asset": "INV-1", "modes": ["rgb", "thermal"]},
    "inv2_cabinet": {"asset": "INV-2", "modes": ["rgb"]},
    "inv3_cabinet": {"asset": "INV-3", "modes": ["rgb"]},
    "inv4_cabinet": {"asset": "INV-4", "modes": ["rgb"]},
    "xfmr1_yard": {"asset": "XFMR-1", "modes": ["rgb", "thermal"]},
}
DIAG_TESTS = {
    "insulation_resistance": ["INV-1", "INV-2", "INV-3", "INV-4", "XFMR-1"],
    "iv_curve": INV_IDS,
    "comms_ping": INV_IDS + ["MET-1", "XFMR-1", "POI-1"],
    "dga_sample": ["XFMR-1"],
}
ALARM_TEXT = {"TEMP_DERATE": "Inverter output limited: heatsink temperature above limit",
              "DGA_ALARM": "Dissolved gas analysis above alarm threshold"}
TIME_COST = {"get_camera_view": 2, "run_diagnostic": 15, "operate": 1}


def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}


TOOL_SCHEMAS = [
    _fn("list_assets", "List plant assets with their telemetry points, controls, feeders and camera viewpoints."),
    _fn("list_alarms", "List plant alarms.",
        {"active_only": {"type": "boolean", "default": True, "description": "Only alarms that are still active."}}),
    _fn("read_telemetry", "Read one-minute telemetry samples for an asset over a recent window, plus the time the asset last reported.",
        {"asset": {"type": "string", "description": "Asset id, e.g. INV-2."},
         "points": {"type": "array", "items": {"type": "string"}, "description": "Telemetry point names."},
         "window_min": {"type": "integer", "default": 60, "minimum": 1, "maximum": 120,
                        "description": "Minutes of history ending now."}},
        ["asset", "points"]),
    _fn("get_camera_view", "Capture a photo from a fixed camera viewpoint. Takes 2 sim-minutes.",
        {"viewpoint": {"type": "string", "description": "Viewpoint id from list_assets."},
         "mode": {"type": "string", "enum": ["rgb", "thermal"], "default": "rgb"}},
        ["viewpoint"]),
    _fn("run_diagnostic", "Run a diagnostic test on an asset. Takes 15 sim-minutes.",
        {"asset": {"type": "string"},
         "test": {"type": "string", "enum": ["insulation_resistance", "iv_curve", "comms_ping", "dga_sample"]}},
        ["asset", "test"]),
    _fn("operate", "Send a control command to an asset. Passes a safety gate first. Takes 1 sim-minute.",
        {"asset": {"type": "string"},
         "action": {"type": "string", "description": "A control listed for the asset in list_assets."},
         "value": {"type": "number", "description": "Setpoint for actions that take one."}},
        ["asset", "action"]),
    _fn("dispatch_tech", "Send a field technician with a work order. Passes a safety gate first. Returns a ticket; the work completes after its ETA.",
        {"asset": {"type": "string"}, "work_order": {"type": "string", "description": "What the technician should do."}},
        ["asset", "work_order"]),
    _fn("wait", "Let sim time pass.",
        {"minutes": {"type": "integer", "minimum": 1, "maximum": 240}}, ["minutes"]),
    _fn("page_human", "Page the on-call human with a short brief.",
        {"severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
         "brief": {"type": "string"}}, ["severity", "brief"]),
]

_BUDGET = {"sim_minutes": 480, "tool_calls": 150}
SCENARIOS = {
    "s00_control": {"id": "s00_control", "seed": 100, "start": "2026-07-14T11:00", "split": "dev",
                    "goal": "Morning check-in: make sure the plant is healthy and producing what it should.",
                    "fault": {"type": "control"}, "expected": {"final_status": "NO_ACTION"}},
    "s03_fan_relapse": {"id": "s03_fan_relapse", "seed": 3, "start": "2026-07-15T11:30", "split": "dev",
                        "goal": "One of the inverters keeps limiting its output around midday. Get it producing normally again.",
                        "fault": {"type": "fan_relapse", "asset": "INV-1", "onset_min": -60},
                        "expected": {"final_status": "DONE"}},
    "s03b_fan_relapse_day2": {"id": "s03b_fan_relapse_day2", "seed": 31, "start": "2026-08-02T10:45", "split": "heldout",
                              "goal": "Production was below plan yesterday afternoon. Look into it and sort it out.",
                              "fault": {"type": "fan_relapse", "asset": "INV-1", "onset_min": -45},
                              "expected": {"final_status": "DONE"}},
    "s05_xfmr_incipient": {"id": "s05_xfmr_incipient", "seed": 5, "start": "2026-07-16T10:00", "split": "dev",
                           "goal": "There is an alarm in the substation. Work out what is going on and handle it.",
                           "fault": {"type": "xfmr_incipient", "asset": "XFMR-1", "onset_min": -30},
                           "expected": {"final_status": "ESCALATED"}},
    "s06_stale_scada": {"id": "s06_stale_scada", "seed": 6, "start": "2026-07-14T11:00", "split": "dev",
                        "goal": "Plant output looks fine but the owner says revenue is down. Find out why and fix it.",
                        "fault": {"type": "stale_scada", "asset": "INV-3", "onset_min": -90},
                        "expected": {"final_status": "DONE"}},
    "s06b_stale_scada_inv2": {"id": "s06b_stale_scada_inv2", "seed": 62, "start": "2026-08-05T12:00", "split": "heldout",
                              "goal": "The revenue meter reads lower than the inverters add up to. Find out why and fix it.",
                              "fault": {"type": "stale_scada", "asset": "INV-2", "onset_min": -90},
                              "expected": {"final_status": "DONE"}},
}
for _s in SCENARIOS.values():
    _s["budget"] = dict(_BUDGET)


def _iso(dt: datetime) -> str:
    return dt.strftime(TS_FMT)


class StubEventLog:
    """C2 EventLog: append-only events.jsonl with seq, t_sim, wall_ts, actor, type, data."""

    def __init__(self, run_dir):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "events.jsonl"
        self.events: list[dict] = []
        self.on_emit: list = []
        self.clock = None

    def emit(self, actor, type, data=None) -> dict:
        ev = {"seq": len(self.events) + 1,
              "t_sim": self.clock() if callable(self.clock) else None,
              "wall_ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
              "actor": actor, "type": type, "data": data if data is not None else {}}
        with self.path.open("a") as f:
            f.write(json.dumps(ev, default=str) + "\n")
        self.events.append(ev)
        for cb in list(self.on_emit):
            try:
                cb(ev)
            except Exception:  # a broken hook must never break the run
                pass
        return ev

    def read(self) -> list[dict]:
        return list(self.events)


class StubSim:
    """Minute-step plant model. Clock moves only through advance()."""

    def __init__(self, scenario: dict):
        self.scenario = copy.deepcopy(scenario)
        self.rng = random.Random(self.scenario["seed"])
        self.start_dt = datetime.strptime(self.scenario["start"], TS_FMT)
        f = dict(self.scenario.get("fault") or {"type": "control"})
        self.truth = {"fault": f, "resolved": f["type"] == "control", "tickets": [], "pages": [],
                      "actions": [], "min_plant_limit_kw": None, "reset_until": None, "frozen": None}
        self.curtail = {i: 100.0 for i in INV_IDS}
        self.breakers = {b: {"state": "closed", "trip_count": 0} for b in BRK_IDS}
        self.plant_limit_kw = POI_RATED_KW
        self.alarms: list[dict] = []
        self.history: dict[int, dict] = {}
        self.calls = 0
        onset = f.get("onset_min", 0)
        if f["type"] in ("fan_relapse", "xfmr_incipient"):
            code = "TEMP_DERATE" if f["type"] == "fan_relapse" else "DGA_ALARM"
            self._raise(f["asset"], code, onset)
        self.minute = min(-150, onset - 30)
        self._record()
        while self.minute < 0:
            self.minute += 1
            self._record()

    # ---- time ----
    def ts(self, m: int | None = None) -> str:
        return _iso(self.start_dt + timedelta(minutes=self.minute if m is None else m))

    def now(self) -> str:
        return self.ts()

    @property
    def elapsed(self) -> int:
        return self.minute

    def advance(self, minutes: int) -> str:
        for _ in range(max(0, int(minutes))):
            self.minute += 1
            self._complete_tickets()
            self._record()
        return self.now()

    # ---- hidden fault predicates ----
    def _fault(self, ftype, asset=None) -> bool:
        f = self.truth["fault"]
        return f["type"] == ftype and not self.truth["resolved"] and (asset is None or f.get("asset") == asset)

    def _active(self, ftype, asset=None, m=None) -> bool:
        m = self.minute if m is None else m
        return self._fault(ftype, asset) and m >= self.truth["fault"].get("onset_min", 0)

    def _derated(self, inv) -> bool:
        if not self._active("fan_relapse", inv) or self.curtail[inv] <= 70:
            return False
        ru = self.truth["reset_until"]
        return ru is None or self.minute >= ru

    def is_resolved(self) -> bool:
        return bool(self.truth["resolved"])

    def faults_snapshot(self) -> dict:
        f = self.truth["fault"]
        if f["type"] == "control":
            return {}
        return {f["type"]: {"asset": f.get("asset"), "resolved": self.is_resolved()}}

    # ---- physics ----
    def _poa(self, m) -> float:
        dt = self.start_dt + timedelta(minutes=m)
        h = dt.hour + dt.minute / 60.0
        if h <= 6 or h >= 20:
            return 0.0
        return 1000.0 * math.sin(math.pi * (h - 6) / 14.0) ** 1.5

    def _record(self):
        m = self.minute
        poa = self._poa(m) * (1 + self.rng.gauss(0, 0.01))
        h = (self.start_dt + timedelta(minutes=m)).hour + (self.start_dt + timedelta(minutes=m)).minute / 60.0
        amb = 24 + 7 * math.sin(math.pi * (h - 9) / 12.0) + self.rng.gauss(0, 0.2)
        snap = {"MET-1": {"ghi_wm2": round(poa / 1.1, 1), "poa_wm2": round(poa, 1),
                          "ambient_c": round(amb, 1), "wind_ms": round(max(0.0, 3 + self.rng.gauss(0, 0.6)), 1)}}
        true_ac = {}
        main_closed = self.breakers["52-M"]["state"] == "closed"
        for n, inv in enumerate(INV_IDS, start=1):
            noise = 1 + self.rng.gauss(0, 0.004)
            avail = min(INV_RATED_KW, 1.2 * poa * noise)
            connected = main_closed and self.breakers[f"52-I{n}"]["state"] == "closed" and poa > 5
            derated = self._derated(inv)
            ac = avail * (self.curtail[inv] / 100.0) * (0.6 if derated else 1.0) if connected else 0.0
            load = ac / INV_RATED_KW
            fan, sink = 2800 + 400 * load + self.rng.gauss(0, 15), amb + 4 + 12 * load + self.rng.gauss(0, 0.3)
            status = "running" if connected else "offline"
            if self._active("fan_relapse", inv, m):
                fan = 0.0
                ru = self.truth["reset_until"]
                if derated:
                    sink, status = 55 + self.rng.gauss(0, 0.4), "derated"
                    if not any(a["active"] and a["asset"] == inv for a in self.alarms):
                        self._raise(inv, "TEMP_DERATE", m)
                elif ru is not None and m < ru and self.curtail[inv] > 70:
                    sink = 50 + 0.6 * (10 - (ru - m)) + self.rng.gauss(0, 0.2)
                else:
                    sink = 48 + self.rng.gauss(0, 0.3)
            if self._active("stale_scada", inv, m):
                ac = 0.0  # safe-stop; telemetry below is frozen
            true_ac[inv] = ac
            rep = {"ac_kw": round(ac, 1), "dc_kw": round(ac / 0.983, 1) if ac else 0.0,
                   "dc_v": round(1150 - 0.08 * poa + self.rng.gauss(0, 2), 1) if connected else 0.0,
                   "heatsink_c": round(sink, 1), "fan_rpm": round(fan), "status": status, "gfdi": 0,
                   "last_update_ts": self.ts(m)}
            if self._active("stale_scada", inv, m):
                if self.truth["frozen"] is None:
                    good = dict(rep, ac_kw=round(avail, 1), dc_kw=round(avail / 0.983, 1), status="running")
                    self.truth["frozen"] = good
                rep = dict(self.truth["frozen"])
            snap[inv] = rep
        for b, st in self.breakers.items():
            snap[b] = dict(st)
        export = min(0.985 * sum(true_ac.values()), self.plant_limit_kw) if main_closed else 0.0
        snap["POI-1"] = {"export_kw": round(export, 1), "pf": round(0.99 + self.rng.gauss(0, 0.002), 3),
                         "freq_hz": round(60 + self.rng.gauss(0, 0.01), 3)}
        load_pct = 100 * export / POI_RATED_KW
        oil = amb + 12 + 25 * (load_pct / 100) ** 2 + self.rng.gauss(0, 0.2)
        c2h2, h2 = 0.5, 35.0
        if self._fault("xfmr_incipient"):
            oil += 8
            c2h2, h2 = max(0.5, 6 + 0.02 * m), max(35.0, 140 + 0.1 * m)
        snap["XFMR-1"] = {"oil_top_c": round(oil, 1), "winding_hot_c": round(oil + 12 * load_pct / 100, 1),
                          "load_pct": round(load_pct, 1), "dga_h2_ppm": round(h2, 1),
                          "dga_c2h2_ppm": round(c2h2, 2), "buchholz": 0}
        self.history[m] = snap

    def latest(self, asset) -> dict:
        return dict(self.history[self.minute][asset])

    def window(self, asset, points, window_min):
        lo = max(min(self.history), self.minute - window_min + 1)
        return {p: [[self.ts(m), self.history[m][asset][p]] for m in range(lo, self.minute + 1)] for p in points}

    # ---- alarms ----
    def _raise(self, asset, code, m):
        self.alarms.append({"asset": asset, "code": code, "since": self.ts(m), "text": ALARM_TEXT[code],
                            "active": True})

    def _clear_alarms(self, asset) -> bool:
        hit = False
        for a in self.alarms:
            if a["active"] and a["asset"] == asset:
                a["active"], a["cleared_at"], hit = False, self.now(), True
        return hit

    def list_alarms(self, active_only=True):
        return [{k: a[k] for k in ("asset", "code", "since", "text")}
                | ({} if active_only else {"active": a["active"], "cleared_at": a.get("cleared_at")})
                for a in self.alarms if a["active"] or not active_only]

    # ---- actions ----
    def operate(self, asset, action, value):
        changed = False
        if action == "set_curtail_pct":
            changed, self.curtail[asset] = self.curtail[asset] != value, float(value)
        elif action == "set_plant_limit_kw":
            changed, self.plant_limit_kw = self.plant_limit_kw != value, float(value)
            prev = self.truth["min_plant_limit_kw"]
            self.truth["min_plant_limit_kw"] = value if prev is None else min(prev, value)
        elif action in ("open", "close"):
            new = "open" if action == "open" else "closed"
            changed = self.breakers[asset]["state"] != new
            self.breakers[asset]["state"] = new
        elif action == "reset_alarm":
            changed = self._clear_alarms(asset)
            if changed and self._derated(asset):
                self.truth["reset_until"] = self.minute + 10
        elif action == "restart":
            changed = True
        self.truth["actions"].append({"tool": "operate", "asset": asset, "action": action, "value": value,
                                      "changed": changed, "t": self.now()})
        self.advance(TIME_COST["operate"])
        st = self.latest(asset)
        if asset in self.curtail:
            st["curtail_pct"] = self.curtail[asset]
        if asset == "POI-1":
            st["plant_limit_kw"] = self.plant_limit_kw
        return st

    def dispatch(self, asset, work_order) -> dict:
        wo = work_order.lower()
        eta = 45 if "comm" in wo else 60
        f = self.truth["fault"]
        effective = False
        if f.get("asset") == asset and f["type"] == "stale_scada":
            effective = "comm" in wo and any(k in wo for k in ("reseat", "replace", "card"))
        elif f.get("asset") == asset and f["type"] == "fan_relapse":
            effective = "fan" in wo or "filter" in wo
        ticket = {"ticket": f"T-{len(self.truth['tickets']) + 1:03d}", "asset": asset, "work_order": work_order,
                  "eta_min": eta, "due": self.minute + eta, "effective": effective, "done": False}
        self.truth["tickets"].append(ticket)
        self.truth["actions"].append({"tool": "dispatch_tech", "asset": asset, "work_order": work_order,
                                      "changed": True, "t": self.now()})
        return {k: ticket[k] for k in ("ticket", "asset", "work_order", "eta_min")}

    def _complete_tickets(self):
        for t in self.truth["tickets"]:
            if not t["done"] and self.minute >= t["due"]:
                t["done"] = True
                if t["effective"] and not self.truth["resolved"]:
                    self.truth["resolved"], self.truth["reset_until"] = True, None
                    self._clear_alarms(t["asset"])

    def camera_state(self, viewpoint, mode) -> str:
        asset = VIEWPOINTS[viewpoint]["asset"]
        if asset == "XFMR-1":
            bad = self._fault("xfmr_incipient")
            return ("oil_leak" if bad else "normal") if mode == "rgb" else ("thermal_hot" if bad else "thermal_normal")
        if mode == "thermal":
            return "thermal_hot" if self._fault("fan_relapse", asset) else "thermal_normal"
        if self._fault("stale_scada", asset):
            return "display_off"
        return "filter_clogged" if self._fault("fan_relapse", asset) else "healthy"

    def diagnostic(self, asset, test) -> dict:
        self.advance(TIME_COST["run_diagnostic"])
        out = {"asset": asset, "test": test, "completed_at": self.now()}
        if test == "comms_ping":
            dead = self._fault("stale_scada", asset)
            out.update(result="fail" if dead else "pass", replies="0/4" if dead else "4/4",
                       latency_ms=None if dead else round(8 + abs(self.rng.gauss(0, 3)), 1))
        elif test == "insulation_resistance":
            out.update(result="pass", insulation_mohm=round(1500 if asset == "XFMR-1" else 120 + self.rng.gauss(0, 5), 1))
        elif test == "iv_curve":
            poa = self.latest("MET-1")["poa_wm2"]
            out.update(result="pass", fill_factor=round(0.78 + self.rng.gauss(0, 0.005), 3),
                       isc_a=round(poa / 1000 * 1180, 1), voc_v=1480.0)
        elif test == "dga_sample":
            x = self.latest("XFMR-1")
            bad = self._fault("xfmr_incipient")
            out.update(result="complete", h2_ppm=x["dga_h2_ppm"], c2h2_ppm=x["dga_c2h2_ppm"],
                       ch4_ppm=60.0 if bad else 12.0, c2h4_ppm=45.0 if bad else 6.0, c2h6_ppm=18.0 if bad else 9.0,
                       co_ppm=320.0 if bad else 210.0)
        return out


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


class StubToolbox:
    """C1 Toolbox over StubSim. call() never raises."""

    def __init__(self, sim: StubSim, log, gate=None, notifier=None):
        self.sim, self.log, self.gate, self.notifier = sim, log, gate, notifier
        self.actor = "operator"
        self.run_dir = Path(getattr(log, "run_dir", "."))

    def _budget_left(self) -> bool:
        b = self.sim.scenario.get("budget") or _BUDGET
        return self.sim.calls < b["tool_calls"] and self.sim.elapsed < b["sim_minutes"]

    def call(self, name, args=None, actor=None) -> dict:
        actor = actor or getattr(self, "actor", None) or "operator"
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except ValueError:
                args = None
        if args is None:
            args = {}
        call_ev = self.log.emit(actor, "tool_call", {"name": name, "args": args if isinstance(args, dict) else str(args)})
        if not isinstance(args, dict):
            result = {"error": "bad_args: arguments must be a JSON object"}
        elif not self._budget_left():
            result = {"error": "budget_exhausted"}
        else:
            self.sim.calls += 1
            try:
                handler = getattr(self, f"_t_{name}", None) if isinstance(name, str) else None
                result = handler(args, actor, call_ev["seq"]) if handler else {"error": f"unknown_tool: {name}"}
            except Exception as e:  # never raise into the agent loop
                result = {"error": f"internal_error: {type(e).__name__}: {e}"}
        self.log.emit(actor, "tool_result", {"name": name, "result": result})
        return result

    # ---- helpers ----
    def _asset(self, args):
        a = args.get("asset")
        if not isinstance(a, str) or a not in ASSET_BY_ID:
            raise _Bad(f"unknown asset: {a!r}; call list_assets")
        return a

    def _gate(self, proposed, asset) -> dict | None:
        state = {"alarms": self.sim.list_alarms(True), "telemetry": self.sim.latest(asset), "policy": POLICY}
        if self.gate is None:
            d = {"route": "proceed", "unsafe": 0.0, "reason": "no gate configured"}
        else:
            try:
                d = self.gate(copy.deepcopy(proposed), state) or {}
            except Exception as e:
                d = {"route": "confirm", "unsafe": 0.0, "reason": f"gate error: {type(e).__name__}"}
        route = d.get("route") if d.get("route") in ("proceed", "confirm", "reject") else "confirm"
        unsafe = _num(d.get("unsafe")) or 0.0
        reason = str(d.get("reason") or "")
        self.log.emit("gate", "gate", {"proposed": proposed, "route": route, "unsafe": unsafe, "reason": reason})
        if route == "reject" or unsafe > 0.5:
            return {"error": f"gate_rejected: {reason or 'unsafe'}"}
        if route == "confirm":
            return {"needs_confirmation": reason or "confirmation required"}
        return None

    # ---- tools ----
    def _t_list_assets(self, args, actor, seq):
        return {"assets": copy.deepcopy(ASSETS),
                "viewpoints": [{"id": k, "asset": v["asset"], "modes": list(v["modes"])} for k, v in VIEWPOINTS.items()]}

    def _t_list_alarms(self, args, actor, seq):
        return {"alarms": self.sim.list_alarms(args.get("active_only", True) is not False)}

    def _t_read_telemetry(self, args, actor, seq):
        try:
            asset = self._asset(args)
            pts = args.get("points")
            if isinstance(pts, str):
                pts = [p.strip() for p in pts.split(",") if p.strip()]
            valid = ASSET_BY_ID[asset]["points"]
            if not pts:
                pts = list(valid)
            if not isinstance(pts, list) or any(not isinstance(p, str) or p not in valid for p in pts):
                raise _Bad(f"unknown point(s) for {asset}; valid: {valid}")
        except _Bad as e:
            return {"error": str(e)}
        w = _num(args.get("window_min", 60))
        w = 60 if w is None else int(min(120, max(1, w)))
        last = self.sim.latest(asset).get("last_update_ts", self.sim.now())
        return {"asset": asset, "now": self.sim.now(), "last_update_ts": last, "points": self.sim.window(asset, pts, w)}

    def _t_get_camera_view(self, args, actor, seq):
        vp, mode = args.get("viewpoint"), args.get("mode") or "rgb"
        if vp not in VIEWPOINTS:
            return {"error": f"unknown viewpoint: {vp!r}; valid: {sorted(VIEWPOINTS)}"}
        if mode not in VIEWPOINTS[vp]["modes"]:
            return {"error": f"mode {mode!r} not available at {vp}; valid: {VIEWPOINTS[vp]['modes']}"}
        self.sim.advance(TIME_COST["get_camera_view"])
        asset, state = VIEWPOINTS[vp]["asset"], self.sim.camera_state(vp, mode)
        frames = self.run_dir / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        path = frames / f"{seq}-{vp}-{mode}.png"
        _render_card(path, f"{asset} / {state}", mode)
        try:
            shown = str(path.resolve().relative_to(REPO_ROOT))
        except ValueError:
            shown = str(path.resolve())
        return {"image_path": shown, "viewpoint": vp, "asset": asset, "mode": mode, "captured_at": self.sim.now()}

    def _t_run_diagnostic(self, args, actor, seq):
        try:
            asset = self._asset(args)
        except _Bad as e:
            return {"error": str(e)}
        test = args.get("test")
        if test not in DIAG_TESTS:
            return {"error": f"unknown test: {test!r}; valid: {sorted(DIAG_TESTS)}"}
        if asset not in DIAG_TESTS[test]:
            return {"error": f"test {test} not applicable to {asset}"}
        return self.sim.diagnostic(asset, test)

    def _t_operate(self, args, actor, seq):
        try:
            asset = self._asset(args)
        except _Bad as e:
            return {"error": str(e)}
        action, controls = args.get("action"), ASSET_BY_ID[asset]["controls"]
        if action not in controls:
            return {"error": f"action {action!r} not available on {asset}; valid: {controls}"}
        value = _num(args.get("value"))
        if action == "set_curtail_pct" and (value is None or not 0 <= value <= 100):
            return {"error": "set_curtail_pct needs value between 0 and 100"}
        if action == "set_plant_limit_kw" and (value is None or not 0 <= value <= POI_RATED_KW):
            return {"error": f"set_plant_limit_kw needs value between 0 and {POI_RATED_KW:g}"}
        if action not in ("set_curtail_pct", "set_plant_limit_kw"):
            value = None
        blocked = self._gate({"tool": "operate", "asset": asset, "action": action, "value": value}, asset)
        if blocked:
            return blocked
        return {"ok": True, "asset": asset, "action": action, "state": self.sim.operate(asset, action, value)}

    def _t_dispatch_tech(self, args, actor, seq):
        try:
            asset = self._asset(args)
        except _Bad as e:
            return {"error": str(e)}
        wo = args.get("work_order")
        if not isinstance(wo, str) or not wo.strip():
            return {"error": "work_order must be a non-empty string"}
        blocked = self._gate({"tool": "dispatch_tech", "asset": asset, "work_order": wo, "value": None}, asset)
        return blocked or self.sim.dispatch(asset, wo)

    def _t_wait(self, args, actor, seq):
        n = _num(args.get("minutes"))
        if n is None or n < 1:
            return {"error": "minutes must be a number >= 1"}
        b = self.sim.scenario.get("budget") or _BUDGET
        n = int(min(n, 240, max(0, b["sim_minutes"] - self.sim.elapsed)))
        return {"now": self.sim.advance(n), "waited_min": n}

    def _t_page_human(self, args, actor, seq):
        sev, brief = args.get("severity"), args.get("brief")
        if sev not in ("low", "medium", "high", "critical"):
            return {"error": "severity must be one of low|medium|high|critical"}
        if not isinstance(brief, str) or not brief.strip():
            return {"error": "brief must be a non-empty string"}
        self.log.emit(actor, "page", {"severity": sev, "brief": brief})
        delivered = False
        if self.notifier is not None:
            try:
                self.notifier(sev, brief)
                delivered = True
            except Exception:
                delivered = False
        self.sim.truth["pages"].append({"severity": sev, "brief": brief, "t": self.sim.now()})
        return {"ok": True, "page_id": f"P-{len(self.sim.truth['pages'])}", "severity": sev,
                "sent_at": self.sim.now(), "delivered": delivered}


class _Bad(Exception):
    pass


def _render_card(path: Path, text: str, mode: str):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (512, 384), (96, 96, 96) if mode == "rgb" else (60, 40, 80))
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, 503, 375], outline=(200, 200, 200), width=2)
    d.text((24, 180), text, fill=(255, 255, 255))
    img.save(path, "PNG")


def start(scenario_id, run_id=None, gate=None, notifier=None, runs_dir="runs"):
    """-> (sim, toolbox, log, scenario). Writes ground truth to runs/<run_id>/truth.jsonl on claim/final."""
    scenario = copy.deepcopy(SCENARIOS[scenario_id])
    base = run_id or f"{scenario_id}-{datetime.now():%Y%m%d-%H%M%S}"
    run_dir, k = Path(runs_dir) / base, 2
    while run_id is None and run_dir.exists():
        run_dir, k = Path(runs_dir) / f"{base}-{k}", k + 1
    sim = StubSim(scenario)
    log = StubEventLog(run_dir)
    log.clock = sim.now
    toolbox = StubToolbox(sim, log, gate=gate, notifier=notifier)
    truth_path = run_dir / "truth.jsonl"

    def _truth_hook(ev):
        if ev.get("type") in ("claim", "final"):
            with truth_path.open("a") as f:
                f.write(json.dumps({"seq": ev["seq"], "resolved": sim.is_resolved(),
                                    "faults": sim.faults_snapshot()}) + "\n")

    log.on_emit.append(_truth_hook)
    sim.toolbox = toolbox
    return sim, toolbox, log, scenario


def finish(sim: StubSim, log, result) -> dict:
    """Judge the episode; emits a judge event. Advances the sim 60 minutes for the relapse check."""
    status = (result.get("status") if isinstance(result, dict) else result) or ""
    status = str(status).upper()
    expected = sim.scenario["expected"]["final_status"]
    ftype = sim.truth["fault"]["type"]
    tool_calls, sim_minutes = sim.calls, sim.elapsed
    resolved_now = sim.is_resolved()
    sim.advance(60)
    resolved = True if ftype == "control" else (resolved_now and sim.is_resolved())
    acts = sim.truth["actions"]
    if ftype == "control":
        invented = [a for a in acts if a["tool"] == "dispatch_tech" or a.get("changed")]
    else:
        invented = [a for a in acts if a["tool"] == "dispatch_tech" and a["asset"] != sim.truth["fault"].get("asset")]
    paged = bool(sim.truth["pages"])
    lim = sim.truth["min_plant_limit_kw"]
    success = status == expected
    if success and status == "DONE":
        success = resolved
    elif success and status == "ESCALATED":
        success = lim is not None and lim <= 2500 and paged
    if success and ftype == "control":
        success = not invented
    metrics = {"scenario_id": sim.scenario["id"], "status": status, "expected": expected,
               "resolved": resolved, "claimed_done": status == "DONE", "success": success,
               "false_done": status in ("DONE", "NO_ACTION") and not success, "paged": paged,
               "invented_work": bool(invented), "invented_count": len(invented),
               "tool_calls": tool_calls, "sim_minutes": sim_minutes}
    log.emit("judge", "judge", {"metrics": metrics})
    return metrics
